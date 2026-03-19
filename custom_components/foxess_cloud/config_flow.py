"""Config flow for FoxESS Cloud."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FoxESSApiClient, FoxESSApiError, FoxESSAuthenticationError
from .const import CONF_DEVICE_SNS, DOMAIN

import logging

_LOGGER = logging.getLogger(__name__)


class FoxESSOpenAPIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FoxESS Cloud."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            session = async_get_clientsession(self.hass)
            api = FoxESSApiClient(session, api_key)

            try:
                devices = await api.async_list_devices()
                _LOGGER.debug("FoxESS config flow discovered %s devices", len(devices))
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    if self._async_current_entries():
                        return self.async_abort(reason="already_configured_account")

                    title = "FoxESS"
                    if len(devices) == 1:
                        title = devices[0].name or devices[0].station_name or devices[0].device_sn
                    else:
                        site_name = devices[0].station_name or devices[0].name
                        if site_name:
                            title = f"{site_name} ({len(devices)} devices)"
                        else:
                            title = f"FoxESS ({len(devices)} devices)"

                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_API_KEY: api_key,
                            CONF_DEVICE_SNS: [device.device_sn for device in devices],
                        },
                    )
            except FoxESSAuthenticationError:
                _LOGGER.warning("FoxESS config flow authentication failed")
                errors["base"] = "invalid_auth"
            except FoxESSApiError as err:
                _LOGGER.warning(
                    "FoxESS config flow request failed errno=%s msg=%s",
                    err.errno,
                    err,
                )
                if err.errno in {40256, 40257}:
                    errors["base"] = "invalid_request"
                else:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )
