"""Actions of the Entur public transport integration."""

import probatio

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import EnturApiError, async_get_situation, text
from .const import DOMAIN

ATTR_SITUATION_ID = "situation_id"
SERVICE_GET_SITUATION = "get_situation"


async def _async_get_situation(call: ServiceCall) -> ServiceResponse:
    situation_id = call.data[ATTR_SITUATION_ID]
    try:
        situation = await async_get_situation(call.hass, situation_id)
    except EnturApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="situation_failed"
        ) from err
    if situation is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="situation_not_found",
            translation_placeholders={"situation_id": situation_id},
        )
    return {
        "id": situation["situationNumber"],
        "summary": text(situation["summary"]),
        "description": text(situation["description"]),
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SITUATION,
        _async_get_situation,
        schema=probatio.Schema({probatio.Required(ATTR_SITUATION_ID): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
