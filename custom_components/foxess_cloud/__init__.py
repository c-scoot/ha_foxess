"""FoxESS Cloud integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FoxESSApiClient, FoxESSApiError, FoxESSAuthenticationError
from .const import (
    CONF_DEVICE_SNS,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_CHARGE_PERIODS,
    SERVICE_SET_DEVICE_SETTING,
    SERVICE_SET_MIN_SOC,
)
from .coordinator import FoxESSDataUpdateCoordinator

FoxESSRuntimeData = dict[str, Any]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FoxESS from a config entry."""
    session = async_get_clientsession(hass)
    api = FoxESSApiClient(session, entry.data[CONF_API_KEY])
    try:
        devices = await api.async_list_devices()
    except FoxESSAuthenticationError as err:
        _LOGGER.warning("FoxESS setup authentication failed: %s", err)
        raise ConfigEntryAuthFailed(str(err)) from err
    except FoxESSApiError as err:
        _LOGGER.warning("FoxESS setup device discovery failed errno=%s msg=%s", err.errno, err)
        raise ConfigEntryNotReady(str(err)) from err

    selected_sns = set(entry.data.get(CONF_DEVICE_SNS, []))
    if selected_sns:
        devices = [device for device in devices if device.device_sn in selected_sns]
    if not devices:
        raise ConfigEntryNotReady("No FoxESS devices were returned for this account")

    coordinators: dict[str, FoxESSDataUpdateCoordinator] = {}
    for device in devices:
        coordinator = FoxESSDataUpdateCoordinator(hass, entry, api, device)
        await coordinator.async_config_entry_first_refresh()
        coordinators[device.device_sn] = coordinator

    runtime_data: FoxESSRuntimeData = {
        "api": api,
        "coordinators": coordinators,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime_data

    if not hass.services.has_service(DOMAIN, SERVICE_SET_MIN_SOC):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_MIN_SOC)
            hass.services.async_remove(DOMAIN, SERVICE_SET_CHARGE_PERIODS)
            hass.services.async_remove(DOMAIN, SERVICE_SET_DEVICE_SETTING)
    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register writable FoxESS services."""

    async def async_handle_set_min_soc(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data["device_sn"])
        await coordinator.async_set_battery_soc_settings(
            min_soc=call.data.get("min_soc"),
            min_soc_on_grid=call.data.get("min_soc_on_grid"),
        )

    async def async_handle_set_charge_periods(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data["device_sn"])
        if any(key in call.data for key in ("enable1", "start_time1", "end_time1")):
            await coordinator.async_set_charge_period(
                1,
                enabled=call.data.get("enable1"),
                start=call.data.get("start_time1"),
                end=call.data.get("end_time1"),
            )
        if any(key in call.data for key in ("enable2", "start_time2", "end_time2")):
            await coordinator.async_set_charge_period(
                2,
                enabled=call.data.get("enable2"),
                start=call.data.get("start_time2"),
                end=call.data.get("end_time2"),
            )

    async def async_handle_set_device_setting(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass, call.data["device_sn"])
        runtime_data = hass.data[DOMAIN][coordinator.config_entry.entry_id]
        api: FoxESSApiClient = runtime_data["api"]
        await api.async_set_device_setting(
            coordinator.device_sn,
            call.data["key"],
            call.data["value"],
        )
        await coordinator._async_refresh_control_settings()  # noqa: SLF001
        coordinator.async_update_listeners()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MIN_SOC,
        async_handle_set_min_soc,
        schema=vol.Schema(
            {
                vol.Required("device_sn"): cv.string,
                vol.Optional("min_soc"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional("min_soc_on_grid"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGE_PERIODS,
        async_handle_set_charge_periods,
        schema=vol.Schema(
            {
                vol.Required("device_sn"): cv.string,
                vol.Optional("enable1"): cv.boolean,
                vol.Optional("start_time1"): cv.time,
                vol.Optional("end_time1"): cv.time,
                vol.Optional("enable2"): cv.boolean,
                vol.Optional("start_time2"): cv.time,
                vol.Optional("end_time2"): cv.time,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DEVICE_SETTING,
        async_handle_set_device_setting,
        schema=vol.Schema(
            {
                vol.Required("device_sn"): cv.string,
                vol.Required("key"): cv.string,
                vol.Required("value"): object,
            }
        ),
    )


def _find_coordinator(
    hass: HomeAssistant,
    device_sn: str,
) -> FoxESSDataUpdateCoordinator:
    """Locate a coordinator by device serial number."""
    for runtime_data in hass.data.get(DOMAIN, {}).values():
        coordinators: Mapping[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]
        coordinator = coordinators.get(device_sn)
        if coordinator is not None:
            return coordinator
    raise vol.Invalid(f"Unknown FoxESS device serial number: {device_sn}")
