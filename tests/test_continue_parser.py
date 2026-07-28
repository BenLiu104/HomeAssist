"""Document expected continue-marker parsing behavior."""

import re

CONTINUE_RE = re.compile(
    r"\s*<ha_continue>\s*(true|false)\s*</ha_continue>\s*",
    flags=re.IGNORECASE,
)


def parse(text):
    matches = list(CONTINUE_RE.finditer(text))
    marker = None if not matches else matches[-1].group(1).lower() == "true"
    return CONTINUE_RE.sub("", text).strip(), marker


def test_true_marker_is_removed():
    assert parse("Which light?<ha_continue>true</ha_continue>") == (
        "Which light?",
        True,
    )


def test_false_marker_is_removed():
    assert parse("Done. <ha_continue>false</ha_continue>") == ("Done.", False)


def test_missing_marker():
    assert parse("Done.") == ("Done.", None)


def test_last_marker_wins():
    assert parse(
        "Answer <ha_continue>false</ha_continue>"
        "<ha_continue>true</ha_continue>"
    ) == ("Answer", True)
