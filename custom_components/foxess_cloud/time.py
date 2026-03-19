"""Time platform for FoxESS writable settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator
from .sensor import build_device_info


@dataclass(frozen=True, kw_only=True)
class FoxESSTimeDescription(TimeEntityDescription):
    """Description for a FoxESS time entity."""

    period: int
    field: str


TIME_DESCRIPTIONS: tuple[FoxESSTimeDescription, ...] = (
    FoxESSTimeDescription(
        key="charge_period_1_start",
        period=1,
        field="start",
        name="Force Charge Window 1 Start",
        icon="mdi:clock-start",
    ),
    FoxESSTimeDescription(
        key="charge_period_1_end",
        period=1,
        field="end",
        name="Force Charge Window 1 End",
        icon="mdi:clock-end",
    ),
    FoxESSTimeDescription(
        key="charge_period_2_start",
        period=2,
        field="start",
        name="Force Charge Window 2 Start",
        icon="mdi:clock-start",
    ),
    FoxESSTimeDescription(
        key="charge_period_2_end",
        period=2,
        field="end",
        name="Force Charge Window 2 End",
        icon="mdi:clock-end",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FoxESS time entities."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]
    async_add_entities(
        FoxESSChargePeriodTime(coordinator, description)
        for coordinator in coordinators.values()
        for description in TIME_DESCRIPTIONS
    )


class FoxESSChargePeriodTime(CoordinatorEntity[FoxESSDataUpdateCoordinator], TimeEntity):
    """A force-charge time entity."""

    entity_description: FoxESSTimeDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        description: FoxESSTimeDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_sn}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.charge_time_settings is not None

    @property
    def native_value(self) -> dt_time | None:
        settings = self.coordinator.data.charge_time_settings
        if settings is None:
            return None
        period = settings.period_1 if self.entity_description.period == 1 else settings.period_2
        return getattr(period, self.entity_description.field)

    async def async_set_value(self, value: dt_time) -> None:
        await self.coordinator.async_set_charge_period_time(
            self.entity_description.period,
            self.entity_description.field,
            value,
        )
