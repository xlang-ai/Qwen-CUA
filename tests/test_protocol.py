from __future__ import annotations

import pytest

from qwen_cua.actions import (
    ClickAction,
    KeyAction,
    ScrollAction,
    TerminateAction,
    TypeAction,
)
from qwen_cua.protocol import ToolCallParseError, parse_tool_calls, redact_tool_text


def tool_call(action: str, parameters: str = "") -> str:
    return (
        "<tool_call>\n"
        "<function=computer_use>\n"
        f"<parameter=action>\n{action}\n</parameter>\n"
        f"{parameters}"
        "</function>\n"
        "</tool_call>"
    )


def test_parse_multiple_typed_actions() -> None:
    response = "\n".join(
        [
            tool_call(
                "left_click",
                "<parameter=coordinate>\n[500, 250]\n</parameter>\n",
            ),
            tool_call(
                "type",
                "<parameter=text>\nHello, 世界\n</parameter>\n",
            ),
            tool_call(
                "key",
                '<parameter=keys>\n["CTRL", "A"]\n</parameter>\n',
            ),
            tool_call(
                "scroll",
                "<parameter=pixels>\n-640\n</parameter>\n",
            ),
            tool_call(
                "terminate",
                "<parameter=status>\nsuccess\n</parameter>\n",
            ),
        ]
    )
    actions = parse_tool_calls(response)
    assert actions == [
        ClickAction(action="left_click", coordinate=(500, 250)),
        TypeAction(action="type", text="Hello, 世界"),
        KeyAction(action="key", keys=["ctrl", "a"]),
        ScrollAction(action="scroll", pixels=-640),
        TerminateAction(action="terminate", status="success"),
    ]


def test_final_text_has_no_actions() -> None:
    assert parse_tool_calls("The requested work is complete.") == []


@pytest.mark.parametrize(
    "response",
    [
        "<tool_call><function=computer_use>",
        tool_call("left_click", "<parameter=coordinate>[2000, 2]</parameter>"),
        tool_call("unknown"),
        "<tool_call><parameter=action>wait</parameter></tool_call>",
    ],
)
def test_malformed_or_unsafe_tool_calls_fail(response: str) -> None:
    with pytest.raises(ToolCallParseError):
        parse_tool_calls(response)


def test_redact_tool_text_preserves_structure() -> None:
    response = tool_call(
        "type",
        "<parameter=text>\nsecret value\n</parameter>\n",
    )
    redacted = redact_tool_text(response)
    assert "secret value" not in redacted
    assert "[REDACTED]" in redacted
    assert parse_tool_calls(redacted)[0] == TypeAction(
        action="type",
        text="[REDACTED]",
    )
