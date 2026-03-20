"""Select platform for FoxESS writable settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator
from .sensor import build_device_info

OPTION_SELF_USE: Final = "Self-use"
OPTION_MODE_SCHEDULER: Final = "Mode Scheduler"

_API_VALUE_TO_OPTION: Final[dict[str, str]] = {
    "selfuse": OPTION_SELF_USE,
    "selfusemode": OPTION_SELF_USE,
    "modescheduler": OPTION_MODE_SCHEDULER,
    "scheduler": OPTION_MODE_SCHEDULER,
    "timemode": OPTION_MODE_SCHEDULER,
}


@dataclass(frozen=True, kw_only=True)
class FoxESSSelectDescription(SelectEntityDescription):
    """Description for a FoxESS select entity."""


SELECT_DESCRIPTIONS: tuple[FoxESSSelectDescription, ...] = (
    FoxESSSelectDescription(
        key="work_mode",
        name="Work Mode",
        icon="mdi:tune-variant",
        options=[OPTION_SELF_USE, OPTION_MODE_SCHEDULER],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FoxESS select entities."""
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, FoxESSDataUpdateCoordinator] = runtime_data["coordinators"]
    async_add_entities(
        FoxESSWorkModeSelect(coordinator, description)
        for coordinator in coordinators.values()
        for description in SELECT_DESCRIPTIONS
    )


class FoxESSWorkModeSelect(
    CoordinatorEntity[FoxESSDataUpdateCoordinator],
    RestoreEntity,
    SelectEntity,
):
    """A best-effort work mode selector."""

    entity_description: FoxESSSelectDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FoxESSDataUpdateCoordinator,
        description: FoxESSSelectDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_sn}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = build_device_info(coordinator)
        self._current_option: str | None = self._infer_current_option()

    async def async_added_to_hass(self) -> None:
        """Restore the last known option if FoxESS doesn't expose it directly."""
        await super().async_added_to_hass()
        if self._current_option is not None:
            return

        if (last_state := await self.async_get_last_state()) is None:
            return

        if last_state.state in self.options:
            self._current_option = last_state.state

    @property
    def current_option(self) -> str | None:
        return self._infer_current_option() or self._current_option

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported option: {option}")

        if option == OPTION_MODE_SCHEDULER:
            await self.coordinator.async_set_scheduler_enabled(True)
        else:
            await self.coordinator.async_set_scheduler_enabled(False)
        self._current_option = option
        self.async_write_ha_state()

    def _infer_current_option(self) -> str | None:
        if self.coordinator.data.scheduler_enabled is True:
            return OPTION_MODE_SCHEDULER
        if self.coordinator.data.scheduler_enabled is False:
            return OPTION_SELF_USE

        for candidate in (
            self.coordinator.data.work_mode,
            self.coordinator.data.detail.get("workMode"),
            self.coordinator.data.detail.get("WorkMode"),
            self.coordinator.data.realtime.get("workMode", {}).get("value"),
            self.coordinator.data.realtime.get("WorkMode", {}).get("value"),
        ):
            normalized = _normalize_mode_value(candidate)
            if normalized is not None:
                return normalized
        return None


def _normalize_mode_value(value: object) -> str | None:
    """Map FoxESS work-mode values to the limited HA options."""
    if not isinstance(value, str):
        return None
    return _API_VALUE_TO_OPTION.get(value.strip().replace(" ", "").lower())
