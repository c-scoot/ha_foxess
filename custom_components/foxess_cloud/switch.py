"""Switch platform for FoxESS writable settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator
from .sensor import build_device_info


@dataclass(frozen=True, kw_only=True)
class FoxESSSwitchDescription(SwitchEntityDescription):
    """Description for a FoxESS switch entity."""

    period: int


SWITCH_DESCRIPTIONS: tuple[FoxESSSwitchDescription, ...] = (
    FoxESSSwitchDescription(
        key="charge_period_1_enabled",
        period=1,
        name="Force Charge Period 1",
        icon="mdi:battery-clock",
    ),
    FoxESSSwitchDescription(
        key="charge_period_2_enabled",
        period=2,
        name="Force Charge Period 2",
        icon="mdi:battery-clock-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FoxESS switch entities."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]
    async_add_entities(
        FoxESSChargePeriodSwitch(coordinator, description)
        for coordinator in coordinators.values()
        for description in SWITCH_DESCRIPTIONS
    )


class FoxESSChargePeriodSwitch(
    CoordinatorEntity[FoxESSDataUpdateCoordinator],
    SwitchEntity,
):
    """A force-charge enable switch."""

    entity_description: FoxESSSwitchDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        description: FoxESSSwitchDescription,
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
    def is_on(self) -> bool | None:
        settings = self.coordinator.data.charge_time_settings
        if settings is None:
            return None
        period = settings.period_1 if self.entity_description.period == 1 else settings.period_2
        return period.enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_charge_period_enabled(self.entity_description.period, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_charge_period_enabled(self.entity_description.period, False)
