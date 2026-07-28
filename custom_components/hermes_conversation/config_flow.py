"""Config flow for Hermes Conversation."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_API_URL,
    CONF_CONTINUE_MODE,
    CONF_MODEL,
    CONF_TIMEOUT,
    CONTINUE_MODES,
    DEFAULT_API_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


class HermesConversationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hermes Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_url = user_input[CONF_API_URL].rstrip("/")
            api_key = user_input[CONF_API_KEY]

            try:
                await _validate_connection(
                    self.hass,
                    api_url=api_url,
                    api_key=api_key,
                    timeout=min(int(user_input[CONF_TIMEOUT]), 30),
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(api_url)
                self._abort_if_unique_id_configured()
                data = dict(user_input)
                data[CONF_API_URL] = api_url
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=data,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Hermes"): str,
                vol.Required(CONF_API_URL, default=DEFAULT_API_URL): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_MODEL, default=DEFAULT_MODEL): str,
                vol.Required(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=600)
                ),
                vol.Required(CONF_CONTINUE_MODE, default="auto"): vol.In(
                    CONTINUE_MODES
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


async def _validate_connection(
    hass,
    *,
    api_url: str,
    api_key: str,
    timeout: int,
) -> None:
    """Validate Hermes API server health and authentication."""
    session = async_get_clientsession(hass)
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with asyncio.timeout(timeout):
            async with session.get(f"{api_url}/health", headers=headers) as response:
                if response.status in (401, 403):
                    raise InvalidAuth
                if response.status >= 400:
                    raise CannotConnect
                payload = await response.json(content_type=None)
    except InvalidAuth:
        raise
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        raise CannotConnect from err

    if payload.get("status") != "ok":
        raise CannotConnect


class CannotConnect(Exception):
    """Raised when Hermes cannot be reached."""


class InvalidAuth(Exception):
    """Raised when Hermes credentials are rejected."""
