"""Data update coordinator for the Entur public transport integration."""

import logging
import time
from typing import override

from enturclient.dto import EstimatedCall, Place

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DIRECTION_SEPARATOR,
    EnturApiError,
    LineFilter,
    Patterns,
    async_get_departures,
    async_get_platforms,
    async_get_stop_place,
    fallback_name,
    line_directions,
    stop_lines,
)
from .const import (
    CONF_EXPAND_PLATFORMS,
    CONF_LEGACY_NAME,
    CONF_NUMBER_OF_DEPARTURES,
    CONF_OMIT_NON_BOARDING,
    CONF_QUAYS,
    CONF_STOP_ID,
    CONF_STOP_SENSOR,
    CONF_WHITELIST_LINES,
    DOMAIN,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type EnturConfigEntry = ConfigEntry[EnturCoordinator]


class EnturCoordinator(DataUpdateCoordinator[dict[str, Place]]):
    """Fetch departures for one stop place and its selected platforms."""

    config_entry: EnturConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EnturConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.stop_id: str = entry.data[CONF_STOP_ID]
        self.quays: list[str] = list(entry.options[CONF_QUAYS])
        self.line_filter = LineFilter(entry.options[CONF_WHITELIST_LINES])
        # Sensor name for each "line|direction" choice.
        self.line_directions: dict[str, str] = {}
        # Departures from the stop place by line and direction.
        self.direction_calls: dict[tuple[str, str], list[EstimatedCall]] = {}
        # Departures from the stop place, cancelled ones included.
        self.disruption_calls: list[EstimatedCall] = []
        # Emptied hourly for timetable changes and journeys not resolved before.
        self._patterns: Patterns = {}
        self._patterns_since = time.monotonic()

    @property
    def disruptions_id(self) -> str:
        """Return the unique id of the stop's disruption sensor."""
        return f"{self.stop_id}_disruptions"

    @property
    def device_name(self) -> str:
        """Return the stop's name, as the built-in YAML sensor named imported ones."""
        data = self.config_entry.data
        title = self.config_entry.title
        if not (legacy_name := data.get(CONF_LEGACY_NAME)):
            return title
        # Without the stop in YAML, the built-in names start from the platform.
        if not data.get(CONF_STOP_SENSOR, True) and self.quays:
            if place := self.data.get(self.quays[0]):
                title = place.raw["name"]
        return f"{legacy_name} {title}"

    @override
    async def _async_setup(self) -> None:
        # Sensors already made stay until the user deselects what they show, so
        # a quiet week or an error at Entur never removes one.
        options = self.config_entry.options
        registered = {
            entity.unique_id: entity.original_name
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.config_entry.entry_id
            )
        }
        try:
            if self.line_filter.line_ids:
                await self._async_find_line_directions(registered)
            if options[CONF_EXPAND_PLATFORMS]:
                platforms = await async_get_platforms(
                    self.hass,
                    self.stop_id,
                    self.line_filter,
                    options[CONF_OMIT_NON_BOARDING],
                    self._patterns,
                )
                platforms += [
                    unique_id
                    for unique_id in registered
                    if unique_id not in (self.stop_id, self.disruptions_id)
                    and DIRECTION_SEPARATOR not in unique_id
                ]
                self.quays = list(dict.fromkeys(self.quays + platforms))
        except EnturApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="update_failed"
            ) from err

    async def _async_find_line_directions(
        self, registered: dict[str, str | None]
    ) -> None:
        stop_place = await async_get_stop_place(self.hass, self.stop_id, self._patterns)
        known = line_directions(stop_place) if stop_place else {}
        prefix = f"{self.stop_id}{DIRECTION_SEPARATOR}"
        for unique_id, name in registered.items():
            if unique_id.startswith(prefix) and name:
                known.setdefault(unique_id.removeprefix(prefix), name)
        for choice in self.config_entry.options[CONF_WHITELIST_LINES]:
            if DIRECTION_SEPARATOR in choice:
                self.line_directions[choice] = known.get(choice) or fallback_name(
                    choice,
                    {**known, **self.line_directions},
                    stop_lines(stop_place) if stop_place else {},
                )
                continue
            # A line chosen without a direction gets a sensor per direction.
            for known_choice, name in known.items():
                if known_choice.partition(DIRECTION_SEPARATOR)[0] == choice:
                    self.line_directions[known_choice] = name

    @override
    async def _async_update_data(self) -> dict[str, Place]:
        options = self.config_entry.options
        if time.monotonic() - self._patterns_since > 3600:
            self._patterns.clear()
            self._patterns_since = time.monotonic()
        try:
            # The stop place is left out when no sensor shows its departures.
            show_stop = self.config_entry.data.get(CONF_STOP_SENSOR, True)
            places, disrupted = await async_get_departures(
                self.hass,
                self.stop_id,
                [self.stop_id] if show_stop or self.line_directions else [],
                self.quays,
                self.line_filter,
                int(options[CONF_NUMBER_OF_DEPARTURES]),
                options[CONF_OMIT_NON_BOARDING],
                self._patterns,
            )
        except EnturApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="update_failed"
            ) from err
        data = {place["id"]: Place(place, is_platform) for place, is_platform in places}
        self.disruption_calls = [EstimatedCall(call) for call in disrupted]
        self.direction_calls = {}
        if self.line_directions and (stop := data.get(self.stop_id)):
            for call in stop.estimated_calls:
                key = (call.line_id, call.raw["direction"])
                self.direction_calls.setdefault(key, []).append(call)
        return data
