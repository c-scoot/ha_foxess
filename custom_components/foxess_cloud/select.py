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

from .api import normalize_work_mode_option_key
from .const import DOMAIN
from .coordinator import FoxESSDataUpdateCoordinator
from .sensor import build_device_info

OPTION_SELF_USE: Final = "Self-use"
OPTION_FEED_IN_PRIORITY: Final = "Feed-in Priority"
OPTION_BACKUP: Final = "Backup"
OPTION_MODE_SCHEDULER: Final = "Mode Scheduler"
_OPTION_TO_KEY: Final[dict[str, str]] = {
    OPTION_SELF_USE: "self_use",
    OPTION_FEED_IN_PRIORITY: "feed_in_priority",
    OPTION_BACKUP: "backup",
    OPTION_MODE_SCHEDULER: "mode_scheduler",
}
_OPTION_KEY_TO_OPTION: Final[dict[str, str]] = {value: key for key, value in _OPTION_TO_KEY.items()}
_OPTION_ORDER: Final[tuple[str, ...]] = tuple(_OPTION_TO_KEY)


@dataclass(frozen=True, kw_only=True)
class FoxESSSelectDescription(SelectEntityDescription):
    """Description for a FoxESS select entity."""


SELECT_DESCRIPTIONS: tuple[FoxESSSelectDescription, ...] = (
    FoxESSSelectDescription(
        key="work_mode",
        name="Work Mode",
        icon="mdi:tune-variant",
        options=list(_OPTION_ORDER),
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
    """A best-effort work mode selector with scheduler-aware handling."""

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

        if last_state.state in _OPTION_TO_KEY:
            self._current_option = last_state.state

    @property
    def options(self) -> list[str]:
        return [option for option in _OPTION_ORDER if _OPTION_TO_KEY[option] in self._available_option_keys()]

    @property
    def current_option(self) -> str | None:
        return self._current_option or self._infer_current_option()

    async def async_select_option(self, option: str) -> None:
        option_key = _OPTION_TO_KEY.get(option)
        if option_key is None or option not in self.options:
            raise ValueError(f"Unsupported option: {option}")

        await self.coordinator.async_set_work_mode(option_key)
        self._current_option = option
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Sync the displayed option back to the latest cloud state."""
        self._current_option = self._infer_current_option() or self._current_option
        super()._handle_coordinator_update()

    def _infer_current_option(self) -> str | None:
        if self.coordinator.data.scheduler_enabled is True:
            return OPTION_MODE_SCHEDULER

        for candidate in self._reported_work_mode_values():
            option_key = normalize_work_mode_option_key(candidate)
            if option_key is None or option_key == "mode_scheduler":
                continue
            return _OPTION_KEY_TO_OPTION[option_key]
        return None

    def _available_option_keys(self) -> set[str]:
        option_keys = {"self_use", "mode_scheduler"}
        has_explicit_enum_list = False
        snapshot = self.coordinator.data.scheduler_snapshot
        result = snapshot.get("result") if isinstance(snapshot, dict) else None
        properties = result.get("properties") if isinstance(result, dict) else None
        work_mode = properties.get("workMode") if isinstance(properties, dict) else None
        if not isinstance(work_mode, dict) and isinstance(properties, dict):
            work_mode = properties.get("workmode")

        if isinstance(work_mode, dict):
            enum_list = work_mode.get("enumList")
            if isinstance(enum_list, list):
                has_explicit_enum_list = True
                for raw_value in enum_list:
                    option_key = normalize_work_mode_option_key(raw_value)
                    if option_key is not None:
                        option_keys.add(option_key)

        for candidate in self._reported_work_mode_values():
            option_key = normalize_work_mode_option_key(candidate)
            if option_key is not None:
                option_keys.add(option_key)

        if not has_explicit_enum_list and option_keys == {"self_use", "mode_scheduler"}:
            option_keys.update({"feed_in_priority", "backup"})
        return option_keys

    def _reported_work_mode_values(self) -> tuple[object, ...]:
        return (
            self.coordinator.data.work_mode,
            self.coordinator.data.detail.get("workMode"),
            self.coordinator.data.detail.get("WorkMode"),
            self.coordinator.data.realtime.get("workMode", {}).get("value"),
            self.coordinator.data.realtime.get("WorkMode", {}).get("value"),
        )
