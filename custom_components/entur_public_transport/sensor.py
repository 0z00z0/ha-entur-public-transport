"""Real-time information about public transport departures in Norway."""

import asyncio
from datetime import datetime
import json
from typing import Any, override

from enturclient.dto import EstimatedCall, Place
import probatio

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_SHOW_ON_MAP,
    UnitOfTime,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import (
    DIRECTION_SEPARATOR,
    EnturApiError,
    async_get_parent_stop_ids,
    short_id,
    text,
)
from .const import (
    CONF_EXPAND_PLATFORMS,
    CONF_LEGACY_NAME,
    CONF_NUMBER_OF_DEPARTURES,
    CONF_OMIT_NON_BOARDING,
    CONF_QUAYS,
    CONF_STOP_ID,
    CONF_STOP_IDS,
    CONF_STOP_SENSOR,
    CONF_WHITELIST_LINES,
    DEFAULT_EXPAND_PLATFORMS,
    DEFAULT_NAME,
    DEFAULT_NUMBER_OF_DEPARTURES,
    DEFAULT_OMIT_NON_BOARDING,
    DEFAULT_SHOW_ON_MAP,
    DOMAIN,
    MAX_DEPARTURES,
    MIN_DEPARTURES,
)
from .coordinator import EnturConfigEntry, EnturCoordinator

# The YAML platform of the built-in integration, imported into config entries.
PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        probatio.Required(CONF_STOP_IDS): probatio.All(
            probatio.EnsureList(), [cv.string]
        ),
        probatio.Optional(
            CONF_EXPAND_PLATFORMS, default=DEFAULT_EXPAND_PLATFORMS
        ): cv.boolean,
        probatio.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        probatio.Optional(CONF_SHOW_ON_MAP, default=DEFAULT_SHOW_ON_MAP): cv.boolean,
        probatio.Optional(CONF_WHITELIST_LINES, default=[]): probatio.EnsureList(),
        probatio.Optional(
            CONF_OMIT_NON_BOARDING, default=DEFAULT_OMIT_NON_BOARDING
        ): cv.boolean,
        probatio.Optional(
            CONF_NUMBER_OF_DEPARTURES, default=DEFAULT_NUMBER_OF_DEPARTURES
        ): probatio.All(
            cv.positive_int, probatio.Range(min=MIN_DEPARTURES, max=MAX_DEPARTURES)
        ),
    }
)
BREAKS_IN_HA_VERSION = "2027.6.0"
IMPORT_ISSUES = {"cannot_connect", "stop_not_found"}
IMPORT_LOCK = f"{DOMAIN}_import_lock"

DEFAULT_ICON = "mdi:bus"
ICONS = {
    "air": "mdi:airplane",
    "bus": "mdi:bus",
    "metro": "mdi:subway",
    "rail": "mdi:train",
    "tram": "mdi:tram",
    "water": "mdi:ferry",
}

# Field of departure_<n>_<field> and its key in the departures JSON.
JSON_KEYS = {
    "route": "r",
    "route_id": "id",
    "aimed": "a",
    "expected": "e",
    "delay": "d",
    "realtime": "rt",
}


# Keys of an affected departure and of a situation in the disruptions JSON.
DISRUPTION_JSON_KEYS = ("l", "dir", "to", "a", "e", "s", "end")
SITUATION_JSON_KEYS = ("sum", "l", "ids")


def due_in_minutes(timestamp: datetime) -> int:
    """Get the time in minutes from a timestamp."""
    return int((timestamp - dt_util.now()).total_seconds() / 60)


def _parse_time(value: str | None) -> datetime | None:
    if not value or (parsed := dt_util.parse_datetime(value)) is None:
        return None
    return dt_util.as_local(parsed)


def departure_fields(call: EstimatedCall) -> dict[str, Any]:
    """Return route, times, delay and real-time flag of one departure."""
    raw = call.raw
    realtime = bool(raw.get("realtime"))
    aimed = _parse_time(raw.get("aimedDepartureTime"))
    expected = _parse_time(raw.get("expectedDepartureTime"))
    if expected is None and not realtime:
        expected = aimed
    delay = None
    if aimed is not None and expected is not None:
        # Truncated toward zero, so a departure is late from 60 seconds onwards.
        delay = int((expected - aimed).total_seconds() / 60)
    line = raw.get("serviceJourney", {}).get("journeyPattern", {}).get("line", {})
    code = line.get("publicCode")
    front = destination_text(raw)
    return {
        "route": " ".join(p for p in (code, front) if p) or None,
        "route_id": line.get("id"),
        "aimed": aimed.isoformat() if aimed else None,
        "expected": expected.isoformat() if expected else None,
        "delay": delay,
        "realtime": realtime,
    }


def destination_text(raw: dict[str, Any]) -> str | None:
    """Return the destination text, or None where Entur gives no destination."""
    return (raw.get("destinationDisplay") or {}).get("frontText")


def disruption_status(call: EstimatedCall) -> tuple[str, str | None] | None:
    """Return "cancelled" or "cut_short", the latter with the last stop served."""
    raw = call.raw
    if raw["cancellation"]:
        return "cancelled", None
    later = raw["serviceJourneyEstimatedCalls"]["next"]
    if not later or not later[-1]["cancellation"]:
        return None
    served = [stop for stop in later if not stop["cancellation"]] or [raw]
    return "cut_short", ((served[-1]["quay"] or {}).get("stopPlace") or {}).get("name")


def trip_situations(call: EstimatedCall) -> list[dict[str, Any]]:
    """Return the situations of every call of the trip."""
    calls = call.raw["serviceJourneyEstimatedCalls"]
    return [
        situation
        for trip_call in (*calls["previous"], call.raw, *calls["next"])
        for situation in trip_call["situations"]
    ]


def _departure_time(departure: dict[str, Any]) -> datetime | None:
    time = departure["expected"] or departure["aimed"]
    return dt_util.parse_datetime(time) if time else None


def _clock(departure: dict[str, Any]) -> str | None:
    time = _departure_time(departure)
    return time.strftime("%H:%M") if time else None


def legacy_attributes(departures: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the attributes of the YAML sensor, with its names and meaning."""
    if not departures:
        return {}
    first = departures[0]
    attributes: dict[str, Any] = {
        "route": first["route"],
        "route_id": first["route_id"],
        "due_at": _clock(first),
        "real_time": first["realtime"],
        "delay": first["delay"] or 0,
    }
    if len(departures) < 2:
        return attributes
    second = departures[1]
    time = _departure_time(second)
    attributes |= {
        "next_route": second["route"],
        "next_route_id": second["route_id"],
        "next_due_at": _clock(second),
        "next_due_in": f"{due_in_minutes(time)} min" if time else None,
        "next_real_time": second["realtime"],
        "next_delay": second["delay"] or 0,
    }
    for number, departure in enumerate(departures[2:], start=3):
        attributes[f"departure_#{number}"] = (
            f"{'' if departure['realtime'] else 'ca. '}"
            f"{_clock(departure)} {departure['route']}"
        )
    return attributes


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Import the YAML sensor platform into config entries."""
    # Several YAML blocks for one stop are imported one after the other.
    async with hass.data.setdefault(IMPORT_LOCK, asyncio.Lock()):
        await _async_import(hass, config)


async def _async_import(hass: HomeAssistant, config: ConfigType) -> None:
    yaml_ids = config[CONF_STOP_IDS]
    failed: dict[str, str] = {}
    # One entry per stop place, holding the YAML platforms that belong to it.
    stops: dict[str, list[str]] = {}
    for place_id in yaml_ids:
        if "Quay" not in place_id:
            stops.setdefault(place_id, [])
            continue
        try:
            parents = await async_get_parent_stop_ids(hass, place_id)
        except EnturApiError:
            failed[place_id] = "cannot_connect"
            continue
        if not parents:
            failed[place_id] = "stop_not_found"
            continue
        stop_id = next((stop for stop in parents if stop in yaml_ids), parents[0])
        stops.setdefault(stop_id, []).append(place_id)

    for stop_id, quays in stops.items():
        # The built-in sensor has a stop sensor and platforms only when the
        # stop itself is in YAML.
        in_yaml = stop_id in yaml_ids
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                **config,
                CONF_STOP_ID: stop_id,
                CONF_QUAYS: quays,
                CONF_STOP_SENSOR: in_yaml,
                CONF_EXPAND_PLATFORMS: in_yaml and config[CONF_EXPAND_PLATFORMS],
            },
        )
        if (
            result["type"] is FlowResultType.ABORT
            and result["reason"] != "already_configured"
        ):
            for place_id in [stop_id, *quays] if in_yaml else quays:
                failed[place_id] = result["reason"]

    for place_id in yaml_ids:
        if place_id not in failed:
            ir.async_delete_issue(hass, DOMAIN, f"deprecated_yaml_import_{place_id}")
    for place_id, reason in failed.items():
        issue = reason if reason in IMPORT_ISSUES else "unknown"
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"deprecated_yaml_import_{place_id}",
            breaks_in_ha_version=BREAKS_IN_HA_VERSION,
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key=f"deprecated_yaml_import_issue_{issue}",
            translation_placeholders={"domain": DOMAIN, "stop_id": place_id},
        )
    if not failed:
        ir.async_create_issue(
            hass,
            HOMEASSISTANT_DOMAIN,
            f"deprecated_yaml_{DOMAIN}",
            breaks_in_ha_version=BREAKS_IN_HA_VERSION,
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_yaml",
            translation_placeholders={"domain": DOMAIN, "integration_title": "Entur"},
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnturConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up departure sensors for a stop, its platforms and line directions."""
    coordinator = entry.runtime_data
    entities: list[EnturSensor] = [
        EnturPublicTransportSensor(coordinator, place_id)
        for place_id in (
            [coordinator.stop_id] if entry.data.get(CONF_STOP_SENSOR, True) else []
        )
        + coordinator.quays
    ]
    entities.extend(
        EnturLineDirectionSensor(coordinator, choice, name)
        for choice, name in coordinator.line_directions.items()
    )
    entities.append(EnturDisruptionSensor(coordinator))

    # Deselected platforms and lines leave their sensors behind otherwise.
    unique_ids = {entity.unique_id for entity in entities}
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in unique_ids:
            registry.async_remove(entity.entity_id)

    async_add_entities(entities)


class EnturSensor(CoordinatorEntity[EnturCoordinator], SensorEntity):
    """Next departures from a stop place or platform."""

    _attr_attribution = "Data provided by entur.org under NLOD"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement: str | None = UnitOfTime.MINUTES
    # Departure data changes on every poll; recording it adds a row per poll.
    _unrecorded_attributes = frozenset(
        {
            "departures",
            *(
                f"departure_{number}_{field}"
                for number in range(1, MAX_DEPARTURES + 1)
                for field in JSON_KEYS
            ),
        }
    )

    def __init__(self, coordinator: EnturCoordinator, place_id: str) -> None:
        """Initialise the sensor as part of the stop's device."""
        super().__init__(coordinator)
        self._place_id = place_id
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, coordinator.stop_id)},
            manufacturer="Entur",
            name=coordinator.device_name,
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update()
        super()._handle_coordinator_update()

    def _calls(self, place: Place | None) -> list[EstimatedCall]:
        return place.estimated_calls if place else []

    def _update(self) -> None:
        place = self.coordinator.data.get(self._place_id)
        options = self.coordinator.config_entry.options
        count = int(options[CONF_NUMBER_OF_DEPARTURES])
        calls = self._calls(place)[:count]
        departures = [departure_fields(call) for call in calls]

        attributes: dict[str, Any] = {"stop_id": self._place_id}
        if place and options[CONF_SHOW_ON_MAP] and place.latitude and place.longitude:
            attributes[CONF_LATITUDE] = place.latitude
            attributes[CONF_LONGITUDE] = place.longitude
        attributes |= legacy_attributes(departures)
        # Every slot is present, so a consumer never keeps a stale value.
        for number in range(1, count + 1):
            fields = departures[number - 1] if number <= len(departures) else {}
            for field in JSON_KEYS:
                attributes[f"departure_{number}_{field}"] = fields.get(field)
        attributes["departures"] = json.dumps(
            [{JSON_KEYS[k]: v for k, v in d.items()} for d in departures],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._attr_extra_state_attributes = attributes

        self._attr_native_value = None
        self._attr_icon = DEFAULT_ICON
        if not calls:
            return
        self._attr_icon = ICONS.get(calls[0].transport_mode, DEFAULT_ICON)
        if first_time := _departure_time(departures[0]):
            self._attr_native_value = due_in_minutes(first_time)


class EnturPublicTransportSensor(EnturSensor):
    """Next departures from a stop place or one of its platforms."""

    def __init__(self, coordinator: EnturCoordinator, place_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, place_id)
        self._attr_unique_id = place_id
        place = coordinator.data.get(place_id)
        code = str((place and place.public_code) or short_id(place_id))
        if place_id == coordinator.stop_id:
            self._attr_name = None
        elif coordinator.config_entry.data.get(CONF_LEGACY_NAME):
            # The name the built-in YAML sensor gave, in every language.
            self._attr_name = f"Platform {code}"
        else:
            self._attr_translation_key = "platform"
            self._attr_translation_placeholders = {"code": code}
        self._update()


class EnturLineDirectionSensor(EnturSensor):
    """Next departures from the stop place on one line in one direction."""

    def __init__(self, coordinator: EnturCoordinator, choice: str, name: str) -> None:
        """Initialise the sensor for a "line|direction" choice."""
        super().__init__(coordinator, coordinator.stop_id)
        self._line_id, _, self._direction = choice.partition(DIRECTION_SEPARATOR)
        # Ids only, so a changed destination text never changes the entity.
        self._attr_unique_id = f"{coordinator.stop_id}{DIRECTION_SEPARATOR}{choice}"
        self._attr_name = name
        self._update()

    @override
    def _calls(self, place: Place | None) -> list[EstimatedCall]:
        return self.coordinator.direction_calls.get(
            (self._line_id, self._direction), []
        )


class EnturDisruptionSensor(EnturSensor):
    """Cancelled and cut-short departures from the stop place, with messages."""

    _attr_native_unit_of_measurement = None
    _attr_translation_key = "disruptions"
    _unrecorded_attributes = frozenset(
        {"affected_departures", "situations", "disruptions"}
    )

    def __init__(self, coordinator: EnturCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, coordinator.stop_id)
        self._attr_unique_id = coordinator.disruptions_id
        self._update()

    @override
    def _update(self) -> None:
        affected: list[dict[str, Any]] = []
        situations: dict[str | None, dict[str, Any]] = {}
        for call in self.coordinator.disruption_calls:
            line = call.line_public_code
            # Entur repeats one message under a new id per trip.
            for situation in trip_situations(call):
                summary = text(situation["summary"])
                found = situations.setdefault(
                    summary, {"summary": summary, "lines": [], "ids": []}
                )
                if line not in found["lines"]:
                    found["lines"].append(line)
                if situation["situationNumber"] not in found["ids"]:
                    found["ids"].append(situation["situationNumber"])
            if (status := disruption_status(call)) is None:
                continue
            fields = departure_fields(call)
            # Entur lists cancelled departures for a while after they were due.
            if (when := _departure_time(fields)) and when <= dt_util.now():
                continue
            choice = f"{call.line_id}{DIRECTION_SEPARATOR}{call.raw.get('direction')}"
            affected.append(
                {
                    "line": line,
                    "direction": self.coordinator.line_directions.get(choice),
                    "destination": destination_text(call.raw),
                    "aimed": fields["aimed"],
                    "expected": fields["expected"],
                    "status": status[0],
                    "last_stop": status[1],
                }
            )
        self._attr_native_value = len(affected)
        self._attr_extra_state_attributes = {
            "affected_departures": affected,
            "situations": list(situations.values()),
            "disruptions": json.dumps(
                {
                    "d": [
                        dict(zip(DISRUPTION_JSON_KEYS, item.values(), strict=True))
                        for item in affected
                    ],
                    "m": [
                        dict(zip(SITUATION_JSON_KEYS, item.values(), strict=True))
                        for item in situations.values()
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
