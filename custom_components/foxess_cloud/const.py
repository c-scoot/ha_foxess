"""Constants for the FoxESS Cloud integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "foxess_cloud"
NAME: Final = "FoxESS Cloud"

CONF_API_KEY: Final = "api_key"
CONF_DEVICE_SN: Final = "device_sn"
CONF_DEVICE_SNS: Final = "device_sns"

PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]

API_BASE_URL: Final = "https://www.foxesscloud.com"
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=5)
REPORT_REFRESH_INTERVAL: Final = timedelta(minutes=5)
DETAIL_REFRESH_INTERVAL: Final = timedelta(hours=6)
SETTINGS_REFRESH_INTERVAL: Final = timedelta(hours=6)
WORK_MODE_REFRESH_INTERVAL: Final = timedelta(minutes=5)
REQUEST_TIMEOUT_SECONDS: Final = 30
QUERY_REQUEST_MIN_INTERVAL_SECONDS: Final = 1.05
UPDATE_REQUEST_MIN_INTERVAL_SECONDS: Final = 2.05

REALTIME_V1_PATH: Final = "/op/v1/device/real/query"
REALTIME_V0_PATH: Final = "/op/v0/device/real/query"
DETAIL_V1_PATH: Final = "/op/v1/device/detail"
DETAIL_V0_PATH: Final = "/op/v0/device/detail"
DEVICE_LIST_PATH: Final = "/op/v0/device/list"
REPORT_PATH: Final = "/op/v0/device/report/query"
BATTERY_SOC_GET_PATH: Final = "/op/v0/device/battery/soc/get"
BATTERY_SOC_SET_PATH: Final = "/op/v0/device/battery/soc/set"
FORCE_CHARGE_TIME_GET_PATH: Final = "/op/v0/device/battery/forceChargeTime/get"
FORCE_CHARGE_TIME_SET_PATH: Final = "/op/v0/device/battery/forceChargeTime/set"
DEVICE_SETTING_SET_PATH: Final = "/op/v0/device/setting/set"
DEVICE_SETTING_GET_PATH: Final = "/op/v0/device/setting/get"

REAUTH_ERRORS: Final = {41808, 41809}
AUTH_ERRORS: Final = {41807, *REAUTH_ERRORS}
REQUEST_ERRORS: Final = {40256, 40257}
RATE_LIMIT_ERRORS: Final = {40400}

REPORT_VARIABLES: Final[tuple[str, ...]] = (
    "generation",
    "PVEnergyTotal",
    "feedin",
    "gridConsumption",
    "loads",
    "chargeEnergyToTal",
    "dischargeEnergyToTal",
)

SERVICE_SET_MIN_SOC: Final = "set_min_soc"
SERVICE_SET_CHARGE_PERIODS: Final = "set_charge_periods"
SERVICE_SET_DEVICE_SETTING: Final = "set_device_setting"
