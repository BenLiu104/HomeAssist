"""Conversation platform for Hermes Agent."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

import aiohttp

from homeassistant import intent
from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_API_KEY,
    CONF_API_URL,
    CONF_CONTINUE_MODE,
    CONF_MODEL,
    CONF_TIMEOUT,
    CONTINUE_ALWAYS,
    CONTINUE_AUTO,
    CONTINUE_NEVER,
)

_LOGGER = logging.getLogger(__name__)

_CONTINUE_RE = re.compile(
    r"\s*<ha_continue>\s*(true|false)\s*</ha_continue>\s*",
    flags=re.IGNORECASE,
)

_SYSTEM_INSTRUCTION = """
You are replying through a Home Assistant voice assistant.

Requirements:
- Answer in the user's language.
- Return only concise, natural speech suitable for text-to-speech.
- Do not use Markdown tables or long code blocks unless explicitly requested.
- At the very end append exactly one control marker:
  <ha_continue>true</ha_continue>
  when you require an immediate user reply, clarification, or confirmation;
  otherwise append:
  <ha_continue>false</ha_continue>
- Never mention or explain the control marker.
""".strip()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Hermes conversation entity."""
    async_add_entities([HermesConversationEntity(hass, entry)])


class HermesConversationEntity(ConversationEntity):
    """Forward Home Assistant conversations to Hermes Agent."""

    _attr_has_entity_name = True
    _attr_name = "Conversation"
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {
            "identifiers": {("hermes_conversation", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Nous Research / custom adapter",
            "model": "Hermes Agent API",
        }

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""
        return ["*"]

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> conversation.ConversationResult:
        """Send a final transcript to Hermes and return its reply."""
        conversation_id = user_input.conversation_id or str(uuid.uuid4())

        try:
            raw_reply = await self._async_ask_hermes(
                text=user_input.text,
                conversation_id=conversation_id,
                language=user_input.language,
            )
            spoken_reply, marker = parse_continue_marker(raw_reply)
            if not spoken_reply:
                raise HermesResponseError("Hermes returned empty content")

            should_continue = decide_continue(
                mode=self._entry.data[CONF_CONTINUE_MODE],
                marker=marker,
                spoken_reply=spoken_reply,
            )

        except HermesAuthenticationError:
            _LOGGER.error("Hermes rejected the configured API key")
            return _error_result(
                user_input,
                conversation_id,
                "Hermes 驗證失敗，請檢查 API key。",
            )
        except HermesTimeoutError:
            _LOGGER.warning("Hermes request timed out")
            return _error_result(
                user_input,
                conversation_id,
                "Hermes 今次回應逾時，請再試一次。",
            )
        except HermesConnectionError as err:
            _LOGGER.error("Unable to connect to Hermes: %s", err)
            return _error_result(
                user_input,
                conversation_id,
                "暫時連接唔到 Hermes，請檢查服務有冇運行。",
            )
        except HermesResponseError as err:
            _LOGGER.error("Invalid response from Hermes: %s", err)
            return _error_result(
                user_input,
                conversation_id,
                "Hermes 回傳咗無法處理嘅結果。",
            )

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=user_input.agent_id,
                content=spoken_reply,
            )
        )

        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(spoken_reply)

        return conversation.ConversationResult(
            conversation_id=conversation_id,
            response=response,
            continue_conversation=should_continue,
        )

    async def _async_ask_hermes(
        self,
        *,
        text: str,
        conversation_id: str,
        language: str,
    ) -> str:
        """Call Hermes's OpenAI-compatible chat completions endpoint."""
        session = async_get_clientsession(self.hass)
        api_url = self._entry.data[CONF_API_URL].rstrip("/")
        timeout_seconds = int(self._entry.data[CONF_TIMEOUT])

        headers = {
            "Authorization": f"Bearer {self._entry.data[CONF_API_KEY]}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": conversation_id,
            "X-Hermes-Session-Key": f"home-assistant:{conversation_id}",
        }
        payload = {
            "model": self._entry.data[CONF_MODEL],
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": f"{_SYSTEM_INSTRUCTION}\nUser language: {language}",
                },
                {"role": "user", "content": text},
            ],
        }

        try:
            async with asyncio.timeout(timeout_seconds):
                async with session.post(
                    f"{api_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status in (401, 403):
                        raise HermesAuthenticationError
                    if response.status >= 400:
                        body = await response.text()
                        raise HermesConnectionError(
                            f"HTTP {response.status}: {body[:300]}"
                        )
                    data: dict[str, Any] = await response.json(content_type=None)
        except HermesAuthenticationError:
            raise
        except TimeoutError as err:
            raise HermesTimeoutError from err
        except (aiohttp.ClientError, ValueError) as err:
            raise HermesConnectionError(str(err)) from err

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as err:
            raise HermesResponseError("Missing choices[0].message.content") from err

        if not isinstance(content, str):
            raise HermesResponseError("Message content is not text")
        return content


def parse_continue_marker(text: str) -> tuple[str, bool | None]:
    """Strip the control marker and return its value."""
    matches = list(_CONTINUE_RE.finditer(text))
    marker: bool | None = None

    if matches:
        marker = matches[-1].group(1).lower() == "true"

    cleaned = _CONTINUE_RE.sub("", text).strip()
    return cleaned, marker


def decide_continue(
    *,
    mode: str,
    marker: bool | None,
    spoken_reply: str,
) -> bool:
    """Choose whether Home Assistant should immediately listen again."""
    if mode == CONTINUE_ALWAYS:
        return True
    if mode == CONTINUE_NEVER:
        return False
    if mode != CONTINUE_AUTO:
        _LOGGER.warning("Unknown continue mode %s; defaulting to auto", mode)

    if marker is not None:
        return marker

    return spoken_reply.rstrip().endswith(("?", "？"))


def _error_result(
    user_input: ConversationInput,
    conversation_id: str,
    speech: str,
) -> conversation.ConversationResult:
    """Build a safe spoken error response."""
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(speech)
    return conversation.ConversationResult(
        conversation_id=conversation_id,
        response=response,
        continue_conversation=False,
    )


class HermesAuthenticationError(Exception):
    """Hermes authentication failed."""


class HermesTimeoutError(Exception):
    """Hermes request timed out."""


class HermesConnectionError(Exception):
    """Hermes could not be reached."""


class HermesResponseError(Exception):
    """Hermes returned an invalid response."""
