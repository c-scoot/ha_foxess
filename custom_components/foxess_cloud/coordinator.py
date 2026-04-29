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
    FoxESSApiUsageStats,
    normalize_work_mode_option_key,
)
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DETAIL_REFRESH_INTERVAL,
    REPORT_REFRESH_INTERVAL,
    SCHEDULER_SNAPSHOT_REFRESH_INTERVAL,
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
    scheduler_enabled: bool | None
    scheduler_supported: bool | None
    scheduler_snapshot: dict[str, Any] | None
    api_usage: FoxESSApiUsageStats
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
        self._scheduler_enabled: bool | None = None
        self._scheduler_supported: bool | None = None
        self._scheduler_snapshot: dict[str, Any] | None = None
        self._scheduler_flag_fetched_at: datetime | None = None
        self._scheduler_snapshot_fetched_at: datetime | None = None

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
            if self._scheduler_flag_fetched_at is None:
                await self._async_refresh_scheduler_flag(now)
            if (
                self._scheduler_supported is not False
                and (
                    self._scheduler_snapshot_fetched_at is None
                    or now - self._scheduler_snapshot_fetched_at >= SCHEDULER_SNAPSHOT_REFRESH_INTERVAL
                )
            ):
                await self._async_refresh_scheduler_snapshot(now)
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
            scheduler_enabled=self._scheduler_enabled,
            scheduler_supported=self._scheduler_supported,
            scheduler_snapshot=self._scheduler_snapshot,
            api_usage=self.api.get_daily_usage(self.device_sn),
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

    async def _async_refresh_work_mode(
        self,
        now: datetime | None = None,
        *,
        expected_option_key: str | None = None,
    ) -> None:
        """Refresh the current work mode and optionally verify a recent write."""
        try:
            self._work_mode = await self.api.async_get_work_mode(self.device_sn)
        except FoxESSApiError as err:
            _LOGGER.debug("Work mode unavailable for %s: %s", self.device_sn, err)
        else:
            if expected_option_key is not None:
                actual_option_key = normalize_work_mode_option_key(self._work_mode)
                if actual_option_key is not None and actual_option_key != expected_option_key:
                    _LOGGER.warning(
                        "Work mode readback mismatch for %s: expected=%s actual=%s raw=%s",
                        self.device_sn,
                        expected_option_key,
                        actual_option_key,
                        self._work_mode,
                    )
        self._work_mode_fetched_at = now or dt_util.utcnow()

    async def _async_refresh_scheduler_flag(self, now: datetime | None = None) -> None:
        """Refresh the lightweight scheduler support/enabled flag."""
        try:
            scheduler_flag = await self.api.async_get_scheduler_flag(self.device_sn)
            self._scheduler_enabled = scheduler_flag.get("enable")
            self._scheduler_supported = scheduler_flag.get("support")
        except FoxESSApiError as err:
            _LOGGER.debug("Scheduler flag unavailable for %s: %s", self.device_sn, err)
        self._scheduler_flag_fetched_at = now or dt_util.utcnow()

    async def _async_refresh_scheduler_snapshot(self, now: datetime | None = None) -> None:
        """Refresh full scheduler metadata, including groups and work-mode enums."""
        if self._scheduler_supported is False:
            self._scheduler_snapshot_fetched_at = now or dt_util.utcnow()
            return
        try:
            self._scheduler_snapshot = await self.api.async_get_scheduler(self.device_sn)
        except FoxESSApiError as err:
            _LOGGER.debug("Scheduler snapshot unavailable for %s: %s", self.device_sn, err)
        self._scheduler_snapshot_fetched_at = now or dt_util.utcnow()

    async def _async_refresh_scheduler_metadata(
        self,
        *,
        include_snapshot: bool = False,
    ) -> None:
        """Refresh scheduler state after a user-triggered write."""
        await self._async_refresh_scheduler_flag()
        if include_snapshot:
            await self._async_refresh_scheduler_snapshot()

    async def async_set_scheduler_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduler mode and refresh coordinator state."""
        await self.api.async_set_scheduler_enabled(self.device_sn, enabled)
        self._scheduler_enabled = enabled
        self._scheduler_supported = True
        await self._async_refresh_scheduler_metadata(include_snapshot=True)
        await self._async_refresh_work_mode()
        self.async_update_listeners()

    async def async_set_work_mode(self, option_key: str) -> None:
        """Set a top-level work mode while preserving scheduler compatibility."""
        current_data = getattr(self, "data", None)
        scheduler_error: FoxESSApiError | None = None
        work_mode_error: FoxESSApiError | None = None
        current_work_mode_key = next(
            (
                normalized_key
                for normalized_key in (
                    normalize_work_mode_option_key(candidate)
                    for candidate in (
                        self._work_mode,
                        current_data.work_mode if current_data is not None else None,
                    )
                )
                if normalized_key is not None
            ),
            None,
        )

        if option_key == "mode_scheduler":
            try:
                await self.api.async_set_scheduler_enabled(self.device_sn, True)
            except FoxESSApiError as err:
                scheduler_error = err
            else:
                self._scheduler_enabled = True
                self._scheduler_supported = True
                await self._async_refresh_scheduler_metadata(include_snapshot=True)
                await self._async_refresh_work_mode()
                self.async_update_listeners()
                return

            try:
                self._work_mode = await self.api.async_set_work_mode(self.device_sn, option_key)
            except FoxESSApiError as err:
                work_mode_error = err
            else:
                await self._async_refresh_scheduler_metadata()
                await self._async_refresh_work_mode(expected_option_key=option_key)
                self.async_update_listeners()
                return

            raise UpdateFailed(
                "Unable to enable Mode Scheduler. "
                f"Scheduler flag write failed: {scheduler_error}. "
                f"WorkMode fallback failed: {work_mode_error}."
            ) from work_mode_error or scheduler_error

        should_try_disable_scheduler = (
            option_key == "self_use"
            or self._scheduler_enabled is True
            or (current_data is not None and current_data.scheduler_enabled is True)
            or current_work_mode_key == "mode_scheduler"
        )
        if should_try_disable_scheduler:
            try:
                await self.api.async_set_scheduler_enabled(self.device_sn, False)
            except FoxESSApiError as err:
                scheduler_error = err
            else:
                self._scheduler_enabled = False
                self._scheduler_supported = True

        try:
            self._work_mode = await self.api.async_set_work_mode(self.device_sn, option_key)
        except FoxESSApiError as err:
            work_mode_error = err
        else:
            await self._async_refresh_scheduler_metadata()
            await self._async_refresh_work_mode(expected_option_key=option_key)
            self.async_update_listeners()
            return

        if scheduler_error is not None:
            raise UpdateFailed(
                f"Unable to set work mode {option_key}: "
                f"scheduler disable failed ({scheduler_error}) and "
                f"work-mode write failed ({work_mode_error})."
            ) from work_mode_error or scheduler_error
        raise UpdateFailed(f"Unable to set work mode {option_key}: {work_mode_error}") from work_mode_error

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
