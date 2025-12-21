"""BGH Smart AC UDP client - Alternative version with listener."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any

from .const import (
    CMD_CONTROL,
    CMD_STATUS,
    MODES,
    UDP_RECV_PORT,
    UDP_SEND_PORT,
)

_LOGGER = logging.getLogger(__name__)


class BGHClientAlt:
    """BGH Smart AC UDP client - Alternative with port 20911 listener."""

    def __init__(self, host: str) -> None:
        """Initialize the client."""
        self.host = host
        self._send_sock: socket.socket | None = None
        self._recv_sock: socket.socket | None = None
        self._current_mode = 0
        self._current_fan = 1

    async def async_connect(self) -> bool:
        """Connect to the AC unit."""
        try:
            loop = asyncio.get_event_loop()
            
            # Create send socket
            self._send_sock = await loop.run_in_executor(
                None, self._create_send_socket
            )
            
            # Create receive socket
            self._recv_sock = await loop.run_in_executor(
                None, self._create_recv_socket
            )
            
            _LOGGER.info("Sockets created for %s", self.host)
            return True
        except Exception as err:
            _LOGGER.error("Failed to connect to %s: %s", self.host, err)
            return False

    def _create_send_socket(self) -> socket.socket:
        """Create UDP send socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5)
        _LOGGER.info("Send socket created")
        return sock

    def _create_recv_socket(self) -> socket.socket:
        """Create UDP receive socket bound to port 20911."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5)
        
        try:
            # Bind to receive port 20911
            sock.bind(("", UDP_RECV_PORT))
            _LOGGER.info("Receive socket bound to port %d", UDP_RECV_PORT)
        except OSError as e:
            _LOGGER.error("Could not bind to port %d: %s", UDP_RECV_PORT, e)
            raise
            
        return sock

    async def async_get_status(self) -> dict[str, Any] | None:
        """Get current status from AC unit."""
        try:
            _LOGGER.info("Getting status from %s", self.host)
            # Send status query
            command = bytes.fromhex(CMD_STATUS)
            _LOGGER.info("Sending command: %s to %s:%d", command.hex(), self.host, UDP_SEND_PORT)
            await self._send_command(command)

            # Wait for response on port 20911
            _LOGGER.info("Waiting for response from %s on port %d...", self.host, UDP_RECV_PORT)
            data = await self._receive_response()
            if data:
                _LOGGER.info("Received %d bytes from %s: %s", len(data), self.host, data.hex()[:40])
                return self._parse_status(data)
            else:
                _LOGGER.warning("No response received from %s", self.host)
        except Exception as err:
            _LOGGER.error("Failed to get status from %s: %s", self.host, err)
            import traceback
            _LOGGER.error("Traceback: %s", traceback.format_exc())
        
        return None

    async def async_set_mode(
        self,
        mode: int,
        fan_speed: int | None = None,
    ) -> bool:
        """Set AC mode and fan speed."""
        try:
            # Update current state
            self._current_mode = mode
            if fan_speed is not None:
                self._current_fan = fan_speed

            # Build control command
            command = bytes.fromhex(CMD_CONTROL)
            command = bytearray(command)
            command[17] = self._current_mode
            command[18] = self._current_fan

            _LOGGER.info("Setting mode=%d, fan=%d on %s", mode, self._current_fan, self.host)
            await self._send_command(bytes(command))
            
            # Wait a bit for the command to take effect
            await asyncio.sleep(0.5)
            
            return True
        except Exception as err:
            _LOGGER.error("Failed to set mode on %s: %s", self.host, err)
            return False

    async def _send_command(self, command: bytes) -> None:
        """Send UDP command."""
        if not self._send_sock:
            raise RuntimeError("Send socket not connected")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._send_sock.sendto,
            command,
            (self.host, UDP_SEND_PORT),
        )
        _LOGGER.debug("Sent %d bytes to %s:%d", len(command), self.host, UDP_SEND_PORT)

    async def _receive_response(self, timeout: float = 3) -> bytes | None:
        """Receive UDP response on port 20911."""
        if not self._recv_sock:
            raise RuntimeError("Receive socket not connected")

        try:
            loop = asyncio.get_event_loop()
            data, addr = await asyncio.wait_for(
                loop.run_in_executor(None, self._recv_sock.recvfrom, 1024),
                timeout=timeout,
            )
            _LOGGER.debug("Received %d bytes from %s", len(data), addr)
            return data
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for response from %s (no data on port %d)", 
                          self.host, UDP_RECV_PORT)
            return None
        except Exception as e:
            _LOGGER.error("Error receiving: %s", e)
            return None

    def _parse_status(self, data: bytes) -> dict[str, Any]:
        """Parse status response."""
        if len(data) < 25:
            _LOGGER.warning("Invalid status data length: %d bytes (need 25+)", len(data))
            _LOGGER.warning("Data received: %s", data.hex())
            return {}

        # Extract data according to Node-RED flow
        mode = data[18]
        fan_speed = data[19]
        
        # Temperature is in bytes 21-22 (little-endian, divided by 100)
        temp_raw = struct.unpack("<H", data[21:23])[0]
        current_temp = temp_raw / 100.0
        
        # Setpoint is in bytes 23-24
        setpoint_raw = struct.unpack("<H", data[23:25])[0]
        target_temp = setpoint_raw / 100.0

        status = {
            "mode": MODES.get(mode, "unknown"),
            "mode_raw": mode,
            "fan_speed": fan_speed,
            "current_temperature": current_temp,
            "target_temperature": target_temp,
            "is_on": mode != 0,
        }

        # Update internal state
        self._current_mode = mode
        self._current_fan = fan_speed

        _LOGGER.info("Parsed status from %s: mode=%s(%d), fan=%d, temp=%.1f, target=%.1f", 
                    self.host, status["mode"], mode, fan_speed, current_temp, target_temp)
        return status

    async def async_close(self) -> None:
        """Close the connection."""
        if self._send_sock:
            self._send_sock.close()
            self._send_sock = None
        if self._recv_sock:
            self._recv_sock.close()
            self._recv_sock = None
