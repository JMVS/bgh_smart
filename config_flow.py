"""Config flow for BGH Smart Control integration."""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_HOST): cv.string,
    }
)


def _validate_ip_address(host: str) -> None:
    """Validate IP address format.
    
    Raises:
        ValueError: If IP format is invalid or IP is reserved/loopback/multicast
    """
    try:
        ip = ipaddress.ip_address(host)
        
        # Additional checks for IPv4
        if isinstance(ip, ipaddress.IPv4Address):
            if ip.is_reserved:
                raise ValueError(f"IP address {host} is reserved")
            if ip.is_loopback:
                raise ValueError(f"IP address {host} is loopback (127.x.x.x)")
            if ip.is_multicast:
                raise ValueError(f"IP address {host} is multicast")
        
    except ValueError as err:
        _LOGGER.error("Invalid IP address: %s - %s", host, err)
        raise


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.
    
    Args:
        hass: Home Assistant instance
        data: User input from config flow
        
    Returns:
        Dict with title for config entry
        
    Raises:
        ValueError: If IP validation fails
        CannotConnect: If port binding fails
    """
    # Validate IP format before any network operations
    _validate_ip_address(data[CONF_HOST])
    
    import socket
    try:
        # Test if we can bind to the broadcast receive port
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test_sock.settimeout(2)
        test_sock.bind(("", 20911))
        test_sock.close()
        _LOGGER.info("Successfully validated broadcast port 20911")
    except socket.timeout:
        _LOGGER.error("Timeout binding to port 20911")
        raise CannotConnect("Port binding timeout") from None
    except Exception as err:
        _LOGGER.error("Cannot bind to port 20911: %s", err)
        raise CannotConnect from err

    return {"title": data[CONF_NAME]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BGH Smart Control."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                # Validate IP format first
                _validate_ip_address(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_ip"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()

                try:
                    info = await validate_input(self.hass, user_input)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""
