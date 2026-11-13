"""Requests to Entur and the line and direction filter."""

import asyncio
from collections import Counter
from collections.abc import Iterable, Mapping
from itertools import count
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_CLIENT_NAME, GEOCODER_URL, JOURNEY_PLANNER_URL

# Stops of each journey pattern by pattern id; None for one Entur cannot resolve.
type Patterns = dict[str, list[str | None] | None]

# A stored line choice is a line id, or "<line id>|<direction key>".
DIRECTION_SEPARATOR = "|"
# Look-ahead in seconds for departures and for the lines and directions offered.
WEEK = 604800

# Only what the sensors show, plus the ids that lead to the direction.
CALL_FRAGMENT = """
fragment callData on EstimatedCall {
  realtime
  aimedDepartureTime
  expectedDepartureTime
  destinationDisplay { frontText }
  stopPositionInPattern @include(if: $withDirection)
  serviceJourney {
    id @include(if: $withDirection)
    journeyPattern {
      id @include(if: $withDirection)
      directionType @include(if: $withDirection)
      line { id name transportMode publicCode }
    }
  }
}
"""

# Cancellation, message summaries for the whole trip, and where a cut-short trip ends.
DISRUPTION_FRAGMENT = """
fragment situationData on PtSituationElement {
  situationNumber
  summary { value language }
}
fragment disruptionData on EstimatedCall {
  ...callData
  cancellation
  quay { stopPlace { name } }
  situations { ...situationData }
  serviceJourneyEstimatedCalls {
    previous { situations { ...situationData } }
    next {
      cancellation
      quay { stopPlace { name } }
      situations { ...situationData }
    }
  }
}
"""

PATTERN_FIELDS = "journeyPattern { quays { stopPlace { id } } }"
PATTERNS_PER_QUERY = 20

DEPARTURES_QUERY = (
    """
query(
  $stop: String!,
  $stops: [String],
  $quays: [String],
  $whitelist: InputWhiteListed,
  $numberOfDepartures: Int = 2,
  $numberPerLine: Int,
  $timeRange: Int,
  $omitNonBoarding: Boolean = true,
  $arrivalDeparture: ArrivalDeparture = departures,
  $withDirection: Boolean = false) {
  stopPlaces(ids: $stops) {
    id
    name
    estimatedCalls(
      timeRange: $timeRange,
      whiteListed: $whitelist,
      arrivalDeparture: $arrivalDeparture,
      numberOfDepartures: $numberOfDepartures,
      numberOfDeparturesPerLineAndDestinationDisplay: $numberPerLine) {
      ...callData
    }
  }
  quays(ids: $quays) {
    id
    name
    publicCode
    latitude
    longitude
    estimatedCalls(
      timeRange: $timeRange,
      whiteListed: $whitelist,
      omitNonBoarding: $omitNonBoarding,
      numberOfDepartures: $numberOfDepartures,
      numberOfDeparturesPerLineAndDestinationDisplay: $numberPerLine) {
      ...callData
    }
  }
  disruptions: stopPlace(id: $stop) {
    estimatedCalls(
      timeRange: $timeRange,
      whiteListed: $whitelist,
      arrivalDeparture: $arrivalDeparture,
      numberOfDepartures: $numberOfDepartures,
      numberOfDeparturesPerLineAndDestinationDisplay: $numberPerLine,
      includeCancelledTrips: true) {
      ...disruptionData
    }
  }
}
"""
    + CALL_FRAGMENT
    + DISRUPTION_FRAGMENT
)

PLATFORMS_QUERY = (
    """
query(
  $id: String!,
  $whitelist: InputWhiteListed,
  $numberOfDepartures: Int = 1,
  $numberPerLine: Int,
  $timeRange: Int,
  $omitNonBoarding: Boolean = true,
  $withDirection: Boolean = false) {
  stopPlace(id: $id) {
    quays(filterByInUse: true) {
      id
      estimatedCalls(
        timeRange: $timeRange,
        numberOfDepartures: $numberOfDepartures,
        numberOfDeparturesPerLineAndDestinationDisplay: $numberPerLine,
        whiteListed: $whitelist,
        omitNonBoarding: $omitNonBoarding) {
        ...callData
      }
    }
  }
}
"""
    + CALL_FRAGMENT
)

STOP_PLACE_QUERY = (
    """
query($id: String!, $timeRange: Int, $withDirection: Boolean = true) {
  stopPlace(id: $id) {
    id
    name
    quays(filterByInUse: true) {
      id
      publicCode
      lines { id publicCode name }
    }
    estimatedCalls(
      timeRange: $timeRange,
      numberOfDepartures: 1000,
      numberOfDeparturesPerLineAndDestinationDisplay: 3,
      arrivalDeparture: departures) {
      ...callData
    }
  }
}
"""
    + CALL_FRAGMENT
)

SITUATION_QUERY = """
query($id: String!) {
  situation(situationNumber: $id) {
    situationNumber
    summary { value language }
    description { value language }
  }
}
"""

STOP_NAME_QUERY = "query($id: String!) { stopPlace(id: $id) { id name } }"

PARENT_QUERY = """
query($id: String!) { quay(id: $id) { stopPlace { id parent { id } } } }
"""


class EnturApiError(HomeAssistantError):
    """Error talking to the Entur API."""


async def _async_request(
    hass: HomeAssistant, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    try:
        async with async_get_clientsession(hass).request(
            method,
            url,
            headers={"ET-Client-Name": API_CLIENT_NAME},
            timeout=aiohttp.ClientTimeout(total=15),
            **kwargs,
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise EnturApiError from err


async def _async_graphql(
    hass: HomeAssistant,
    query: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    result = await _async_request(
        hass,
        "POST",
        JOURNEY_PLANNER_URL,
        json={"query": query, "variables": variables},
    )
    if "errors" in result:
        raise EnturApiError(str(result["errors"]))
    return result["data"]


async def async_search_stops(hass: HomeAssistant, text: str) -> dict[str, str]:
    """Return the id and label of every stop place matching the text."""
    result = await _async_request(
        hass,
        "GET",
        GEOCODER_URL,
        params={"text": text, "layers": "venue", "size": "20", "lang": "no"},
    )
    stops: dict[str, str] = {}
    for feature in result.get("features", []):
        properties = feature["properties"]
        if properties["id"].startswith("NSR:StopPlace:"):
            stops.setdefault(properties["id"], properties["label"])
    return stops


def _needs_stops(call: dict[str, Any]) -> bool:
    pattern = call["serviceJourney"]["journeyPattern"]
    return pattern["directionType"] in (None, "unknown")


async def _async_fetch_patterns(
    hass: HomeAssistant,
    journeys: dict[str, str],
    patterns: Patterns,
) -> None:
    names = [f"j{number}" for number in range(len(journeys))]
    query = "query({}) {{ {} }}".format(
        ", ".join(f"${name}: String!" for name in names),
        " ".join(
            f"{name}: serviceJourney(id: ${name}) {{ {PATTERN_FIELDS} }}"
            for name in names
        ),
    )
    data = await _async_graphql(
        hass, query, dict(zip(names, journeys.values(), strict=True))
    )
    for name, pattern_id in zip(names, journeys, strict=True):
        pattern = (data[name] or {}).get("journeyPattern")
        patterns[pattern_id] = pattern and [
            (quay["stopPlace"] or {}).get("id") for quay in pattern["quays"]
        ]


async def _async_add_directions(
    hass: HomeAssistant,
    calls: list[dict[str, Any]],
    patterns: Patterns,
) -> None:
    """Give every departure the key shared by all departures running its way.

    The key is Entur's direction type, or else the next stop. Vy sets the
    direction type to "unknown". The stops of a journey pattern are fetched
    once into patterns, keyed by pattern id.
    """
    missing: dict[str, str] = {}
    for call in calls:
        pattern_id = call["serviceJourney"]["journeyPattern"]["id"]
        if _needs_stops(call) and pattern_id not in patterns:
            missing.setdefault(pattern_id, call["serviceJourney"]["id"])
    items = list(missing.items())
    await asyncio.gather(
        *(
            _async_fetch_patterns(
                hass, dict(items[start : start + PATTERNS_PER_QUERY]), patterns
            )
            for start in range(0, len(items), PATTERNS_PER_QUERY)
        )
    )
    for call in calls:
        pattern = call["serviceJourney"]["journeyPattern"]
        if not _needs_stops(call):
            call["direction"] = pattern["directionType"]
            continue
        # The position holds when the train changes platform and on loop routes.
        stops = patterns.get(pattern["id"]) or []
        position = call["stopPositionInPattern"]
        call["direction"] = (
            stops[position + 1]
            if position is not None and position + 1 < len(stops)
            else None
        )


class LineFilter:
    """Chosen lines, each in both directions or in one."""

    def __init__(self, choices: Iterable[str]) -> None:
        """Read plain line ids as both directions."""
        self.lines: set[str] = set()
        self.directions: set[tuple[str, str]] = set()
        for choice in choices:
            line_id, separator, direction = choice.partition(DIRECTION_SEPARATOR)
            if separator:
                self.directions.add((line_id, direction))
            else:
                self.lines.add(line_id)

    @property
    def line_ids(self) -> list[str]:
        """Return every chosen line id."""
        return sorted(self.lines | {line_id for line_id, _ in self.directions})

    @property
    def is_directional(self) -> bool:
        """Return whether a line is limited to one direction."""
        return any(line_id not in self.lines for line_id, _ in self.directions)

    def wanted(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the wanted departures, whose direction keys are set."""
        if not self.is_directional:
            return calls
        return [
            call
            for call in calls
            if (line_id := call["serviceJourney"]["journeyPattern"]["line"]["id"])
            in self.lines
            or (line_id, call["direction"]) in self.directions
        ]

    def variables(self, number_of_departures: int) -> dict[str, Any]:
        """Return query variables for this filter."""
        if not self.line_ids:
            return {
                "whitelist": {"lines": []},
                "numberOfDepartures": number_of_departures,
            }
        # Each line and destination sign gets its own number within the week.
        # Where both directions of a ring line show one sign, they share it.
        return {
            "whitelist": {"lines": self.line_ids},
            "numberOfDepartures": 1000,
            "numberPerLine": number_of_departures,
            "withDirection": True,
        }


async def async_get_platforms(
    hass: HomeAssistant,
    stop_id: str,
    line_filter: LineFilter,
    omit_non_boarding: bool,
    patterns: Patterns,
) -> list[str]:
    """Return the platforms of a stop with wanted departures in the week ahead."""
    data = await _async_graphql(
        hass,
        PLATFORMS_QUERY,
        {
            "id": stop_id,
            "timeRange": WEEK,
            "omitNonBoarding": omit_non_boarding,
            **line_filter.variables(1),
        },
    )
    quays = data["stopPlace"]["quays"] if data["stopPlace"] else []
    if len(quays) < 2:
        return []
    if line_filter.line_ids:
        await _async_add_directions(
            hass, [call for quay in quays for call in quay["estimatedCalls"]], patterns
        )
    return [quay["id"] for quay in quays if line_filter.wanted(quay["estimatedCalls"])]


async def async_get_departures(
    hass: HomeAssistant,
    stop_id: str,
    stop_ids: list[str],
    quay_ids: list[str],
    line_filter: LineFilter,
    number_of_departures: int,
    omit_non_boarding: bool,
    patterns: Patterns,
) -> tuple[list[tuple[dict[str, Any], bool]], list[dict[str, Any]]]:
    """Return the stops and platforms with their wanted departures.

    Also returns the stop place's wanted departures with cancelled trips and
    disruption fields, kept apart so they never change the others.
    """
    data = await _async_graphql(
        hass,
        DEPARTURES_QUERY,
        {
            "stop": stop_id,
            "stops": stop_ids,
            "quays": quay_ids,
            "timeRange": WEEK,
            "omitNonBoarding": omit_non_boarding,
            # Stop places take arrivalDeparture in place of omitNonBoarding.
            "arrivalDeparture": "departures" if omit_non_boarding else "both",
            **line_filter.variables(number_of_departures),
        },
    )
    places = [(place, False) for place in data["stopPlaces"] if place] + [
        (place, True) for place in data["quays"] if place
    ]
    disrupted = (data["disruptions"] or {}).get("estimatedCalls", [])
    if line_filter.line_ids:
        await _async_add_directions(
            hass,
            [call for place, _ in places for call in place["estimatedCalls"]]
            + disrupted,
            patterns,
        )
    for place, _ in places:
        place["estimatedCalls"] = line_filter.wanted(place["estimatedCalls"])
    return places, line_filter.wanted(disrupted)


async def async_get_stop_place(
    hass: HomeAssistant,
    stop_id: str,
    patterns: Patterns | None = None,
) -> dict[str, Any] | None:
    """Return the stop place with its platforms, lines and coming week's calls."""
    data = await _async_graphql(
        hass, STOP_PLACE_QUERY, {"id": stop_id, "timeRange": WEEK}
    )
    if stop_place := data["stopPlace"]:
        await _async_add_directions(
            hass, stop_place["estimatedCalls"], {} if patterns is None else patterns
        )
    return stop_place


async def async_get_stop_name(
    hass: HomeAssistant, stop_id: str
) -> dict[str, str] | None:
    """Return the id and name of a stop place."""
    data = await _async_graphql(hass, STOP_NAME_QUERY, {"id": stop_id})
    return data["stopPlace"]


async def async_get_situation(
    hass: HomeAssistant, situation_id: str
) -> dict[str, Any] | None:
    """Return a situation message with its summary and description."""
    data = await _async_graphql(hass, SITUATION_QUERY, {"id": situation_id})
    return data["situation"]


async def async_get_parent_stop_ids(hass: HomeAssistant, quay_id: str) -> list[str]:
    """Return the stop place of a platform, then the stop place holding that."""
    data = await _async_graphql(hass, PARENT_QUERY, {"id": quay_id})
    stop_ids: list[str] = []
    stop_place = (data["quay"] or {}).get("stopPlace")
    while stop_place:
        stop_ids.append(stop_place["id"])
        stop_place = stop_place.get("parent")
    return stop_ids


NORWEGIAN = {"no", "nb", "nob"}


def text(texts: list[dict[str, str]]) -> str | None:
    """Return the Norwegian text, else the first."""
    return next(
        (item["value"] for item in texts if item["language"] in NORWEGIAN),
        texts[0]["value"] if texts else None,
    )


def short_id(entur_id: str) -> str:
    """Return the last part of an Entur id, such as R13 for VYG:Line:R13."""
    return entur_id.rsplit(":", maxsplit=1)[-1]


def line_code(line: dict[str, Any]) -> str:
    """Return how a line is shown: its public code, else its name or id."""
    return line["publicCode"] or line.get("name") or short_id(line["id"])


def stop_lines(stop_place: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return every line serving the stop place's platforms, by line id."""
    return {line["id"]: line for quay in stop_place["quays"] for line in quay["lines"]}


def fallback_name(
    choice: str, names: Mapping[str, str], lines: Mapping[str, dict[str, Any]]
) -> str:
    """Return "<line> <n>" for a direction with nothing to name it by.

    n is the first number no other direction of the line uses in names.
    """
    line_id = choice.partition(DIRECTION_SEPARATOR)[0]
    code = line_code(lines.get(line_id, {"id": line_id, "publicCode": None}))
    used = {
        name
        for other, name in names.items()
        if other.partition(DIRECTION_SEPARATOR)[0] == line_id
    }
    return next(name for number in count(1) if (name := f"{code} {number}") not in used)


def line_directions(stop_place: dict[str, Any]) -> dict[str, str]:
    """Return a label such as "R13 → Dal / Eidsvoll" for every line choice."""
    found: dict[str, tuple[str, Counter[str]]] = {}
    for call in stop_place["estimatedCalls"]:
        if (direction := call["direction"]) is None:
            continue
        line = call["serviceJourney"]["journeyPattern"]["line"]
        _, texts = found.setdefault(
            f"{line['id']}{DIRECTION_SEPARATOR}{direction}",
            (line_code(line), Counter()),
        )
        if text := (call["destinationDisplay"] or {}).get("frontText"):
            texts[text.strip()] += 1
    # Destinations most frequent first.
    return {
        choice: " → ".join(
            filter(None, (code, " / ".join(text for text, _ in texts.most_common())))
        )
        for choice, (code, texts) in found.items()
    }
