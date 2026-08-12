"""The speech service."""
import logging
import time
from typing import Any
from collections.abc import AsyncGenerator

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.tts import (
    CONF_LANG,
    TextToSpeechEntity,
    TtsAudioType,
    TTSAudioRequest,
    TTSAudioResponse,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import ulid
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import edge_tts
from .const import (
    DEFAULT_LANG,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    DOMAIN,
    SUPPORTED_VOICES,
)


_LOGGER = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = {
    **dict(zip(SUPPORTED_VOICES.values(), SUPPORTED_VOICES.keys())),
    DEFAULT_LANG: DEFAULT_VOICE,
}

SEPARATORS = ("\n", "。", ". ", "，", ", ", "；", "; ", "！", "! ", "？", "? ", "、")


def _split_at_last_separator(text: str) -> tuple[str, str]:
    """Split text just after its last sentence separator.

    Returns (complete, remainder); complete is "" when no separator exists.
    """
    best = -1
    for sep in SEPARATORS:
        idx = text.rfind(sep)
        if idx >= 0:
            best = max(best, idx + len(sep))
    if best < 0:
        return "", text
    return text[:best], text[best:]


class _ReusableConnector(aiohttp.TCPConnector):
    """TCP connector that survives ClientSession.close().

    edge-tts opens a fresh ClientSession per synthesis and closes it when
    done, which would also close the connector it was given. Keeping the
    connector alive across requests reuses the DNS cache and TLS session
    pool, cutting cold-start time-to-first-audio from 1.7-3.9s to a stable
    ~0.7s. This matters because the Voice PE firmware only waits 2 seconds
    for playback to start before it reopens the microphone.
    """

    async def close(self) -> None:
        return None

    async def really_close(self) -> None:
        await super().close()

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Edge TTS entity from a config entry."""
    entity = EdgeTTSEntity(hass, config_entry)
    async_add_entities([entity])


class EdgeTTSEntity(TextToSpeechEntity):
    """The Edge TTS entity."""

    _attr_name = "Edge TTS"
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize Edge TTS entity."""
        self.hass = hass
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}-tts"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": "Edge TTS Service",
            "manufacturer": "Edge TTS Community",
            "model": "Cloud TTS",
            "sw_version": edge_tts.__version__,
            "entry_type": DeviceEntryType.SERVICE,
        }
        self._attr_extra_state_attributes = {}

        # Prosody and style options
        self._prosody_options = ['pitch', 'rate', 'volume']
        self._style_options = ['style', 'styledegree', 'role']
        self._connector: _ReusableConnector | None = None

    async def async_added_to_hass(self) -> None:
        self._connector = _ReusableConnector(
            ttl_dns_cache=3600, keepalive_timeout=300
        )
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        domain_data["tts_entity_id"] = self.entity_id
        access_tokens = domain_data.setdefault("access_tokens", {
            "temp": ulid.ulid_hex(),
            "long": self.hass.data["core.uuid"],
        })
        self._attr_extra_state_attributes["access_tokens"] = access_tokens.copy()

    async def async_will_remove_from_hass(self) -> None:
        if self._connector is not None:
            await self._connector.really_close()
            self._connector = None

    @property
    def default_language(self) -> str:
        """Return the default language from options."""
        return self._config_entry.options.get(CONF_LANG, DEFAULT_LANG)

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        return list([*SUPPORTED_LANGUAGES.keys(), *SUPPORTED_VOICES.keys()])

    @property
    def supported_options(self) -> list[str]:
        """Return a list of supported options."""
        return ['voice'] + self._prosody_options

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        return "mp3", await self.async_process_tts_audio(message, language, options)

    async def async_process_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> bytes | None:
        mp3 = b''
        async for data in self._async_stream_edge_tts(message, language, options):
            mp3 += data
        return mp3

    def _resolve_voice(
        self, message: str, language: str, options: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Resolve the voice and merged options for a synthesis request."""
        opt = {CONF_LANG: language}
        if language in SUPPORTED_VOICES:
            opt[CONF_LANG] = SUPPORTED_VOICES[language]
            opt['voice'] = language
        opt = {**self._config_entry.options, **opt, **(options or {})}

        lang = opt.get(CONF_LANG) or language or DEFAULT_LANG
        voice = opt.get('voice') or SUPPORTED_LANGUAGES.get(lang) or DEFAULT_VOICE

        for f in self._style_options:
            if f in opt:
                _LOGGER.warning(
                    'Edge TTS options style/styledegree/role are no longer supported, '
                    'please remove them from your automation or script. '
                    'See: https://github.com/hasscc/hass-edge-tts/issues/8'
                )
                break

        _LOGGER.debug('%s: %s', self.name, [message, opt])
        return voice, opt

    async def _async_stream_edge_tts(
        self, message: str, language: str, options: dict[str, Any]
    ) -> AsyncGenerator[bytes]:
        """Synthesize message, yielding mp3 chunks as edge-tts streams them.

        Yielding per chunk (instead of accumulating the full synthesis)
        minimizes time-to-first-audio, which the Voice PE firmware needs to
        stay under its 2-second playback-start timeout.
        """
        voice, opt = self._resolve_voice(message, language, options)
        start_time = time.perf_counter()
        tts = edge_tts.Communicate(
            message,
            voice=voice,
            pitch=opt.get('pitch', '+0Hz'),
            rate=opt.get('rate', DEFAULT_RATE),
            volume=opt.get('volume', '+0%'),
            connector=self._connector,
        )
        first_chunk_time = None
        try:
            async for chunk in tts.stream():
                if chunk["type"] == "audio":
                    if first_chunk_time is None:
                        first_chunk_time = time.perf_counter()
                        _LOGGER.debug(
                            "first audio chunk after %.0fms",
                            (first_chunk_time - start_time) * 1000,
                        )
                    yield chunk["data"]
                else:
                    _LOGGER.debug("Edge TTS metadata: %s", chunk)
        except edge_tts.exceptions.NoAudioReceived as exc:
            _LOGGER.warning("No audio received for text: %s", message)
            raise HomeAssistantError(f"{self.name}: No audio received: {message}") from exc
        _LOGGER.debug(
            "load tts elapsed_time: %sms",
            (time.perf_counter() - start_time) * 1000,
        )

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        return TTSAudioResponse("mp3", self._process_tts_stream(request))

    async def _process_tts_stream(self, request: TTSAudioRequest) -> AsyncGenerator[bytes]:
        """Generate speech from an incoming message stream.

        Complete sentences are synthesized as soon as they are available:
        each incoming message is appended to a buffer and everything up to
        the last sentence separator is synthesized in one edge-tts stream
        (a non-streaming conversation agent delivers its full reply in a
        single message, so it is synthesized in one shot with no buffering
        delay).
        """
        _LOGGER.debug("Starting TTS Stream with options: %s", request.options)
        buffer = ""
        async for message in request.message_gen:
            _LOGGER.debug("Streaming tts message: %s", message)
            buffer += message
            complete, buffer = _split_at_last_separator(buffer)
            if complete.strip():
                async for data in self._async_stream_edge_tts(
                    complete, request.language, request.options
                ):
                    yield data
        if buffer.strip():
            async for data in self._async_stream_edge_tts(
                buffer, request.language, request.options
            ):
                yield data
