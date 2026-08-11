"""Behaviour tests for cross-wake Hermes session policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import importlib.util
import itertools
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "hermes_conversation"
    / "session_policy.py"
)
SPEC = importlib.util.spec_from_file_location("hermes_session_policy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
session_policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = session_policy
SPEC.loader.exec_module(session_policy)
SessionPolicy = session_policy.SessionPolicy
ScopeLockPool = session_policy.ScopeLockPool
parse_session_directive = session_policy.parse_session_directive


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _policy(state=None) -> SessionPolicy:
    ids = (f"session-{number}" for number in itertools.count(1))
    return SessionPolicy(
        entry_id="entry-1",
        normal_ttl=timedelta(minutes=10),
        pinned_ttl=timedelta(hours=2),
        state=state,
        id_factory=lambda: next(ids),
    )


class SessionPolicyTests(unittest.TestCase):
    def test_new_ha_conversation_reuses_working_session_within_ttl(self) -> None:
        policy = _policy()

        first = policy.decide(scope="user:ben", text="幫我搵食譜", now=NOW)
        second = policy.decide(
            scope="user:ben",
            text="有咩材料？",
            now=NOW + timedelta(minutes=9),
        )

        self.assertEqual(first.conversation_name, second.conversation_name)

    def test_normal_session_rotates_after_ttl(self) -> None:
        policy = _policy()

        first = policy.decide(scope="user:ben", text="第一題", now=NOW)
        second = policy.decide(
            scope="user:ben",
            text="第二題",
            now=NOW + timedelta(minutes=11),
        )

        self.assertNotEqual(first.conversation_name, second.conversation_name)

    def test_explicit_new_topic_rotates_immediately(self) -> None:
        policy = _policy()
        first = policy.decide(scope="user:ben", text="第一題", now=NOW)

        command = policy.decide(
            scope="user:ben",
            text="開始新話題",
            now=NOW + timedelta(minutes=1),
        )
        next_message = policy.decide(
            scope="user:ben",
            text="第二題",
            now=NOW + timedelta(minutes=2),
        )

        self.assertIsNone(command.forward_text)
        self.assertEqual(
            command.local_response,
            "好，已經開始新話題。你想傾咩？",
        )
        self.assertNotEqual(first.conversation_name, next_message.conversation_name)

    def test_resume_previous_restores_expired_session(self) -> None:
        policy = _policy()
        original = policy.decide(scope="user:ben", text="第一題", now=NOW)
        policy.decide(
            scope="user:ben",
            text="第二題",
            now=NOW + timedelta(minutes=11),
        )

        resumed = policy.decide(
            scope="user:ben",
            text="繼續頭先",
            now=NOW + timedelta(minutes=12),
        )
        next_message = policy.decide(
            scope="user:ben",
            text="講返落去",
            now=NOW + timedelta(minutes=13),
        )

        self.assertIsNone(resumed.forward_text)
        self.assertEqual(original.conversation_name, next_message.conversation_name)

    def test_pinned_research_session_uses_long_ttl(self) -> None:
        policy = _policy()
        started = policy.decide(
            scope="user:ben",
            text="開始研究模式：幫我研究焗爐",
            now=NOW,
        )
        continued = policy.decide(
            scope="user:ben",
            text="比較多兩款",
            now=NOW + timedelta(hours=1),
        )

        self.assertEqual(started.forward_text, "幫我研究焗爐")
        self.assertEqual(started.conversation_name, continued.conversation_name)

    def test_state_round_trip_survives_restart(self) -> None:
        policy = _policy()
        first = policy.decide(scope="user:ben", text="第一題", now=NOW)

        restored = _policy(policy.export_state())
        second = restored.decide(
            scope="user:ben",
            text="第二題",
            now=NOW + timedelta(minutes=1),
        )

        self.assertEqual(first.conversation_name, second.conversation_name)

    def test_scopes_do_not_share_working_conversations(self) -> None:
        policy = _policy()

        ben = policy.decide(scope="user:ben", text="第一題", now=NOW)
        guest = policy.decide(scope="device:guest", text="第一題", now=NOW)

        self.assertNotEqual(ben.conversation_name, guest.conversation_name)

    def test_agent_session_marker_is_stripped_and_applied(self) -> None:
        policy = _policy()
        decision = policy.decide(scope="user:ben", text="逐步教我煮", now=NOW)
        cleaned, directive = parse_session_directive(
            "好，先準備材料。<ha_session>pin</ha_session>"
        )
        policy.apply_directive(
            scope="user:ben",
            directive=directive,
            now=NOW,
        )
        continued = policy.decide(
            scope="user:ben",
            text="下一步",
            now=NOW + timedelta(hours=1),
        )

        self.assertEqual(cleaned, "好，先準備材料。")
        self.assertEqual(decision.conversation_name, continued.conversation_name)

    def test_malformed_agent_session_marker_is_stripped_but_not_applied(self) -> None:
        cleaned, directive = parse_session_directive(
            "完成。<ha_session>keep</ha_session>"
        )

        self.assertEqual(cleaned, "完成。")
        self.assertIsNone(directive)


class ScopeLockPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_scope_requests_are_serialized(self) -> None:
        pool = ScopeLockPool()
        events = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first_request() -> None:
            async with pool.for_scope("user:ben"):
                events.append("first-enter")
                first_entered.set()
                await release_first.wait()
                events.append("first-exit")

        async def second_request() -> None:
            await first_entered.wait()
            async with pool.for_scope("user:ben"):
                events.append("second-enter")

        first = asyncio.create_task(first_request())
        second = asyncio.create_task(second_request())
        await first_entered.wait()
        await asyncio.sleep(0)
        self.assertEqual(events, ["first-enter"])

        release_first.set()
        await asyncio.gather(first, second)
        self.assertEqual(events, ["first-enter", "first-exit", "second-enter"])


if __name__ == "__main__":
    unittest.main()
