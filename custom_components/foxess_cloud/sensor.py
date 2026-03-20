"""Sensor platform for FoxESS Cloud."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import normalize_key, prettify_key
from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator

_PV_STRING_POWER_KEY_PATTERN = re.compile(r"^pv_(\d+)_power$")
_RUNNING_STATE_LABELS: dict[int, str] = {
    160: "Self-Test",
    161: "Waiting",
    162: "Checking",
    163: "On-Grid",
    164: "Off-Grid",
    165: "Fault",
    166: "Permanent Fault",
    167: "Standby",
    168: "Upgrading",
    169: "FCT",
    170: "Illegal",
}


@dataclass(frozen=True, kw_only=True)
class FoxESSSensorDescription(SensorEntityDescription):
    """Description of a FoxESS sensor."""

    source: str
    value_key: str
    entity_registry_enabled_default: bool = True


KNOWN_REALTIME_DESCRIPTIONS: dict[str, FoxESSSensorDescription] = {
    "generation_power": FoxESSSensorDescription(
        key="generation_power",
        source="realtime",
        value_key="generationPower",
        name="Generation Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    "pv_power": FoxESSSensorDescription(
        key="pv_power",
        source="realtime",
        value_key="pvPower",
        name="PV Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    "feedin_power": FoxESSSensorDescription(
        key="feedin_power",
        source="realtime",
        value_key="feedinPower",
        name="Feed-in Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
    ),
    "grid_consumption_power": FoxESSSensorDescription(
        key="grid_consumption_power",
        source="realtime",
        value_key="gridConsumptionPower",
        name="Grid Consumption Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
    ),
    "loads_power": FoxESSSensorDescription(
        key="loads_power",
        source="realtime",
        value_key="loadsPower",
        name="Load Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
    ),
    "charge_power": FoxESSSensorDescription(
        key="charge_power",
        source="realtime",
        value_key="chargePower",
        name="Battery Charge Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up",
    ),
    "discharge_power": FoxESSSensorDescription(
        key="discharge_power",
        source="realtime",
        value_key="dischargePower",
        name="Battery Discharge Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down",
    ),
    "battery_soc": FoxESSSensorDescription(
        key="battery_soc",
        source="realtime",
        value_key="SoC",
        name="Battery SOC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "battery_soc_1": FoxESSSensorDescription(
        key="battery_soc_1",
        source="realtime",
        value_key="SoC1",
        name="Battery SOC 1",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "battery_soc_2": FoxESSSensorDescription(
        key="battery_soc_2",
        source="realtime",
        value_key="SoC2",
        name="Battery SOC 2",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "battery_soh": FoxESSSensorDescription(
        key="battery_soh",
        source="realtime",
        value_key="SoH",
        name="Battery SOH",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-heart-variant",
        entity_registry_enabled_default=False,
    ),
    "battery_net_power": FoxESSSensorDescription(
        key="battery_net_power",
        source="realtime",
        value_key="invBatPower",
        name="Battery Net Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-sync",
    ),
    "residual_energy": FoxESSSensorDescription(
        key="residual_energy",
        source="realtime",
        value_key="residualEnergy",
        name="Residual Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery",
        entity_registry_enabled_default=False,
    ),
    "generation": FoxESSSensorDescription(
        key="generation",
        source="realtime",
        value_key="generation",
        name="Total Generation",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    "pv_energy_total": FoxESSSensorDescription(
        key="pv_energy_total",
        source="realtime",
        value_key="PVEnergyTotal",
        name="Total PV Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
    ),
    "feedin": FoxESSSensorDescription(
        key="feedin",
        source="realtime",
        value_key="feedin",
        name="Total Feed-in",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
    ),
    "feedin2": FoxESSSensorDescription(
        key="feedin2",
        source="realtime",
        value_key="feedin2",
        name="Total Feed-in (Meter 2)",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
        entity_registry_enabled_default=False,
    ),
    "grid_consumption": FoxESSSensorDescription(
        key="grid_consumption",
        source="realtime",
        value_key="gridConsumption",
        name="Total Grid Consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
    ),
    "grid_consumption2": FoxESSSensorDescription(
        key="grid_consumption2",
        source="realtime",
        value_key="gridConsumption2",
        name="Total Grid Consumption (Meter 2)",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
        entity_registry_enabled_default=False,
    ),
    "loads": FoxESSSensorDescription(
        key="loads",
        source="realtime",
        value_key="loads",
        name="Total Load Consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-lightning-bolt",
    ),
    "charge_energy_total": FoxESSSensorDescription(
        key="charge_energy_total",
        source="realtime",
        value_key="chargeEnergyToTal",
        name="Total Battery Charged",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up",
    ),
    "discharge_energy_total": FoxESSSensorDescription(
        key="discharge_energy_total",
        source="realtime",
        value_key="dischargeEnergyToTal",
        name="Total Battery Discharged",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down",
    ),
    "energy_throughput": FoxESSSensorDescription(
        key="energy_throughput",
        source="realtime",
        value_key="energyThroughput",
        name="Battery Throughput",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-sync",
        entity_registry_enabled_default=False,
    ),
    "running_state": FoxESSSensorDescription(
        key="running_state",
        source="realtime",
        value_key="runningState",
        name="Running State",
        icon="mdi:state-machine",
    ),
    "ambient_temperature": FoxESSSensorDescription(
        key="ambient_temperature",
        source="realtime",
        value_key="ambientTemp",
        name="Ambient Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "boost_temperature": FoxESSSensorDescription(
        key="boost_temperature",
        source="realtime",
        value_key="boostTemp",
        name="Boost Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "inverter_temperature": FoxESSSensorDescription(
        key="inverter_temperature",
        source="realtime",
        value_key="invTemp",
        name="Inverter Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "battery_temperature": FoxESSSensorDescription(
        key="battery_temperature",
        source="realtime",
        value_key="batTemperature",
        name="Battery Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "battery_temperature_2": FoxESSSensorDescription(
        key="battery_temperature_2",
        source="realtime",
        value_key="batTemperature2",
        name="Battery Temperature 2",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    "power_factor": FoxESSSensorDescription(
        key="power_factor",
        source="realtime",
        value_key="powerFactor",
        name="Power Factor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_registry_enabled_default=False,
    ),
    "api_requested_at": FoxESSSensorDescription(
        key="api_requested_at",
        source="meta",
        value_key="requested_at",
        name="Last Successful Update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
}

KNOWN_REPORT_DESCRIPTIONS: dict[str, FoxESSSensorDescription] = {
    "daily_generation": FoxESSSensorDescription(
        key="daily_generation",
        source="report",
        value_key="generation",
        name="Daily Generation",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    "daily_pv_energy_total": FoxESSSensorDescription(
        key="daily_pv_energy_total",
        source="report",
        value_key="PVEnergyTotal",
        name="Daily PV Energy Total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power-variant",
    ),
    "daily_feedin": FoxESSSensorDescription(
        key="daily_feedin",
        source="report",
        value_key="feedin",
        name="Daily Feed-in",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-export",
    ),
    "daily_grid_consumption": FoxESSSensorDescription(
        key="daily_grid_consumption",
        source="report",
        value_key="gridConsumption",
        name="Daily Grid Consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:transmission-tower-import",
    ),
    "daily_load_consumption": FoxESSSensorDescription(
        key="daily_load_consumption",
        source="report",
        value_key="loads",
        name="Daily Load Consumption",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-lightning-bolt",
    ),
    "daily_battery_charged": FoxESSSensorDescription(
        key="daily_battery_charged",
        source="report",
        value_key="chargeEnergyToTal",
        name="Daily Battery Charged",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up",
    ),
    "daily_battery_discharged": FoxESSSensorDescription(
        key="daily_battery_discharged",
        source="report",
        value_key="dischargeEnergyToTal",
        name="Daily Battery Discharged",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FoxESS sensors."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]

    entities: list[SensorEntity] = []
    for coordinator in coordinators.values():
        entities.extend(_build_entities_for_coordinator(coordinator))

    async_add_entities(entities)


def _build_entities_for_coordinator(
    coordinator: FoxESSDataUpdateCoordinator,
) -> list[SensorEntity]:
    entities: list[SensorEntity] = []
    entities.append(FoxESSSchedulerSensor(coordinator))
    entities.append(FoxESSGridNetPowerSensor(coordinator))

    for description in KNOWN_REALTIME_DESCRIPTIONS.values():
        entities.append(FoxESSOpenAPISensor(coordinator, description))

    for description in KNOWN_REPORT_DESCRIPTIONS.values():
        entities.append(FoxESSOpenAPISensor(coordinator, description))

    seen_dynamic_keys = {
        normalize_key(entity.entity_description.value_key)
        for entity in entities
        if isinstance(entity, FoxESSOpenAPISensor)
        and entity.entity_description.source == "realtime"
    }

    for raw_key, value in coordinator.data.realtime.items():
        normalized_key = normalize_key(raw_key)
        if normalized_key in seen_dynamic_keys:
            continue
        entities.append(
            FoxESSOpenAPISensor(
                coordinator,
                build_dynamic_realtime_description(raw_key, value),
            )
        )
        seen_dynamic_keys.add(normalized_key)

    for raw_key, value in coordinator.data.realtime.items():
        normalized_key = normalize_key(raw_key)
        if _PV_STRING_POWER_KEY_PATTERN.match(normalized_key) and value.get("unit") == "kW":
            entities.append(FoxESSPVStringEnergySensor(coordinator, raw_key))

    return entities


class FoxESSSchedulerSensor(CoordinatorEntity[FoxESSDataUpdateCoordinator], SensorEntity):
    """Read-only summary of the current FoxESS scheduler configuration."""

    _attr_has_entity_name = True
    _attr_name = "Schedule Status"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: FoxESSDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_scheduler"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        return (
            super().available
            and (
                self.coordinator.data.scheduler_supported is not None
                or self.coordinator.data.scheduler_snapshot is not None
            )
        )

    @property
    def native_value(self) -> str | None:
        enabled = self.coordinator.data.scheduler_enabled
        if enabled is True:
            return "enabled"
        if enabled is False:
            return "disabled"
        if self.coordinator.data.scheduler_supported is False:
            return "unsupported"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self.coordinator.data.scheduler_snapshot
        attributes: dict[str, Any] = {
            "scheduler_supported": self.coordinator.data.scheduler_supported,
        }
        if snapshot is None:
            return attributes

        result = snapshot.get("result")
        groups = result.get("groups") if isinstance(result, dict) else None
        properties = result.get("properties") if isinstance(result, dict) else None

        attributes["api_version"] = snapshot.get("version")
        if isinstance(result, dict):
            attributes["max_group_count"] = result.get("maxGroupCount")
            attributes["schedule_enabled"] = bool(result.get("enable")) if "enable" in result else None
        if isinstance(groups, list):
            attributes["group_count"] = len(groups)
            attributes["groups"] = groups
        if isinstance(properties, dict):
            work_mode = properties.get("workmode")
            if isinstance(work_mode, dict):
                attributes["available_work_modes"] = work_mode.get("enumList")

        return attributes


class FoxESSGridNetPowerSensor(CoordinatorEntity[FoxESSDataUpdateCoordinator], SensorEntity):
    """Derived net grid power where import is positive and export is negative."""

    _attr_has_entity_name = True
    _attr_name = "Grid Net Power"
    _attr_icon = "mdi:transmission-tower"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FoxESSDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_sn}_grid_net_power"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def native_value(self) -> float | None:
        import_power = _coerce_power_value(
            self.coordinator.data.realtime.get("gridConsumptionPower", {}).get("value")
        )
        export_power = _coerce_power_value(
            self.coordinator.data.realtime.get("feedinPower", {}).get("value")
        )

        if import_power is None and export_power is None:
            return None

        return (import_power or 0.0) - (export_power or 0.0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "positive_direction": "importing_from_grid",
            "negative_direction": "exporting_to_grid",
            "import_source": "gridConsumptionPower",
            "export_source": "feedinPower",
        }


class FoxESSOpenAPISensor(CoordinatorEntity[FoxESSDataUpdateCoordinator], SensorEntity):
    """Representation of a FoxESS sensor."""

    entity_description: FoxESSSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        description: FoxESSSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_sn}_{description.key}"
        self._attr_device_info = build_device_info(coordinator)
        self._attr_name = description.name
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        description = self.entity_description

        if description.source == "meta":
            return getattr(data, description.value_key)

        source_data = data.realtime if description.source == "realtime" else data.report
        item = source_data.get(description.value_key)
        if item is None:
            return None
        value = item.get("value")
        if description.value_key == "runningState":
            return _translate_running_state(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.source != "realtime":
            return None

        item = self.coordinator.data.realtime.get(self.entity_description.value_key)
        if item is None:
            return None

        attributes: dict[str, Any] = {}
        if item.get("unit"):
            attributes["api_unit"] = item["unit"]
        if item.get("name"):
            attributes["api_name"] = item["name"]
        if item.get("time"):
            attributes["data_updated_at"] = item["time"]
        if self.entity_description.value_key == "runningState":
            code = _coerce_running_state_code(item.get("value"))
            if code is not None:
                attributes["code"] = code
        return attributes or None


class FoxESSPVStringEnergySensor(
    CoordinatorEntity[FoxESSDataUpdateCoordinator], RestoreEntity, SensorEntity
):
    """Derived cumulative energy for an individual PV string."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:solar-power-variant"
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        source_key: str,
    ) -> None:
        """Initialize the derived PV string energy sensor."""
        super().__init__(coordinator)
        self._source_key = source_key
        self._source_normalized_key = normalize_key(source_key)
        self._attr_unique_id = (
            f"{coordinator.device_sn}_{self._source_normalized_key.removesuffix('_power')}_generated_energy"
        )
        self._attr_device_info = build_device_info(coordinator)
        self._attr_name = _build_pv_string_energy_name(source_key)
        self._native_value: float = 0.0
        self._last_power_kw: float | None = None
        self._last_sample_at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the previous total, then resume integrating from now."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._native_value = float(last_state.state)
            except (TypeError, ValueError):
                self._native_value = 0.0

        self._reset_integration_window()

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return super().available

    @property
    def native_value(self) -> float:
        """Return the cumulative generated energy."""
        return round(self._native_value, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the source realtime metric for traceability."""
        return {
            "source_power_key": self._source_normalized_key,
        }

    def _handle_coordinator_update(self) -> None:
        """Update the cumulative total from the latest power sample."""
        current_power_kw = self._get_current_power_kw()
        current_sample_at = self.coordinator.data.requested_at

        if (
            current_power_kw is not None
            and self._last_power_kw is not None
            and self._last_sample_at is not None
        ):
            elapsed_hours = (
                current_sample_at - self._last_sample_at
            ).total_seconds() / 3600
            if elapsed_hours > 0:
                average_power_kw = (self._last_power_kw + current_power_kw) / 2
                if average_power_kw >= 0:
                    self._native_value += average_power_kw * elapsed_hours

        self._last_power_kw = current_power_kw
        self._last_sample_at = current_sample_at if current_power_kw is not None else None
        super()._handle_coordinator_update()

    def _get_current_power_kw(self) -> float | None:
        """Return the current PV string power sample."""
        item = self.coordinator.data.realtime.get(self._source_key)
        value = item.get("value") if item is not None else None
        if value is None:
            return None

        try:
            power_kw = float(value)
        except (TypeError, ValueError):
            return None

        return power_kw if power_kw >= 0 else 0.0

    def _reset_integration_window(self) -> None:
        """Start a new integration window from the current coordinator sample."""
        self._last_power_kw = self._get_current_power_kw()
        self._last_sample_at = (
            self.coordinator.data.requested_at if self._last_power_kw is not None else None
        )


def build_device_info(coordinator: FoxESSDataUpdateCoordinator) -> DeviceInfo:
    """Build a Home Assistant device record."""
    detail = coordinator.data.detail
    serial = coordinator.device_sn
    model = detail.get("deviceType") or detail.get("model") or coordinator.device.device_type
    sw_parts = [
        part
        for part in (
            detail.get("masterVersion"),
            detail.get("managerVersion"),
            detail.get("slaveVersion"),
        )
        if part
    ]

    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        manufacturer="FoxESS",
        name=coordinator.device_name,
        model=model,
        serial_number=serial,
        sw_version=" / ".join(sw_parts) if sw_parts else None,
    )


def _build_pv_string_energy_name(source_key: str) -> str:
    """Return a friendly name for a derived PV string energy sensor."""
    source_name = prettify_key(source_key)
    if source_name.endswith(" Power"):
        source_name = source_name.removesuffix(" Power")
    return f"{source_name} Generated Energy"


def _coerce_power_value(value: Any) -> float | None:
    """Convert a power value to float when available."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_running_state_code(value: Any) -> int | None:
    """Convert running-state values to int where possible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _translate_running_state(value: Any) -> str | Any:
    """Convert FoxESS running-state codes into readable labels."""
    code = _coerce_running_state_code(value)
    if code is None:
        return value
    return _RUNNING_STATE_LABELS.get(code, f"Unknown ({code})")


def build_dynamic_realtime_description(
    raw_key: str,
    item: dict[str, Any],
) -> FoxESSSensorDescription:
    """Create a runtime sensor description for an API variable."""
    normalized_key = normalize_key(raw_key)
    unit = item.get("unit")

    kwargs: dict[str, Any] = {
        "key": normalized_key,
        "source": "realtime",
        "value_key": raw_key,
        "name": prettify_key(raw_key),
        "entity_registry_enabled_default": True,
    }

    if unit == "kW":
        kwargs["device_class"] = SensorDeviceClass.POWER
        kwargs["native_unit_of_measurement"] = UnitOfPower.KILO_WATT
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit == "kWh":
        kwargs["device_class"] = SensorDeviceClass.ENERGY
        kwargs["native_unit_of_measurement"] = UnitOfEnergy.KILO_WATT_HOUR
        if raw_key in {
            "generation",
            "PVEnergyTotal",
            "feedin",
            "feedin2",
            "gridConsumption",
            "gridConsumption2",
            "loads",
            "chargeEnergyToTal",
            "dischargeEnergyToTal",
            "energyThroughput",
        }:
            kwargs["state_class"] = SensorStateClass.TOTAL_INCREASING
    elif unit == "V":
        kwargs["device_class"] = SensorDeviceClass.VOLTAGE
        kwargs["native_unit_of_measurement"] = UnitOfElectricPotential.VOLT
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit == "A":
        kwargs["device_class"] = SensorDeviceClass.CURRENT
        kwargs["native_unit_of_measurement"] = UnitOfElectricCurrent.AMPERE
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit == "Hz":
        kwargs["device_class"] = SensorDeviceClass.FREQUENCY
        kwargs["native_unit_of_measurement"] = UnitOfFrequency.HERTZ
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit and unit.upper().endswith("C"):
        kwargs["device_class"] = SensorDeviceClass.TEMPERATURE
        kwargs["native_unit_of_measurement"] = UnitOfTemperature.CELSIUS
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit == "%":
        kwargs["native_unit_of_measurement"] = PERCENTAGE
        kwargs["state_class"] = SensorStateClass.MEASUREMENT
    elif unit == "ms":
        kwargs["native_unit_of_measurement"] = UnitOfTime.MILLISECONDS
        kwargs["entity_category"] = EntityCategory.DIAGNOSTIC
        kwargs["entity_registry_enabled_default"] = False

    return FoxESSSensorDescription(**kwargs)
