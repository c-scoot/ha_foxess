"""Data update coordinator for FoxESS Cloud."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FoxESSApiClient,
    FoxESSApiError,
    FoxESSAuthenticationError,
    FoxESSBatterySocSettings,
    FoxESSChargePeriod,
    FoxESSChargeTimeSettings,
    FoxESSDevice,
    FoxESSRateLimitError,
)
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH_INTERVAL,
    REPORT_REFRESH_INTERVAL,
    SETTINGS_REFRESH_INTERVAL,
    WORK_MODE_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class FoxESSCoordinatorData:
    """Current FoxESS API snapshot."""

    detail: dict[str, Any]
    realtime: dict[str, dict[str, Any]]
    report: dict[str, dict[str, Any]]
    battery_settings: FoxESSBatterySocSettings | None
    charge_time_settings: FoxESSChargeTimeSettings | None
    work_mode: str | None
    requested_at: datetime


class FoxESSDataUpdateCoordinator(DataUpdateCoordinator[FoxESSCoordinatorData]):
    """Coordinate FoxESS API polling for one inverter."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: FoxESSApiClient,
        device: FoxESSDevice,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"FoxESS {device.device_sn}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.api = api
        self.device = device
        self.device_sn = device.device_sn
        self.device_name = device.name or device.station_name or device.device_sn
        self._detail: dict[str, Any] = {}
        self._report: dict[str, dict[str, Any]] = {}
        self._detail_fetched_at: datetime | None = None
        self._report_fetched_at: datetime | None = None
        self._battery_settings: FoxESSBatterySocSettings | None = None
        self._charge_time_settings: FoxESSChargeTimeSettings | None = None
        self._settings_fetched_at: datetime | None = None
        self._work_mode: str | None = None
        self._work_mode_fetched_at: datetime | None = None

    async def _async_update_data(self) -> FoxESSCoordinatorData:
        now = dt_util.utcnow()

        try:
            if (
                not self._detail
                or self._detail_fetched_at is None
                or now - self._detail_fetched_at >= DETAIL_REFRESH_INTERVAL
            ):
                try:
                    self._detail = await self.api.async_get_device_detail(self.device_sn)
                    self._detail_fetched_at = now
                except FoxESSApiError as err:
                    _LOGGER.warning(
                        "FoxESS detail endpoint unavailable for %s; continuing without metadata: errno=%s msg=%s",
                        self.device_sn,
                        err.errno,
                        err,
                    )
                    self._detail = self._detail or {}
                    self._detail_fetched_at = now

            realtime = await self.api.async_get_realtime(self.device_sn)
            if (
                not self._report
                or self._report_fetched_at is None
                or now - self._report_fetched_at >= REPORT_REFRESH_INTERVAL
            ):
                self._report = await self.api.async_get_daily_report(
                    self.device_sn,
                    dt_util.now().date(),
                )
                self._report_fetched_at = now
            if (
                self._settings_fetched_at is None
                or now - self._settings_fetched_at >= SETTINGS_REFRESH_INTERVAL
            ):
                await self._async_refresh_control_settings(now)
            if (
                self._work_mode_fetched_at is None
                or now - self._work_mode_fetched_at >= WORK_MODE_REFRESH_INTERVAL
            ):
                await self._async_refresh_work_mode(now)
        except FoxESSAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FoxESSRateLimitError as err:
            raise UpdateFailed(f"FoxESS API rate limited: {err}") from err
        except FoxESSApiError as err:
            raise UpdateFailed(str(err)) from err

        return FoxESSCoordinatorData(
            detail=self._detail,
            realtime=realtime,
            report=self._report,
            battery_settings=self._battery_settings,
            charge_time_settings=self._charge_time_settings,
            work_mode=self._work_mode,
            requested_at=now,
        )

    async def _async_refresh_control_settings(self, now: datetime | None = None) -> None:
        """Refresh slower-changing writable settings."""
        try:
            self._battery_settings = await self.api.async_get_battery_soc_settings(self.device_sn)
        except FoxESSApiError as err:
            _LOGGER.debug("Battery SOC settings unavailable for %s: %s", self.device_sn, err)
            self._battery_settings = None

        try:
            self._charge_time_settings = await self.api.async_get_charge_time_settings(self.device_sn)
        except FoxESSApiError as err:
            _LOGGER.debug("Charge time settings unavailable for %s: %s", self.device_sn, err)
            self._charge_time_settings = None

        self._settings_fetched_at = now or dt_util.utcnow()

    async def _async_refresh_work_mode(self, now: datetime | None = None) -> None:
        """Refresh the current work mode more frequently than other settings."""
        try:
            self._work_mode = await self.api.async_get_work_mode(self.device_sn)
        except FoxESSApiError as err:
            _LOGGER.debug("Work mode unavailable for %s: %s", self.device_sn, err)
            self._work_mode = self._work_mode
        self._work_mode_fetched_at = now or dt_util.utcnow()

    async def async_set_battery_soc_settings(
        self,
        *,
        min_soc: int | None = None,
        min_soc_on_grid: int | None = None,
    ) -> None:
        """Update minimum SOC settings and refresh the coordinator."""
        current = self.data.battery_settings if self.data else self._battery_settings
        if current is None:
            raise UpdateFailed("Battery SOC settings are not available for this inverter")

        await self.api.async_set_battery_soc_settings(
            self.device_sn,
            min_soc=min_soc if min_soc is not None else current.min_soc,
            min_soc_on_grid=(
                min_soc_on_grid if min_soc_on_grid is not None else current.min_soc_on_grid
            ),
        )
        await self._async_refresh_control_settings()
        self.async_update_listeners()

    async def async_set_charge_from_grid(self, period: int, enabled: bool) -> None:
        """Enable or disable charging from grid for a force-charge time period."""
        await self.async_set_charge_period(
            period,
            charge_from_grid=enabled,
        )

    async def async_set_charge_period_time(
        self,
        period: int,
        field: str,
        value: dt_time,
    ) -> None:
        """Set the start or end time of a force-charge period."""
        if field == "start":
            await self.async_set_charge_period(period, start=value)
        else:
            await self.async_set_charge_period(period, end=value)

    async def async_set_charge_period(
        self,
        period: int,
        *,
        charge_from_grid: bool | None = None,
        start: dt_time | None = None,
        end: dt_time | None = None,
    ) -> None:
        """Update one charge period with a single API write."""
        settings = _copy_charge_settings(
            self.data.charge_time_settings if self.data else self._charge_time_settings
        )
        if settings is None:
            raise UpdateFailed("Charge time settings are not available for this inverter")

        target = settings.period_1 if period == 1 else settings.period_2
        if charge_from_grid is not None:
            target.charge_from_grid = charge_from_grid
        if start is not None:
            target.start = start
        if end is not None:
            target.end = end
        await self.api.async_set_charge_time_settings(self.device_sn, settings)
        await self._async_refresh_control_settings()
        self.async_update_listeners()


def _copy_charge_settings(
    settings: FoxESSChargeTimeSettings | None,
) -> FoxESSChargeTimeSettings | None:
    """Make a mutable copy of charge-time settings."""
    if settings is None:
        return None
    return FoxESSChargeTimeSettings(
        period_1=FoxESSChargePeriod(
            charge_from_grid=settings.period_1.charge_from_grid,
            start=settings.period_1.start,
            end=settings.period_1.end,
        ),
        period_2=FoxESSChargePeriod(
            charge_from_grid=settings.period_2.charge_from_grid,
            start=settings.period_2.start,
            end=settings.period_2.end,
        ),
    )
