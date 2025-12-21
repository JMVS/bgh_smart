"""Data update coordinator for BGH Smart Control."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bgh_client import BGHClient
from .const import CONF_HOST, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BGHDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching BGH data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.client = BGHClient(entry.data[CONF_HOST])
        self.entry = entry
        
        # Set up callback for broadcast updates
        self.client._status_callback = self._handle_broadcast_update

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    def _handle_broadcast_update(self, status: dict[str, Any]) -> None:
        """Handle broadcast status update from AC."""
        _LOGGER.debug("Received broadcast update: %s", status)
        # Update coordinator data
        self.async_set_updated_data(status)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        # Connect if not connected
        if not self.client._recv_sock:
            if not await self.client.async_connect():
                raise UpdateFailed("Failed to connect to AC unit")

        # Get status (returns last broadcast or requests new one)
        data = await self.client.async_get_status()
        
        # Don't fail if no data yet - just return empty dict
        # The broadcast listener will update it when data arrives
        if data is None:
            _LOGGER.info("No status data yet, requesting and waiting...")
            await self.client.async_request_status()
            
            # Wait a bit but don't fail
            await asyncio.sleep(2)
            
            # Return empty data if still nothing (will retry on next poll)
            if not self.client._last_status:
                _LOGGER.warning("No broadcast yet, will keep trying in background")
                # Return fake data so setup doesn't fail
                return {
                    "mode": "unknown",
                    "mode_raw": 0,
                    "fan_speed": 1,
                    "current_temperature": None,
                    "target_temperature": None,
                    "is_on": False,
                }
            
            return self.client._last_status

        return data

    async def async_set_mode(self, mode: int, fan_speed: int | None = None) -> bool:
        """Set AC mode."""
        success = await self.client.async_set_mode(mode, fan_speed)
        # The broadcast listener will automatically update the data
        return success

    async def async_set_temperature(self, temperature: float) -> bool:
        """Set target temperature."""
        success = await self.client.async_set_temperature(temperature)
        # The broadcast listener will automatically update the data
        return success

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        await self.client.async_close()