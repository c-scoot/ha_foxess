"""Select platform for FoxESS writable settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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
    SelectEntity,
):
    """A work mode selector that treats FoxESS readback as authoritative."""

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

    @property
    def options(self) -> list[str]:
        return [option for option in _OPTION_ORDER if _OPTION_TO_KEY[option] in self._available_option_keys()]

    @property
    def current_option(self) -> str | None:
        return self._infer_current_option()

    async def async_select_option(self, option: str) -> None:
        option_key = _OPTION_TO_KEY.get(option)
        if option_key is None or option not in self.options:
            raise ValueError(f"Unsupported option: {option}")

        await self.coordinator.async_set_work_mode(option_key)

    def _infer_current_option(self) -> str | None:
        direct_work_mode = normalize_work_mode_option_key(self.coordinator.data.work_mode)
        if direct_work_mode is not None:
            return _OPTION_KEY_TO_OPTION[direct_work_mode]

        if self.coordinator.data.scheduler_enabled is True:
            return OPTION_MODE_SCHEDULER
        return None

    def _available_option_keys(self) -> set[str]:
        option_keys = {"self_use"}
        if (
            self.coordinator.data.scheduler_supported is True
            or self.coordinator.data.scheduler_enabled is not None
            or self.coordinator.data.scheduler_snapshot is not None
        ):
            option_keys.add("mode_scheduler")

        snapshot = self.coordinator.data.scheduler_snapshot
        result = snapshot.get("result") if isinstance(snapshot, dict) else None
        properties = result.get("properties") if isinstance(result, dict) else None
        work_mode = properties.get("workMode") if isinstance(properties, dict) else None
        if not isinstance(work_mode, dict) and isinstance(properties, dict):
            work_mode = properties.get("workmode")

        if isinstance(work_mode, dict):
            enum_list = work_mode.get("enumList")
            if isinstance(enum_list, list):
                for raw_value in enum_list:
                    option_key = normalize_work_mode_option_key(raw_value)
                    if option_key is not None:
                        option_keys.add(option_key)

        for candidate in self._reported_work_mode_values():
            option_key = normalize_work_mode_option_key(candidate)
            if option_key is not None:
                option_keys.add(option_key)
        return option_keys

    def _reported_work_mode_values(self) -> tuple[object, ...]:
        return (
            self.coordinator.data.work_mode,
            self.coordinator.data.detail.get("workMode"),
            self.coordinator.data.detail.get("WorkMode"),
            self.coordinator.data.realtime.get("workMode", {}).get("value"),
            self.coordinator.data.realtime.get("WorkMode", {}).get("value"),
        )
