"""Config flow for the Entur public transport integration."""

from collections.abc import Mapping
from typing import Any, override

import probatio

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL, CONF_SHOW_ON_MAP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.translation import async_get_translations

from .api import (
    DIRECTION_SEPARATOR,
    EnturApiError,
    async_get_stop_name,
    async_get_stop_place,
    async_search_stops,
    fallback_name,
    line_code,
    line_directions,
    short_id,
    stop_lines,
)
from .const import (
    CONF_EXPAND_PLATFORMS,
    CONF_LEGACY_NAME,
    CONF_NUMBER_OF_DEPARTURES,
    CONF_OMIT_NON_BOARDING,
    CONF_QUAYS,
    CONF_SEARCH,
    CONF_STOP_ID,
    CONF_STOP_SENSOR,
    CONF_WHITELIST_LINES,
    CONF_YAML_QUAYS,
    DEFAULT_EXPAND_PLATFORMS,
    DEFAULT_NUMBER_OF_DEPARTURES,
    DEFAULT_OMIT_NON_BOARDING,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHOW_ON_MAP,
    DOMAIN,
    MAX_DEPARTURES,
    MAX_SCAN_INTERVAL,
    MIN_DEPARTURES,
    MIN_SCAN_INTERVAL,
)


def _multi_select(labels: dict[str, str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=value, label=label)
                for value, label in sorted(labels.items(), key=lambda item: item[1])
            ],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


async def _async_whole_line_label(hass: HomeAssistant) -> str:
    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    return translations.get(
        f"component.{DOMAIN}.selector.whole_line.options.label", "{code}"
    )


def _whole_line_label(template: str, code: str) -> str:
    return template.replace("{code}", code) if "{code}" in template else code


def departures_schema(
    stop_place: dict[str, Any],
    options: Mapping[str, Any],
    whole_line: str,
    registered: Mapping[str, str] | None = None,
) -> probatio.Schema:
    """Build the departure settings form for a stop place."""
    lines = line_directions(stop_place)
    # Directions that have a sensor stay on offer, so each can be kept alone.
    for choice, name in (registered or {}).items():
        lines.setdefault(choice, name)
    lines_with_directions = {value.partition(DIRECTION_SEPARATOR)[0] for value in lines}
    quays: dict[str, str] = {}
    known_lines = stop_lines(stop_place)
    for quay in stop_place["quays"]:
        code = quay["publicCode"] or short_id(quay["id"])
        quay_lines = sorted(
            {line["publicCode"] or line["name"] or "" for line in quay["lines"]}
        )
        quays[quay["id"]] = f"{code} – {', '.join(filter(None, quay_lines))}"
        for line in quay["lines"]:
            # A line with no directions to offer is chosen whole.
            if line["id"] not in lines_with_directions:
                lines[line["id"]] = _whole_line_label(whole_line, line_code(line))
    # Chosen values that Entur no longer lists stay, so the form still validates,
    # under the names the sensors get.
    for choice in options.get(CONF_WHITELIST_LINES, []):
        if choice not in lines:
            lines[choice] = (
                fallback_name(choice, lines, known_lines)
                if DIRECTION_SEPARATOR in choice
                else _whole_line_label(
                    whole_line,
                    line_code(
                        known_lines.get(choice, {"id": choice, "publicCode": None})
                    ),
                )
            )
    for quay_id in options.get(CONF_QUAYS, []):
        quays.setdefault(quay_id, quay_id)

    return probatio.Schema(
        {
            probatio.Optional(CONF_WHITELIST_LINES): _multi_select(lines),
            probatio.Optional(CONF_QUAYS): _multi_select(quays),
            probatio.Required(
                CONF_EXPAND_PLATFORMS, default=DEFAULT_EXPAND_PLATFORMS
            ): BooleanSelector(),
            probatio.Required(
                CONF_NUMBER_OF_DEPARTURES, default=DEFAULT_NUMBER_OF_DEPARTURES
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_DEPARTURES,
                    max=MAX_DEPARTURES,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Required(
                CONF_OMIT_NON_BOARDING, default=DEFAULT_OMIT_NON_BOARDING
            ): BooleanSelector(),
            probatio.Required(
                CONF_SHOW_ON_MAP, default=DEFAULT_SHOW_ON_MAP
            ): BooleanSelector(),
        }
    )


def departure_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return entry options from submitted departure settings."""
    return {
        CONF_WHITELIST_LINES: list(user_input.get(CONF_WHITELIST_LINES, [])),
        CONF_QUAYS: list(user_input.get(CONF_QUAYS, [])),
        CONF_EXPAND_PLATFORMS: user_input[CONF_EXPAND_PLATFORMS],
        CONF_NUMBER_OF_DEPARTURES: int(user_input[CONF_NUMBER_OF_DEPARTURES]),
        CONF_OMIT_NON_BOARDING: user_input[CONF_OMIT_NON_BOARDING],
        CONF_SHOW_ON_MAP: user_input[CONF_SHOW_ON_MAP],
    }


class EnturConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Entur."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._stops: dict[str, str] = {}
        self._stop_place: dict[str, Any] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> EnturOptionsFlow:
        """Return the options flow."""
        return EnturOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search for a stop place by name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._stops = await async_search_stops(
                    self.hass, user_input[CONF_SEARCH]
                )
            except EnturApiError:
                errors["base"] = "cannot_connect"
            else:
                if self._stops:
                    return await self.async_step_stop()
                errors[CONF_SEARCH] = "no_stops"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Required(CONF_SEARCH): TextSelector()}),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the matching stop places."""
        errors: dict[str, str] = {}
        if user_input is not None:
            stop_id = user_input[CONF_STOP_ID]
            await self.async_set_unique_id(stop_id)
            self._abort_if_unique_id_configured()
            try:
                stop_place = await async_get_stop_place(self.hass, stop_id)
            except EnturApiError:
                errors["base"] = "cannot_connect"
            else:
                if stop_place is None:
                    return self.async_abort(reason="stop_not_found")
                self._stop_place = stop_place
                return await self.async_step_departures()
        return self.async_show_form(
            step_id="stop",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_STOP_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=value, label=label)
                                for value, label in self._stops.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_departures(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose lines, platforms and how departures are shown."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._stop_place["name"],
                data={CONF_STOP_ID: self._stop_place["id"]},
                options=departure_options(user_input),
            )
        return self.async_show_form(
            step_id="departures",
            data_schema=departures_schema(
                self._stop_place, {}, await _async_whole_line_label(self.hass)
            ),
            description_placeholders={"stop": self._stop_place["name"]},
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import one stop place and its platforms from the YAML sensor platform."""
        stop_id = import_data[CONF_STOP_ID]
        quays = import_data[CONF_QUAYS]
        await self.async_set_unique_id(stop_id)
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, stop_id
        )
        if entry is None or entry.source == SOURCE_IGNORE:
            self._abort_if_unique_id_configured()
        else:
            # Only YAML platforms never seen before are added, so options chosen
            # since are kept. An entry without the record has seen them all.
            handled = entry.data.get(CONF_YAML_QUAYS)
            new = [] if handled is None else [q for q in quays if q not in handled]
            seen = handled if handled is not None else entry.options[CONF_QUAYS]
            data = {**entry.data, CONF_YAML_QUAYS: list(dict.fromkeys([*seen, *quays]))}
            options = {
                **entry.options,
                CONF_QUAYS: list(dict.fromkeys([*entry.options[CONF_QUAYS], *new])),
            }
            # A stop imported through its platforms alone gets its own sensor
            # once YAML lists it; expansion is turned on only if YAML asks.
            if import_data[CONF_STOP_SENSOR] and not entry.data.get(
                CONF_STOP_SENSOR, True
            ):
                data[CONF_STOP_SENSOR] = True
                options[CONF_EXPAND_PLATFORMS] = (
                    entry.options[CONF_EXPAND_PLATFORMS]
                    or import_data[CONF_EXPAND_PLATFORMS]
                )
            return self.async_update_reload_and_abort(
                entry,
                data=data,
                options=options,
                reason="already_configured",
                reload_even_if_entry_is_unchanged=False,
            )
        try:
            stop_place = await async_get_stop_name(self.hass, stop_id)
        except EnturApiError:
            return self.async_abort(reason="cannot_connect")
        if stop_place is None:
            return self.async_abort(reason="stop_not_found")
        return self.async_create_entry(
            title=stop_place["name"],
            data={
                CONF_STOP_ID: stop_place["id"],
                CONF_LEGACY_NAME: import_data[CONF_NAME],
                CONF_STOP_SENSOR: import_data[CONF_STOP_SENSOR],
                CONF_YAML_QUAYS: quays,
            },
            options=departure_options(import_data),
        )


class EnturOptionsFlow(OptionsFlowWithReload):
    """Change lines, platforms and departure settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **departure_options(user_input),
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                }
            )
        try:
            stop_place = await async_get_stop_place(
                self.hass, self.config_entry.data[CONF_STOP_ID]
            )
        except EnturApiError:
            return self.async_abort(reason="cannot_connect")
        if stop_place is None:
            return self.async_abort(reason="stop_not_found")
        prefix = f"{self.config_entry.data[CONF_STOP_ID]}{DIRECTION_SEPARATOR}"
        registered = {
            entity.unique_id.removeprefix(prefix): entity.original_name
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), self.config_entry.entry_id
            )
            if entity.unique_id.startswith(prefix) and entity.original_name
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                departures_schema(
                    stop_place,
                    self.config_entry.options,
                    await _async_whole_line_label(self.hass),
                    registered,
                ).extend(
                    {
                        probatio.Required(
                            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                        ): NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_SCAN_INTERVAL,
                                max=MAX_SCAN_INTERVAL,
                                step=1,
                                mode=NumberSelectorMode.BOX,
                                unit_of_measurement="s",
                            )
                        )
                    }
                ),
                self.config_entry.options,
            ),
            description_placeholders={"stop": stop_place["name"]},
        )
