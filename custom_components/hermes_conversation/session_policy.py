"""Persistent cross-wake session policy for Hermes conversations.

This module deliberately has no Home Assistant imports.  It owns the rules for
mapping many short Assist interactions onto a smaller number of Hermes named
conversation chains; Home Assistant storage and transport stay outside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import re
from typing import Any, Callable, Dict, Optional, Tuple
import uuid


_SESSION_DIRECTIVE_RE = re.compile(
    r"\s*<ha_session>\s*(pin|release|unchanged)\s*</ha_session>\s*",
    flags=re.IGNORECASE,
)

_NEW_COMMANDS = ("開始新話題", "新話題")
_RESUME_COMMANDS = ("繼續頭先", "繼續之前個話題", "繼續上一個話題")
_CLOSE_COMMANDS = ("結束呢個話題", "結束對話", "完結呢個話題")
_PIN_COMMANDS = {
    "開始研究模式": "research",
    "開始煮餸模式": "cooking",
    "開始煮飯模式": "cooking",
}


@dataclass(frozen=True)
class SessionDecision:
    """Result of applying session rules to one user utterance."""

    conversation_name: Optional[str]
    forward_text: Optional[str]
    local_response: Optional[str] = None


@dataclass
class _ScopeState:
    active: Optional[str] = None
    previous: Optional[str] = None
    mode: str = "normal"
    topic: Optional[str] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class SessionPolicy:
    """Choose and persist the Hermes working conversation for each scope."""

    def __init__(
        self,
        *,
        entry_id: str,
        normal_ttl: timedelta,
        pinned_ttl: timedelta,
        state: Optional[Dict[str, Any]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._entry_id = entry_id
        self._normal_ttl = normal_ttl
        self._pinned_ttl = pinned_ttl
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._scopes = self._load_state(state or {})

    def decide(self, *, scope: str, text: str, now: datetime) -> SessionDecision:
        """Resolve a user utterance to a Hermes conversation or local action."""
        state = self._scopes.setdefault(scope, _ScopeState())
        normalized = _normalize(text)

        if normalized in _NEW_COMMANDS:
            self._rotate(scope, state, now=now, mode="normal")
            return SessionDecision(
                state.active,
                None,
                "好，已經開始新話題。你想傾咩？",
            )

        if normalized in _RESUME_COMMANDS:
            if state.previous:
                state.active, state.previous = state.previous, state.active
                state.mode = "normal"
                self._touch(state, now)
                response = "好，繼續返之前個話題。"
            elif state.active:
                self._touch(state, now)
                response = "好，繼續。"
            else:
                response = "暫時冇之前嘅話題可以繼續。"
            return SessionDecision(state.active, None, response)

        if normalized in _CLOSE_COMMANDS:
            state.previous = state.active or state.previous
            state.active = None
            state.mode = "normal"
            state.topic = None
            state.updated_at = now
            state.expires_at = None
            return SessionDecision(None, None, "好，已經結束呢個話題。")

        pin_match = _match_prefixed_command(text, tuple(_PIN_COMMANDS))
        if pin_match is not None:
            command, remainder = pin_match
            self._rotate(
                scope,
                state,
                now=now,
                mode="pinned",
                topic=_PIN_COMMANDS[command],
            )
            if remainder:
                return SessionDecision(state.active, remainder)
            noun = "研究" if state.topic == "research" else "煮餸"
            return SessionDecision(
                state.active,
                None,
                f"好，已經開始{noun}模式。你想做咩？",
            )

        if state.active is None or _is_expired(state, now):
            self._rotate(scope, state, now=now, mode="normal")
        else:
            self._touch(state, now)

        return SessionDecision(state.active, text.strip())

    def apply_directive(
        self,
        *,
        scope: str,
        directive: Optional[str],
        now: datetime,
    ) -> None:
        """Apply a non-spoken session directive emitted by Hermes."""
        if directive not in ("pin", "release"):
            return
        state = self._scopes.get(scope)
        if state is None or state.active is None:
            return
        state.mode = "pinned" if directive == "pin" else "normal"
        self._touch(state, now)

    def export_state(self) -> Dict[str, Any]:
        """Return JSON-serializable state for Home Assistant storage."""
        return {
            "scopes": {
                scope: {
                    "active": state.active,
                    "previous": state.previous,
                    "mode": state.mode,
                    "topic": state.topic,
                    "updated_at": _format_datetime(state.updated_at),
                    "expires_at": _format_datetime(state.expires_at),
                }
                for scope, state in self._scopes.items()
            }
        }

    def _rotate(
        self,
        scope: str,
        state: _ScopeState,
        *,
        now: datetime,
        mode: str,
        topic: Optional[str] = None,
    ) -> None:
        state.previous = state.active
        scope_digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
        state.active = (
            f"home-assistant:{self._entry_id}:{scope_digest}:{self._id_factory()}"
        )
        state.mode = mode
        state.topic = topic
        self._touch(state, now)

    def _touch(self, state: _ScopeState, now: datetime) -> None:
        state.updated_at = now
        ttl = self._pinned_ttl if state.mode == "pinned" else self._normal_ttl
        state.expires_at = now + ttl

    @staticmethod
    def _load_state(raw: Dict[str, Any]) -> Dict[str, _ScopeState]:
        scopes: Dict[str, _ScopeState] = {}
        raw_scopes = raw.get("scopes", {})
        if not isinstance(raw_scopes, dict):
            return scopes
        for scope, item in raw_scopes.items():
            if not isinstance(scope, str) or not isinstance(item, dict):
                continue
            scopes[scope] = _ScopeState(
                active=_optional_string(item.get("active")),
                previous=_optional_string(item.get("previous")),
                mode="pinned" if item.get("mode") == "pinned" else "normal",
                topic=_optional_string(item.get("topic")),
                updated_at=_parse_datetime(item.get("updated_at")),
                expires_at=_parse_datetime(item.get("expires_at")),
            )
        return scopes


def parse_session_directive(text: str) -> Tuple[str, Optional[str]]:
    """Strip the last Hermes session marker and return its directive."""
    matches = list(_SESSION_DIRECTIVE_RE.finditer(text))
    directive = matches[-1].group(1).lower() if matches else None
    return _SESSION_DIRECTIVE_RE.sub("", text).strip(), directive


def _normalize(text: str) -> str:
    return text.strip().rstrip("。！？!?，,").strip()


def _match_prefixed_command(
    text: str, commands: Tuple[str, ...]
) -> Optional[Tuple[str, str]]:
    normalized = text.strip()
    for command in commands:
        if not normalized.startswith(command):
            continue
        remainder = normalized[len(command) :].lstrip("：:，,。 ")
        return command, remainder
    return None


def _is_expired(state: _ScopeState, now: datetime) -> bool:
    return state.expires_at is None or now > state.expires_at


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _optional_string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None
