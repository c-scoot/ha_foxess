"""FoxESS Cloud API client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
import hashlib
import logging
import re
import time
from typing import Any

import aiohttp
import async_timeout
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE_URL,
    AUTH_ERRORS,
    BATTERY_SOC_GET_PATH,
    BATTERY_SOC_SET_PATH,
    DETAIL_V0_PATH,
    DETAIL_V1_PATH,
    DEVICE_LIST_PATH,
    DEVICE_SETTING_GET_PATH,
    DEVICE_SETTING_SET_PATH,
    FORCE_CHARGE_TIME_GET_PATH,
    FORCE_CHARGE_TIME_SET_PATH,
    QUERY_REQUEST_MIN_INTERVAL_SECONDS,
    RATE_LIMIT_ERRORS,
    REQUEST_ERRORS,
    REALTIME_V0_PATH,
    REALTIME_V1_PATH,
    REPORT_PATH,
    REPORT_VARIABLES,
    SCHEDULER_GET_FLAG_V0_PATH,
    SCHEDULER_GET_FLAG_V1_PATH,
    SCHEDULER_GET_V0_PATH,
    SCHEDULER_GET_V1_PATH,
    SCHEDULER_GET_V2_PATH,
    SCHEDULER_GET_V3_PATH,
    SCHEDULER_SET_FLAG_V0_PATH,
    SCHEDULER_SET_FLAG_V1_PATH,
    REQUEST_TIMEOUT_SECONDS,
    UPDATE_REQUEST_MIN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_CAMEL_CASE_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")
_PV_PATTERN = re.compile(r"^(pv)(\d+)(power|volt|voltage|current)$", re.IGNORECASE)
_PHASE_PATTERN = re.compile(r"^([rst])(volt|current|power|freq)$", re.IGNORECASE)
_WORK_MODE_SETTING_KEYS: tuple[str, ...] = ("WorkMode", "workMode")
_WORK_MODE_OPTION_CANDIDATES: dict[str, tuple[str, ...]] = {
    "self_use": ("SelfUse", "SelfUseMode", "Self Use", "Self-use"),
    "feed_in_priority": (
        "Feedin",
        "FeedIn",
        "FeedinMode",
        "FeedInMode",
        "FeedinFirst",
        "FeedInFirst",
        "FeedinPriority",
        "FeedInPriority",
        "Feed In Priority",
        "Feed-in Priority",
    ),
    "backup": ("Backup", "BackUp", "Back Up", "Back-up"),
    "mode_scheduler": ("ModeScheduler", "Scheduler", "TimeMode", "Mode Scheduler"),
}


class FoxESSApiError(Exception):
    """Base FoxESS API exception."""

    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.errno = errno


class FoxESSAuthenticationError(FoxESSApiError):
    """Raised when credentials are invalid or expired."""


class FoxESSRateLimitError(FoxESSApiError):
    """Raised when the API rate limit is hit."""


def _normalize_work_mode_value(value: str) -> str:
    """Normalize FoxESS work-mode labels across spacing/casing variants."""
    return value.strip().replace(" ", "").replace("-", "").replace("_", "").lower()


def normalize_work_mode_option_key(value: object) -> str | None:
    """Map a FoxESS work-mode value to one of the integration option keys."""
    if not isinstance(value, str):
        return None
    return _WORK_MODE_VALUE_TO_OPTION_KEY.get(_normalize_work_mode_value(value))


_WORK_MODE_VALUE_TO_OPTION_KEY: dict[str, str] = {
    normalized_candidate: option_key
    for option_key, candidates in _WORK_MODE_OPTION_CANDIDATES.items()
    for normalized_candidate in (_normalize_work_mode_value(candidate) for candidate in candidates)
}


@dataclass(slots=True)
class FoxESSDevice:
    """FoxESS device summary."""

    device_sn: str
    name: str | None
    device_type: str | None
    station_name: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class FoxESSBatterySocSettings:
    """Battery SOC settings."""

    min_soc: int
    min_soc_on_grid: int


@dataclass(slots=True)
class FoxESSChargePeriod:
    """A single force-charge time period."""

    charge_from_grid: bool
    start: dt_time
    end: dt_time


@dataclass(slots=True)
class FoxESSChargeTimeSettings:
    """Force-charge settings for both periods."""

    period_1: FoxESSChargePeriod
    period_2: FoxESSChargePeriod


@dataclass(slots=True)
class FoxESSApiUsageStats:
    """Per-device daily API usage counters."""

    day: date
    calls: int = 0
    errors: int = 0
    last_called_at: datetime | None = None
    last_error_at: datetime | None = None


class FoxESSApiClient:
    """Wrapper around the FoxESS Cloud API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._use_realtime_v1 = True
        self._use_detail_v1 = True
        self._scheduler_flag_get_request_by_device: dict[str, tuple[str, str]] = {}
        self._scheduler_flag_set_request_by_device: dict[str, tuple[str, str]] = {}
        self._scheduler_get_request_by_device: dict[str, tuple[str, str]] = {}
        self._work_mode_read_key_by_device: dict[str, str] = {}
        self._work_mode_write_pair_by_device: dict[tuple[str, str], tuple[str, str]] = {}
        self._path_locks: dict[str, asyncio.Lock] = {}
        self._last_request_started: dict[str, float] = {}
        self._usage_by_device: dict[str, FoxESSApiUsageStats] = {}

    def _headers(self, path: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        # FoxESS signs the request path with literal "\r\n" separators.
        raw = f"{path}\\r\\n{self._api_key}\\r\\n{timestamp}"
        signature = hashlib.md5(raw.encode("utf-8")).hexdigest()
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "lang": "en",
            "signature": signature,
            "timestamp": timestamp,
            "token": self._api_key,
            "user-agent": "Home Assistant FoxESS Cloud",
        }

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        *,
        log_request_errors: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        await self._async_wait_for_rate_limit(path, method)
        device_sn = _extract_device_sn(payload, params)
        self._record_request(device_sn)
        _LOGGER.debug(
            "FoxESS request %s %s payload=%s params=%s",
            method,
            path,
            _sanitize_mapping(payload),
            _sanitize_mapping(params),
        )

        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT_SECONDS):
                response = await self._session.request(
                    method,
                    url,
                    headers=self._headers(path),
                    json=payload,
                    params=params,
                )
        except TimeoutError as err:
            self._record_request_error(device_sn)
            raise FoxESSApiError("Timed out contacting FoxESS") from err
        except aiohttp.ClientError as err:
            self._record_request_error(device_sn)
            raise FoxESSApiError("Unable to connect to FoxESS") from err

        try:
            data = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError) as err:
            self._record_request_error(device_sn)
            text = await response.text()
            if response.status == 200 and text.strip() == "":
                _LOGGER.debug("FoxESS response %s %s status=%s empty-json-body", method, path, response.status)
                return {"errno": 0, "result": None}
            raise FoxESSApiError(f"Invalid FoxESS response: {text}") from err

        _LOGGER.debug(
            "FoxESS response %s %s status=%s errno=%s msg=%s",
            method,
            path,
            response.status,
            data.get("errno"),
            data.get("msg"),
        )

        if response.status >= 400:
            self._record_request_error(device_sn)
            _LOGGER.warning(
                "FoxESS HTTP error on %s %s: status=%s msg=%s payload=%s params=%s",
                method,
                path,
                response.status,
                data.get("msg"),
                _sanitize_mapping(payload),
                _sanitize_mapping(params),
            )
            raise FoxESSApiError(
                f"FoxESS returned HTTP {response.status}: {data.get('msg', 'Unknown error')}"
            )

        errno = data.get("errno", 0)
        if errno in AUTH_ERRORS:
            self._record_request_error(device_sn)
            _LOGGER.warning(
                "FoxESS auth error on %s %s: errno=%s msg=%s payload=%s params=%s",
                method,
                path,
                errno,
                data.get("msg"),
                _sanitize_mapping(payload),
                _sanitize_mapping(params),
            )
            raise FoxESSAuthenticationError(data.get("msg", "Authentication failed"), errno)
        if errno in REQUEST_ERRORS:
            self._record_request_error(device_sn)
            log_fn = _LOGGER.warning if log_request_errors else _LOGGER.debug
            log_fn(
                "FoxESS request error on %s %s: errno=%s msg=%s payload=%s params=%s",
                method,
                path,
                errno,
                data.get("msg"),
                _sanitize_mapping(payload),
                _sanitize_mapping(params),
            )
            raise FoxESSApiError(
                data.get("msg", "FoxESS rejected the request format or headers"),
                errno,
            )
        if errno in RATE_LIMIT_ERRORS:
            self._record_request_error(device_sn)
            _LOGGER.warning(
                "FoxESS rate limit on %s %s: errno=%s msg=%s",
                method,
                path,
                errno,
                data.get("msg"),
            )
            raise FoxESSRateLimitError(data.get("msg", "Rate limited"), errno)
        if errno:
            _LOGGER.warning(
                "FoxESS API error on %s %s: errno=%s msg=%s payload=%s params=%s",
                method,
                path,
                errno,
                data.get("msg"),
                _sanitize_mapping(payload),
                _sanitize_mapping(params),
            )
            raise FoxESSApiError(data.get("msg", "FoxESS API error"), errno)

        return data

    def get_daily_usage(self, device_sn: str) -> FoxESSApiUsageStats:
        """Return today's per-device API usage counters."""
        return self._get_usage_bucket(device_sn)

    def _record_request(self, device_sn: str | None) -> None:
        """Record an outbound FoxESS request for a device, if identifiable."""
        if device_sn is None:
            return
        usage = self._get_usage_bucket(device_sn)
        usage.calls += 1
        usage.last_called_at = dt_util.now()

    def _record_request_error(self, device_sn: str | None) -> None:
        """Record a failed FoxESS request for a device, if identifiable."""
        if device_sn is None:
            return
        usage = self._get_usage_bucket(device_sn)
        usage.errors += 1
        usage.last_error_at = dt_util.now()

    def _get_usage_bucket(self, device_sn: str) -> FoxESSApiUsageStats:
        """Return the counter bucket for the current local day."""
        today = dt_util.now().date()
        usage = self._usage_by_device.get(device_sn)
        if usage is None or usage.day != today:
            usage = FoxESSApiUsageStats(day=today)
            self._usage_by_device[device_sn] = usage
        return usage

    async def _async_wait_for_rate_limit(self, path: str, method: str) -> None:
        """Honor FoxESS's per-interface rate limits."""
        interval = (
            UPDATE_REQUEST_MIN_INTERVAL_SECONDS
            if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and any(token in path for token in ("/set", "/enable"))
            else QUERY_REQUEST_MIN_INTERVAL_SECONDS
        )
        lock = self._path_locks.setdefault(path, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last_started = self._last_request_started.get(path)
            if last_started is not None:
                wait_time = interval - (now - last_started)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            self._last_request_started[path] = time.monotonic()

    async def _request_with_fallback(
        self,
        *,
        preferred_path: str,
        fallback_path: str,
        payload: dict[str, Any],
        remember_v1_attr: str,
    ) -> dict[str, Any]:
        use_v1 = getattr(self, remember_v1_attr)
        paths = [preferred_path, fallback_path] if use_v1 else [fallback_path]

        last_error: FoxESSApiError | None = None
        for index, path in enumerate(paths):
            try:
                response = await self._request("POST", path, payload)
            except (FoxESSAuthenticationError, FoxESSRateLimitError):
                raise
            except FoxESSApiError as err:
                last_error = err
                if index == 0 and path == preferred_path:
                    _LOGGER.debug("FoxESS v1 request failed for %s, trying v0: %s", path, err)
                    setattr(self, remember_v1_attr, False)
                    continue
                break
            else:
                if path == preferred_path:
                    setattr(self, remember_v1_attr, True)
                return response

        raise last_error or FoxESSApiError("FoxESS request failed")

    async def async_list_devices(self) -> list[FoxESSDevice]:
        """Return devices visible to the API key."""
        try:
            data = await self._request(
                "POST",
                DEVICE_LIST_PATH,
                payload={"currentPage": 1, "pageSize": 1000},
            )
        except FoxESSApiError as err:
            if err.errno in REQUEST_ERRORS:
                _LOGGER.debug(
                    "FoxESS device list rejected paginated payload, retrying with empty body: errno=%s msg=%s",
                    err.errno,
                    err,
                )
                data = await self._request("POST", DEVICE_LIST_PATH, payload={})
            else:
                raise
        result = data.get("result") or []
        if isinstance(result, dict):
            rows = result.get("data") or result.get("devices") or []
        else:
            rows = result

        devices: list[FoxESSDevice] = []
        for item in rows if isinstance(rows, list) else []:
            device_sn = item.get("deviceSN") or item.get("sn") or item.get("deviceSn")
            if not device_sn:
                continue
            devices.append(
                FoxESSDevice(
                    device_sn=device_sn,
                    name=item.get("deviceName") or item.get("name"),
                    device_type=item.get("deviceType"),
                    station_name=item.get("stationName") or item.get("plantName"),
                    raw=item,
                )
            )
        return devices

    async def async_get_device_detail(self, device_sn: str) -> dict[str, Any]:
        """Return detail data for a device."""
        use_v1 = self._use_detail_v1
        paths = [DETAIL_V1_PATH, DETAIL_V0_PATH] if use_v1 else [DETAIL_V0_PATH]
        last_error: FoxESSApiError | None = None

        for index, path in enumerate(paths):
            try:
                data = await self._request("GET", path, params={"sn": device_sn})
            except (FoxESSAuthenticationError, FoxESSRateLimitError):
                raise
            except FoxESSApiError as err:
                last_error = err
                if index == 0 and path == DETAIL_V1_PATH:
                    _LOGGER.debug(
                        "FoxESS v1 device detail request failed for %s, trying v0: %s",
                        device_sn,
                        err,
                    )
                    self._use_detail_v1 = False
                    continue
                break
            else:
                if path == DETAIL_V1_PATH:
                    self._use_detail_v1 = True

                result = data.get("result") or {}
                if isinstance(result, list):
                    return result[0] if result else {}
                return result

        raise last_error or FoxESSApiError("FoxESS device detail request failed")

    async def async_get_realtime(self, device_sn: str) -> dict[str, dict[str, Any]]:
        """Return realtime variables for a device."""
        last_error: FoxESSApiError | None = None
        requests: list[tuple[str, dict[str, Any], bool]] = []
        if self._use_realtime_v1:
            requests.append((REALTIME_V1_PATH, {"sns": [device_sn]}, True))
        requests.append((REALTIME_V0_PATH, {"sn": device_sn}, False))

        data: dict[str, Any] | None = None
        for index, (path, payload, is_v1) in enumerate(requests):
            try:
                data = await self._request("POST", path, payload=payload)
            except (FoxESSAuthenticationError, FoxESSRateLimitError):
                raise
            except FoxESSApiError as err:
                last_error = err
                if index == 0 and is_v1:
                    _LOGGER.debug(
                        "FoxESS v1 realtime request failed for %s, trying v0: %s",
                        device_sn,
                        err,
                    )
                    self._use_realtime_v1 = False
                    continue
                break
            else:
                if is_v1:
                    self._use_realtime_v1 = True
                break

        if data is None:
            raise last_error or FoxESSApiError("FoxESS realtime request failed")

        result = data.get("result") or []
        block = _select_device_result_block(result, device_sn)
        if not block:
            raise FoxESSApiError(
                f"FoxESS realtime response did not include requested device: {device_sn}"
            )
        variables: dict[str, dict[str, Any]] = {}

        for item in block.get("datas", []):
            key = item.get("variable")
            if not key:
                continue
            variables[key] = {
                "value": _coerce_value(item.get("value")),
                "unit": item.get("unit"),
                "name": item.get("name"),
                "time": item.get("time"),
            }

        return variables

    async def async_get_daily_report(
        self, device_sn: str, target_date: date
    ) -> dict[str, dict[str, Any]]:
        """Return the current day's cumulative report values."""
        payload = {
            "sn": device_sn,
            "year": target_date.year,
            "month": target_date.month,
            "dimension": "month",
            "variables": list(REPORT_VARIABLES),
        }
        data = await self._request("POST", REPORT_PATH, payload=payload)
        result = data.get("result") or []
        if isinstance(result, dict):
            result = result.get("datas") or result.get("result") or []
        values: dict[str, dict[str, Any]] = {}
        day_index = target_date.day - 1

        for item in result:
            variable = item.get("variable")
            if not variable:
                continue
            series = item.get("values") or []
            state = None
            if len(series) > day_index:
                state = series[day_index]
            elif series:
                state = series[-1]

            values[variable] = {
                "value": _coerce_value(state),
                "unit": item.get("unit"),
                "name": item.get("name"),
            }

        return values

    async def async_get_battery_soc_settings(
        self, device_sn: str
    ) -> FoxESSBatterySocSettings | None:
        """Return minimum SOC settings, if supported."""
        data = await self._request("GET", BATTERY_SOC_GET_PATH, params={"sn": device_sn})
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        min_soc = _coerce_value(result.get("minSoc"))
        min_soc_on_grid = _coerce_value(result.get("minSocOnGrid"))
        if min_soc is None or min_soc_on_grid is None:
            return None
        return FoxESSBatterySocSettings(
            min_soc=int(min_soc),
            min_soc_on_grid=int(min_soc_on_grid),
        )

    async def async_set_battery_soc_settings(
        self,
        device_sn: str,
        *,
        min_soc: int,
        min_soc_on_grid: int,
    ) -> None:
        """Set minimum SOC settings."""
        await self._request(
            "POST",
            BATTERY_SOC_SET_PATH,
            payload={
                "sn": device_sn,
                "minSoc": int(min_soc),
                "minSocOnGrid": int(min_soc_on_grid),
            },
        )

    async def async_get_charge_time_settings(
        self, device_sn: str
    ) -> FoxESSChargeTimeSettings | None:
        """Return force-charge time settings, if supported."""
        data = await self._request(
            "GET",
            FORCE_CHARGE_TIME_GET_PATH,
            params={"sn": device_sn},
        )
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        return FoxESSChargeTimeSettings(
            period_1=FoxESSChargePeriod(
                charge_from_grid=_coerce_boolish(result.get("enable1")),
                start=_extract_time(result.get("startTime1")),
                end=_extract_time(result.get("endTime1")),
            ),
            period_2=FoxESSChargePeriod(
                charge_from_grid=_coerce_boolish(result.get("enable2")),
                start=_extract_time(result.get("startTime2")),
                end=_extract_time(result.get("endTime2")),
            ),
        )

    async def async_set_charge_time_settings(
        self,
        device_sn: str,
        settings: FoxESSChargeTimeSettings,
    ) -> None:
        """Set force-charge time settings."""
        await self._request(
            "POST",
            FORCE_CHARGE_TIME_SET_PATH,
            payload={
                "sn": device_sn,
                "enable1": int(settings.period_1.charge_from_grid),
                "enable2": int(settings.period_2.charge_from_grid),
                "startTime1": _time_to_payload(settings.period_1.start),
                "endTime1": _time_to_payload(settings.period_1.end),
                "startTime2": _time_to_payload(settings.period_2.start),
                "endTime2": _time_to_payload(settings.period_2.end),
            },
        )

    async def async_set_device_setting(
        self,
        device_sn: str,
        key: str,
        value: Any,
        *,
        log_request_errors: bool = True,
    ) -> None:
        """Set a writable device setting by key."""
        await self._request(
            "POST",
            DEVICE_SETTING_SET_PATH,
            payload={"sn": device_sn, "key": key, "value": value},
            log_request_errors=log_request_errors,
        )

    async def async_get_device_setting(
        self,
        device_sn: str,
        key: str,
        *,
        log_request_errors: bool = False,
    ) -> Any:
        """Read a device setting by key, if the inverter exposes it."""
        requests: tuple[tuple[str, dict[str, Any] | None, dict[str, Any] | None], ...] = (
            ("GET", None, {"sn": device_sn, "key": key}),
            ("POST", {"sn": device_sn, "key": key}, None),
        )
        last_error: FoxESSApiError | None = None

        for method, payload, params in requests:
            try:
                data = await self._request(
                    method,
                    DEVICE_SETTING_GET_PATH,
                    payload=payload,
                    params=params,
                    log_request_errors=log_request_errors,
                )
            except FoxESSApiError as err:
                last_error = err
                continue

            result = data.get("result")
            if isinstance(result, dict):
                for candidate_key in ("value", "currentValue", "settingValue"):
                    if candidate_key in result:
                        return result[candidate_key]
            if result is not None:
                return result
            return None

        raise last_error or FoxESSApiError(f"Unable to read device setting: {key}")

    async def async_set_work_mode(self, device_sn: str, option_key: str) -> str:
        """Set the inverter work mode using the most likely FoxESS keys and values."""
        candidates = _WORK_MODE_OPTION_CANDIDATES.get(option_key)
        if candidates is None:
            raise FoxESSApiError(f"Unsupported work mode option: {option_key}")

        last_error: FoxESSApiError | None = None
        attempted_pairs: list[str] = []
        pairs = tuple(
            (key, candidate)
            for key in _WORK_MODE_SETTING_KEYS
            for candidate in candidates
        )
        cached_pair = self._work_mode_write_pair_by_device.get((device_sn, option_key))
        for key, candidate in _prefer_cached_option(pairs, cached_pair):
            attempted_pairs.append(f"{key}={candidate}")
            _LOGGER.debug(
                "Trying FoxESS work mode write for %s with key=%s value=%s",
                device_sn,
                key,
                candidate,
            )
            try:
                await self.async_set_device_setting(
                    device_sn,
                    key,
                    candidate,
                    log_request_errors=False,
                )
            except FoxESSApiError as err:
                _LOGGER.debug(
                    "FoxESS rejected work mode write for %s with key=%s value=%s errno=%s msg=%s",
                    device_sn,
                    key,
                    candidate,
                    err.errno,
                    err,
                )
                last_error = err
                continue

            self._work_mode_write_pair_by_device[(device_sn, option_key)] = (key, candidate)
            _LOGGER.info(
                "FoxESS accepted work mode write for %s with key=%s value=%s",
                device_sn,
                key,
                candidate,
            )
            return candidate

        attempts = ", ".join(attempted_pairs)
        if last_error is not None:
            raise FoxESSApiError(
                f"Unable to set work mode: {option_key}. Tried {attempts}. Last error: {last_error}",
                last_error.errno,
            ) from last_error
        raise FoxESSApiError(f"Unable to set work mode: {option_key}. Tried {attempts}")

    async def async_get_work_mode(self, device_sn: str) -> str | None:
        """Return the current inverter work mode, if exposed."""
        last_error: FoxESSApiError | None = None
        cached_key = self._work_mode_read_key_by_device.get(device_sn)

        for key in _prefer_cached_option(_WORK_MODE_SETTING_KEYS, cached_key):
            _LOGGER.debug("Trying FoxESS work mode read for %s with key=%s", device_sn, key)
            try:
                value = await self.async_get_device_setting(
                    device_sn,
                    key,
                    log_request_errors=False,
                )
            except FoxESSApiError as err:
                _LOGGER.debug(
                    "FoxESS rejected work mode read for %s with key=%s errno=%s msg=%s",
                    device_sn,
                    key,
                    err.errno,
                    err,
                )
                last_error = err
                continue

            if value is None:
                _LOGGER.debug("FoxESS work mode read returned empty result for %s with key=%s", device_sn, key)
                continue

            _LOGGER.debug(
                "FoxESS work mode read succeeded for %s with key=%s value=%s",
                device_sn,
                key,
                value,
            )
            self._work_mode_read_key_by_device[device_sn] = key
            return str(value)

        if last_error is not None:
            raise FoxESSApiError(
                f"Unable to read work mode. Tried keys: {', '.join(_WORK_MODE_SETTING_KEYS)}. Last error: {last_error}",
                last_error.errno,
            ) from last_error
        return None

    async def async_get_scheduler_flag(self, device_sn: str) -> dict[str, Any]:
        """Return scheduler support and enable status."""
        requests = (
            ("v1", SCHEDULER_GET_FLAG_V1_PATH),
            ("v0", SCHEDULER_GET_FLAG_V0_PATH),
        )
        last_error: FoxESSApiError | None = None
        cached_request = self._scheduler_flag_get_request_by_device.get(device_sn)

        for version, path in _prefer_cached_option(requests, cached_request):
            try:
                data = await self._request("POST", path, payload={"deviceSN": device_sn}, log_request_errors=False)
            except FoxESSApiError as err:
                last_error = err
                continue

            self._scheduler_flag_get_request_by_device[device_sn] = (version, path)
            result = data.get("result") or {}
            if not isinstance(result, dict):
                return {"version": version, "support": None, "enable": None}
            return {
                "version": version,
                "support": _coerce_boolish(result.get("support")) if "support" in result else None,
                "enable": _coerce_boolish(result.get("enable")) if "enable" in result else None,
                "raw": result,
            }

        raise last_error or FoxESSApiError("Unable to read scheduler flag")

    async def async_set_scheduler_enabled(self, device_sn: str, enabled: bool) -> str:
        """Enable or disable scheduler mode."""
        requests = (
            ("v1", SCHEDULER_SET_FLAG_V1_PATH),
            ("v0", SCHEDULER_SET_FLAG_V0_PATH),
        )
        last_error: FoxESSApiError | None = None
        cached_request = self._scheduler_flag_set_request_by_device.get(device_sn)

        for version, path in _prefer_cached_option(requests, cached_request):
            try:
                await self._request(
                    "POST",
                    path,
                    payload={"deviceSN": device_sn, "enable": int(enabled)},
                    log_request_errors=False,
                )
            except FoxESSApiError as err:
                _LOGGER.debug(
                    "FoxESS rejected scheduler flag write for %s with version=%s enable=%s errno=%s msg=%s",
                    device_sn,
                    version,
                    enabled,
                    err.errno,
                    err,
                )
                last_error = err
                continue

            self._scheduler_flag_set_request_by_device[device_sn] = (version, path)
            _LOGGER.info(
                "FoxESS accepted scheduler flag write for %s with version=%s enable=%s",
                device_sn,
                version,
                enabled,
            )
            return version

        raise last_error or FoxESSApiError(f"Unable to set scheduler enabled={enabled}")

    async def async_get_scheduler(self, device_sn: str) -> dict[str, Any]:
        """Return scheduler groups using the newest supported API version."""
        requests = (
            ("v3", SCHEDULER_GET_V3_PATH),
            ("v2", SCHEDULER_GET_V2_PATH),
            ("v1", SCHEDULER_GET_V1_PATH),
            ("v0", SCHEDULER_GET_V0_PATH),
        )
        last_error: FoxESSApiError | None = None
        cached_request = self._scheduler_get_request_by_device.get(device_sn)

        for version, path in _prefer_cached_option(requests, cached_request):
            try:
                data = await self._request("POST", path, payload={"deviceSN": device_sn}, log_request_errors=False)
            except FoxESSApiError as err:
                last_error = err
                continue

            self._scheduler_get_request_by_device[device_sn] = (version, path)
            result = data.get("result")
            return {
                "version": version,
                "result": result,
            }

        raise last_error or FoxESSApiError("Unable to read scheduler groups")


def normalize_key(key: str) -> str:
    """Normalize API variable names to a stable snake_case key."""
    aliases = {
        "SoC": "battery_soc",
        "SoC1": "battery_soc_1",
        "SoC2": "battery_soc_2",
        "SOC": "battery_soc",
        "SOC1": "battery_soc_1",
        "SOC2": "battery_soc_2",
        "soc": "battery_soc",
        "soc1": "battery_soc_1",
        "soc2": "battery_soc_2",
        "SoH": "battery_soh",
        "SoH1": "battery_soh_1",
        "SoH2": "battery_soh_2",
        "SOH": "battery_soh",
        "SOH1": "battery_soh_1",
        "SOH2": "battery_soh_2",
        "soh": "battery_soh",
        "soh1": "battery_soh_1",
        "soh2": "battery_soh_2",
        "PVEnergyTotal": "pv_energy_total",
        "chargeEnergyToTal": "charge_energy_total",
        "dischargeEnergyToTal": "discharge_energy_total",
        "batChargePower": "charge_power",
        "batDischargePower": "discharge_power",
        "invTemperation": "inverter_temperature",
        "batTemperation": "battery_temperature",
    }
    if key in aliases:
        return aliases[key]

    normalized_alias_key = key.replace("_", "").lower()
    if normalized_alias_key in {
        "ambienttemperature",
        "ambienttemperation",
        "ambianttemperature",
        "ambianttemperation",
    }:
        return "ambient_temperature"

    pv_match = _PV_PATTERN.match(key)
    if pv_match:
        _, string_number, metric = pv_match.groups()
        metric_name = "voltage" if metric.lower() in {"volt", "voltage"} else metric.lower()
        return f"pv_{string_number}_{metric_name}"

    phase_match = _PHASE_PATTERN.match(key)
    if phase_match:
        phase, metric = phase_match.groups()
        metric_name = "frequency" if metric.lower() == "freq" else metric.lower()
        return f"phase_{phase.lower()}_{metric_name}"

    normalized = _CAMEL_CASE_PATTERN.sub("_", key).replace("__", "_").lower()
    return normalized


def prettify_key(key: str) -> str:
    """Convert API variable names to a readable entity name."""
    pretty_names = {
        "generationPower": "Inverter Output Power",
        "generation": "Total Inverter Output Energy",
        "PVEnergyTotal": "Total PV Energy",
        "pvPower": "PV Power",
        "feedinPower": "Feed-in Power",
        "feedin": "Total Feed-in",
        "feedin2": "Total Feed-in (Meter 2)",
        "gridConsumptionPower": "Grid Consumption Power",
        "gridConsumption": "Total Grid Consumption",
        "gridConsumption2": "Total Grid Consumption (Meter 2)",
        "loadsPower": "Load Power",
        "loads": "Total Load Consumption",
        "chargePower": "Battery Charge Power",
        "batChargePower": "Battery Charge Power",
        "dischargePower": "Battery Discharge Power",
        "batDischargePower": "Battery Discharge Power",
        "chargeEnergyToTal": "Total Battery Charged",
        "dischargeEnergyToTal": "Total Battery Discharged",
        "energyThroughput": "Battery Throughput",
        "SoC": "Battery SOC",
        "SoC1": "Battery SOC 1",
        "SoC2": "Battery SOC 2",
        "SoH": "Battery SOH",
        "SoH1": "Battery SOH 1",
        "SoH2": "Battery SOH 2",
        "SOH": "Battery SOH",
        "SOH1": "Battery SOH 1",
        "SOH2": "Battery SOH 2",
        "invBatPower": "Battery Net Power",
        "invBatPower2": "Battery Net Power 2",
        "meterPower2": "Meter 2 Power",
        "residualEnergy": "Residual Energy",
        "minSoc": "System Minimum SOC",
        "minSocOnGrid": "Battery Cut-Off SOC",
        "runningState": "Running State",
        "powerFactor": "Power Factor",
        "ambientTemp": "Inverter Internal Temperature",
        "ambientTemperature": "Inverter Internal Temperature",
        "ambientTemperation": "Inverter Internal Temperature",
        "ambiantTemperature": "Inverter Internal Temperature",
        "ambiantTemperation": "Inverter Internal Temperature",
        "boostTemp": "Boost Temperature",
        "invTemp": "Inverter Temperature",
        "invTemperation": "Inverter Temperature",
        "batTemperature": "Battery BMS Temperature",
        "batTemperation": "Battery BMS Temperature",
        "batTemperature2": "Battery Temperature 2",
        "maxChargeCurrent": "Max Battery Charge Current",
        "maxDischargeCurrent": "Max Battery Discharge Current",
    }
    if key in pretty_names:
        return pretty_names[key]

    pv_match = _PV_PATTERN.match(key)
    if pv_match:
        _, string_number, metric = pv_match.groups()
        metric_name = "Voltage" if metric.lower() in {"volt", "voltage"} else metric.capitalize()
        return f"PV {string_number} {metric_name}"

    phase_match = _PHASE_PATTERN.match(key)
    if phase_match:
        phase, metric = phase_match.groups()
        metric_name = "Frequency" if metric.lower() == "freq" else metric.capitalize()
        return f"Phase {phase.upper()} {metric_name}"

    return _CAMEL_CASE_PATTERN.sub(" ", key).replace("_", " ").title()


def _coerce_value(value: Any) -> Any:
    """Convert numeric API values from strings where possible."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            if "." in stripped:
                return float(stripped)
            return int(stripped)
        except ValueError:
            return value
    return value


def _coerce_boolish(value: Any) -> bool:
    """Convert FoxESS 0/1-style values to bool."""
    coerced = _coerce_value(value)
    if isinstance(coerced, bool):
        return coerced
    if isinstance(coerced, (int, float)):
        return coerced != 0
    if isinstance(coerced, str):
        normalized = coerced.strip().lower()
        if normalized in {"true", "on", "yes"}:
            return True
        if normalized in {"false", "off", "no", ""}:
            return False
    return bool(coerced)


def _prefer_cached_option(options: tuple[Any, ...], cached: Any | None) -> tuple[Any, ...]:
    """Return options with the last successful fallback first."""
    if cached is None or cached not in options:
        return options
    return (cached, *(option for option in options if option != cached))


def _select_device_result_block(result: Any, device_sn: str) -> dict[str, Any]:
    """Select the result block that belongs to the requested inverter."""
    if isinstance(result, dict):
        result_sn = result.get("deviceSN") or result.get("sn") or result.get("deviceSn")
        if result_sn in {None, device_sn}:
            return result
        return {}

    if not isinstance(result, list):
        return {}

    dict_items = [item for item in result if isinstance(item, dict)]
    if len(dict_items) == 1:
        result_sn = dict_items[0].get("deviceSN") or dict_items[0].get("sn") or dict_items[0].get("deviceSn")
        if result_sn in {None, device_sn}:
            return dict_items[0]
        return {}

    for item in dict_items:
        result_sn = item.get("deviceSN") or item.get("sn") or item.get("deviceSn")
        if result_sn == device_sn:
            return item

    return {}


def _extract_time(value: Any) -> dt_time:
    """Convert an API hour/minute object into time."""
    if not isinstance(value, dict):
        return dt_time(0, 0)
    return dt_time(
        hour=int(_coerce_value(value.get("hour")) or 0),
        minute=int(_coerce_value(value.get("minute")) or 0),
    )


def _time_to_payload(value: dt_time) -> dict[str, int]:
    """Convert a time to the API payload shape."""
    return {"hour": value.hour, "minute": value.minute}


def _sanitize_mapping(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact sensitive request values before logging."""
    if mapping is None:
        return None
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if key.lower() in {"token", "api_key", "apikey", "signature"}:
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


def _extract_device_sn(
    payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> str | None:
    """Extract a single device serial number from common FoxESS request shapes."""
    for mapping in (payload, params):
        if not isinstance(mapping, dict):
            continue

        for key in ("sn", "deviceSN", "deviceSn"):
            value = mapping.get(key)
            if isinstance(value, str) and value:
                return value

        sns = mapping.get("sns")
        if isinstance(sns, list) and len(sns) == 1 and isinstance(sns[0], str) and sns[0]:
            return sns[0]

    return None
