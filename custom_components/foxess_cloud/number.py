"""Number platform for FoxESS writable settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .sensor import build_device_info
from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class FoxESSNumberDescription(NumberEntityDescription):
    """Description for a FoxESS number entity."""

    key_name: str


NUMBER_DESCRIPTIONS: tuple[FoxESSNumberDescription, ...] = (
    FoxESSNumberDescription(
        key="min_soc",
        key_name="min_soc",
        name="System Minimum SOC",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery-lock",
    ),
    FoxESSNumberDescription(
        key="min_soc_on_grid",
        key_name="min_soc_on_grid",
        name="Battery Cut-Off SOC",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery-charging-medium",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FoxESS number entities."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]
    async_add_entities(
        FoxESSNumberEntity(coordinator, description)
        for coordinator in coordinators.values()
        for description in NUMBER_DESCRIPTIONS
    )


class FoxESSNumberEntity(CoordinatorEntity[FoxESSDataUpdateCoordinator], NumberEntity):
    """A writable FoxESS number entity."""

    entity_description: FoxESSNumberDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        description: FoxESSNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_sn}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.battery_settings is not None
            and self.native_value is not None
        )

    @property
    def native_value(self) -> float | None:
        settings = self.coordinator.data.battery_settings
        if settings is None:
            return None
        return float(getattr(settings, self.entity_description.key_name))

    async def async_set_native_value(self, value: float) -> None:
        kwargs = {self.entity_description.key_name: int(round(value))}
        await self.coordinator.async_set_battery_soc_settings(**kwargs)
