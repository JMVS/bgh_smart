"""BGH Smart AC UDP client."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
import time
from typing import Any, Callable

from .const import (
    BROADCAST_RATE_LIMIT,
    MAX_PACKET_SIZE,
    MODES,
    UDP_RECV_PORT,
    UDP_SEND_PORT,
)

_LOGGER = logging.getLogger(__name__)

# Maximum packet size to prevent resource exhaustion
MAX_PACKET_SIZE = 100


class ClientError(Exception):
    """Base exception for BGH client errors."""


class ValidationError(ClientError):
    """Data validation failed."""


class NetworkError(ClientError):
    """Network operation failed."""


class TokenBucket:
    """Token bucket rate limiter for broadcast processing.
    
    Implements a simple token bucket algorithm to prevent resource exhaustion
    from broadcast floods.
    """
    
    def __init__(self, rate: float, capacity: float) -> None:
        """Initialize token bucket.
        
        Args:
            rate: Tokens added per second (e.g., 10.0 = 10 packets/sec)
            capacity: Maximum token capacity (burst allowance)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
    
    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume tokens.
        
        Args:
            tokens: Number of tokens to consume
            
        Returns:
            True if tokens available and consumed, False if rate limited
        """
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


def _validate_temperature_ranges(current_temp: float, target_temp: float) -> None:
    """Validate temperature values are within acceptable ranges.
    
    Args:
        current_temp: Ambient temperature in Celsius
        target_temp: Target temperature in Celsius
    
    Raises:
        ValidationError: If temperatures are out of valid range
    """
    if not (0 <= current_temp <= 50):
        raise ValidationError(f"Current temperature {current_temp}°C out of range 0-50")
    
    if not (16 <= target_temp <= 30):
        raise ValidationError(f"Target temperature {target_temp}°C out of range 16-30")


def parse_status_packet(data: bytes) -> dict[str, Any]:
    """Parse BGH status broadcast packet.
    
    Args:
        data: 29-byte status packet
    
    Returns:
        Dictionary with parsed status
        
    Raises:
        ValidationError: If packet is malformed or contains invalid data
    """
    if len(data) < 25:
        raise ValidationError(f"Invalid status data length: {len(data)}")

    mode = data[18]
    fan_speed = data[19]
    
    # Unpack both temperatures in one call
    temps = struct.unpack("<HH", data[21:25])
    current_temp = temps[0] / 100.0
    target_temp = temps[1] / 100.0

    _validate_temperature_ranges(current_temp, target_temp)

    return {
        "mode": MODES.get(mode, "unknown"),
        "mode_raw": mode,
        "fan_speed": fan_speed,
        "current_temperature": current_temp,
        "target_temperature": target_temp,
        "is_on": mode != 0,
    }


class BGHClient:
    """BGH Smart AC UDP client - Broadcast listener with rate limiting."""

    def __init__(
        self,
        host: str,
        rate_limiter: TokenBucket | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the client.
        
        Args:
            host: IP address of AC unit (must be valid IPv4)
            rate_limiter: Optional custom rate limiter (for testing)
            logger: Optional injected logger (for testing/observability)
        """
        self._logger = logger or _LOGGER
        
        # Validate IP at construction time
        try:
            ip = ipaddress.ip_address(host)
            if not isinstance(ip, ipaddress.IPv4Address):
                raise ValueError("Only IPv4 addresses supported")
            if ip.is_reserved or ip.is_loopback or ip.is_multicast:
                raise ValueError("IP address is reserved/loopback/multicast")
        except ValueError as err:
            self._logger.error("Invalid host IP: %s", err)
            raise
        
        self.host = host
        self._send_sock: socket.socket | None = None
        self._recv_sock: socket.socket | None = None
        self._listener_task: asyncio.Task | None = None
        self._current_mode = 0
        self._current_fan = 1
        self._last_status: dict[str, Any] = {}
        self._status_callback: Callable[[dict], None] | None = None
        self._device_id: str | None = None
        self._rate_limiter = rate_limiter or TokenBucket(
            rate=BROADCAST_RATE_LIMIT,
            capacity=BROADCAST_RATE_LIMIT
        )
        self._error_count = 0
        self._max_errors = 10

    async def async_connect(self) -> bool:
        """Connect to the AC unit and start listening for broadcasts."""
        try:
            self._logger.info("BGH Client connecting to %s", self.host)
            
            try:
                self._recv_sock = self._create_recv_socket()
                self._logger.info("Broadcast receive socket created")
            except Exception as e:
                self._logger.error("Failed to create receive socket: %s", e)
                return False
            
            try:
                self._send_sock = self._create_send_socket()
                self._logger.info("Send socket created (reusable)")
            except Exception as e:
                self._logger.error("Failed to create send socket: %s", e)
                if self._recv_sock:
                    self._recv_sock.close()
                    self._recv_sock = None
                return False
            
            self._logger.info("Starting broadcast listener task...")
            self._listener_task = asyncio.create_task(self._broadcast_listener())
            self._logger.info("BGH Client connected for %s", self.host)
            
            self._logger.info("Sending initial status request...")
            await self.async_request_status()
            
            return True
        except Exception as err:
            self._logger.error("Failed to connect to %s: %s", self.host, err)
            return False

    def _create_send_socket(self) -> socket.socket:
        """Create UDP send socket with timeout (reused for all commands)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(2.0)
        self._logger.debug("Send socket created with 2s timeout")
        return sock

    def _create_recv_socket(self) -> socket.socket:
        """Create UDP broadcast receive socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        sock.bind(("", UDP_RECV_PORT))
        sock.setblocking(False)
        self._logger.debug("Broadcast receive socket bound to port %d", UDP_RECV_PORT)
        return sock

    def _is_valid_status_packet(self, data: bytes) -> bool:
        """Validate if packet is a valid status broadcast.
        
        Args:
            data: Raw packet data
            
        Returns:
            True if valid 29-byte status packet
        """
        # Security: Reject oversized packets
        if len(data) > MAX_PACKET_SIZE:
            self._logger.warning("Rejected oversized packet: %d bytes", len(data))
            return False
        
        if len(data) != 29:
            return False
        
        if data[0] != 0x00:
            return False
        
        if data[7:13] != b'\xff\xff\xff\xff\xff\xff':
            return False
        
        if data[14] not in (0x00, 0x01):
            return False
        
        return True

    async def _broadcast_listener(self) -> None:
        """Listen for UDP broadcasts from the AC unit with rate limiting."""
        self._logger.info("Broadcast listener started for %s", self.host)
        self._logger.debug("Listening on port %d for broadcasts from %s", UDP_RECV_PORT, self.host)
        
        broadcast_timeout = 0
        
        while True:
            try:
                if not self._recv_sock:
                    self._logger.warning("Receive socket is None, stopping listener")
                    break
                    
                loop = asyncio.get_event_loop()
                
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(self._recv_sock, 1024),
                        timeout=30.0
                    )
                    
                    # Reset error counter on successful receive
                    self._error_count = 0
                    broadcast_timeout = 0
                    
                    if addr[0] != self.host:
                        continue
                    
                    # Rate limiting - prevent broadcast floods
                    if not self._rate_limiter.consume():
                        self._logger.warning("Broadcast rate limit exceeded from %s", self.host)
                        continue
                    
                    self._logger.debug("Received UDP packet from %s: %d bytes", addr, len(data))
                    
                    # Filter packet types
                    if len(data) == 22:
                        self._logger.debug("Ignoring ACK packet (22 bytes)")
                        continue
                    elif len(data) == 108:
                        self._logger.debug("Ignoring discovery packet (108 bytes)")
                        continue
                    elif len(data) in (46, 47):
                        self._logger.debug("Ignoring control response packet (%d bytes)", len(data))
                        continue
                    elif len(data) != 29:
                        self._logger.debug("Ignoring unknown packet (%d bytes)", len(data))
                        continue
                    
                    if not self._is_valid_status_packet(data):
                        self._logger.warning("Invalid packet structure (29 bytes but wrong format)")
                        continue
                    
                    self._logger.info("Valid status broadcast from %s: 29 bytes", addr)
                    
                    # Extract device ID on first valid packet
                    if not self._device_id:
                        new_device_id = data[1:7].hex()
                        self._device_id = new_device_id
                        self._logger.info("Device ID extracted: %s", self._device_id)
                    else:
                        # Security: Log if device ID changes (potential spoofing)
                        packet_device_id = data[1:7].hex()
                        if packet_device_id != self._device_id:
                            self._logger.warning(
                                "Device ID mismatch: expected=%s, got=%s (possible spoofing)",
                                self._device_id,
                                packet_device_id
                            )
                            continue
                    
                    try:
                        status = parse_status_packet(data)
                        self._last_status = status
                        self._current_mode = status.get("mode_raw", 0)
                        self._current_fan = status.get("fan_speed", 1)
                        self._logger.info("Parsed: mode=%s, fan=%s, temp=%.1f°C, target=%.1f°C", 
                                       status.get('mode'), 
                                       status.get('fan_speed'), 
                                       status.get('current_temperature', 0),
                                       status.get('target_temperature', 0))
                        if self._status_callback:
                            self._status_callback(status)
                    except ValidationError as err:
                        self._logger.warning("Failed to parse status packet: %s", err)
                        continue
                        
                except asyncio.TimeoutError:
                    broadcast_timeout += 1
                    
                    if broadcast_timeout == 1:
                        self._logger.warning("No broadcasts received from %s", self.host)
                        self._logger.info("Switching to polling mode...")
                    
                    self._logger.debug("Polling: Requesting status from %s", self.host)
                    await self.async_request_status()
                    await asyncio.sleep(2)
                            
            except asyncio.CancelledError:
                self._logger.info("Broadcast listener stopped for %s", self.host)
                break
            except Exception as err:
                self._error_count += 1
                self._logger.error(
                    "Error in broadcast listener (%d/%d): %s",
                    self._error_count,
                    self._max_errors,
                    err
                )
                
                # Fail-fast after max errors to prevent resource leak
                if self._error_count >= self._max_errors:
                    self._logger.error(
                        "Max error count reached (%d), stopping listener",
                        self._max_errors
                    )
                    break
                
                await asyncio.sleep(1)

    async def async_request_status(self) -> None:
        """Request status update (triggers a broadcast from the AC)."""
        try:
            CMD_STATUS = "00000000000000accf23aa3190590001e4"
            command = bytes.fromhex(CMD_STATUS)
            await self._send_command(command)
            self._logger.debug("Status request sent to %s", self.host)
        except Exception as err:
            self._logger.error("Failed to request status: %s", err)

    async def async_get_status(self) -> dict[str, Any] | None:
        """Get current status (returns last received broadcast)."""
        if not self._last_status:
            await self.async_request_status()
            await asyncio.sleep(1)
        
        return self._last_status if self._last_status else None

    async def async_set_mode(
        self,
        mode: int,
        fan_speed: int | None = None,
    ) -> bool:
        """Set AC mode and fan speed."""
        try:
            if not self._device_id:
                self._logger.warning("Device ID not yet extracted, waiting for broadcast...")
                await asyncio.sleep(2)
                if not self._device_id:
                    self._logger.error("Cannot send command without Device ID")
                    return False
            
            self._current_mode = mode
            if fan_speed is not None:
                self._current_fan = fan_speed

            cmd_base = f"00000000000000{self._device_id}f60001610402000080"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan

            self._logger.info("Sending mode command: mode=%d, fan=%d", self._current_mode, self._current_fan)
            await self._send_command(bytes(command))
            
            await asyncio.sleep(0.5)
            await self.async_request_status()
            
            return True
        except Exception as err:
            self._logger.error("Failed to set mode on %s: %s", self.host, err)
            return False

    async def async_set_temperature(self, temperature: float) -> bool:
        """Set target temperature."""
        try:
            if not self._device_id:
                self._logger.warning("Device ID not yet extracted, waiting for broadcast...")
                await asyncio.sleep(2)
                if not self._device_id:
                    self._logger.error("Cannot send command without Device ID")
                    return False

            cmd_base = f"00000000000000{self._device_id}810001610100000000"
            command = bytearray(bytes.fromhex(cmd_base))
            command[17] = self._current_mode
            command[18] = self._current_fan
            
            temp_raw = int(temperature * 100)
            command[20] = temp_raw & 0xFF
            command[21] = (temp_raw >> 8) & 0xFF

            self._logger.info("Sending temperature command: temp=%.1f°C", temperature)
            await self._send_command(bytes(command))
            
            await asyncio.sleep(0.5)
            await self.async_request_status()
            
            return True
        except Exception as err:
            self._logger.error("Failed to set temperature on %s: %s", self.host, err)
            return False

    async def _send_command(self, command: bytes) -> None:
        """Send UDP command using reusable socket.
        
        Args:
            command: Command bytes to send
            
        Raises:
            NetworkError: If send fails
        """
        if not self._send_sock:
            raise NetworkError("Send socket not initialized")
        
        self._logger.debug("Sending %d bytes to %s:%d", len(command), self.host, UDP_SEND_PORT)
        
        try:
            self._send_sock.sendto(command, (self.host, UDP_SEND_PORT))
            self._logger.debug("Sent command successfully")
        except socket.timeout:
            self._logger.error("Timeout sending command to %s", self.host)
            raise NetworkError(f"Timeout sending to {self.host}") from None
        except OSError as err:
            self._logger.error("Socket error sending command: %s", err)
            raise NetworkError(f"Socket error: {err}") from err

    async def async_close(self) -> None:
        """Close the connection and cleanup resources."""
        self._logger.info("Closing BGH client for %s", self.host)
        
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
            
        if self._send_sock:
            self._send_sock.close()
            self._send_sock = None
            self._logger.debug("Send socket closed")
            
        if self._recv_sock:
            self._recv_sock.close()
            self._recv_sock = None
            self._logger.debug("Receive socket closed")
