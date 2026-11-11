"""Constants for the Entur public transport integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "entur_public_transport"
PLATFORMS = [Platform.SENSOR]
SCAN_INTERVAL = timedelta(seconds=45)

API_CLIENT_NAME = "0z0-ha_entur_public_transport"
GEOCODER_URL = "https://api.entur.io/geocoder/v1/autocomplete"
JOURNEY_PLANNER_URL = "https://api.entur.io/journey-planner/v3/graphql"

CONF_STOP_ID = "stop_id"
CONF_QUAYS = "quays"
CONF_SEARCH = "search"
CONF_EXPAND_PLATFORMS = "expand_platforms"
CONF_WHITELIST_LINES = "line_whitelist"
CONF_OMIT_NON_BOARDING = "omit_non_boarding"
CONF_NUMBER_OF_DEPARTURES = "number_of_departures"

# Imported from the YAML platform of the built-in integration.
CONF_STOP_IDS = "stop_ids"
CONF_LEGACY_NAME = "legacy_name"
CONF_STOP_SENSOR = "stop_sensor"
CONF_YAML_QUAYS = "yaml_quays"

DEFAULT_NAME = "Entur"
DEFAULT_EXPAND_PLATFORMS = True
DEFAULT_NUMBER_OF_DEPARTURES = 2
DEFAULT_OMIT_NON_BOARDING = True
DEFAULT_SHOW_ON_MAP = False
MIN_DEPARTURES = 2
MAX_DEPARTURES = 10
