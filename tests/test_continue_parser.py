"""Document expected Hermes response parsing behavior."""

import re

CONTINUE_RE = re.compile(
    r"\s*<ha_continue>\s*(true|false)\s*</ha_continue>\s*",
    flags=re.IGNORECASE,
)


def parse_marker(text):
    matches = list(CONTINUE_RE.finditer(text))
    marker = None if not matches else matches[-1].group(1).lower() == "true"
    return CONTINUE_RE.sub("", text).strip(), marker


def extract_response_text(data):
    parts = []
    for item in data.get("output", []):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(
                content.get("text"), str
            ):
                parts.append(content["text"])
    return "".join(parts).strip()


def test_true_marker_is_removed():
    assert parse_marker("Which light?<ha_continue>true</ha_continue>") == (
        "Which light?",
        True,
    )


def test_false_marker_is_removed():
    assert parse_marker("Done. <ha_continue>false</ha_continue>") == (
        "Done.",
        False,
    )


def test_missing_marker():
    assert parse_marker("Done.") == ("Done.", None)


def test_last_marker_wins():
    assert parse_marker(
        "Answer <ha_continue>false</ha_continue>"
        "<ha_continue>true</ha_continue>"
    ) == ("Answer", True)


def test_extracts_only_assistant_output_text():
    payload = {
        "output": [
            {
                "type": "function_call",
                "name": "terminal",
                "arguments": "{}",
                "call_id": "call_1",
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "secret tool output",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Finished."},
                ],
            },
        ]
    }

    assert extract_response_text(payload) == "Finished."


def test_combines_multiple_output_text_parts():
    payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Part one. "},
                    {"type": "output_text", "text": "Part two."},
                ],
            }
        ]
    }

    assert extract_response_text(payload) == "Part one. Part two."
