from __future__ import annotations

import pytest

from qwen_cua.actions import ClickAction, TypeAction
from qwen_cua.computer import TargetInspection
from qwen_cua.safety import (
    UnsafeUrlError,
    classify_sensitive_action,
    validate_custom_url,
)


def inspection(**overrides):
    values = {
        "element_ref": "ref-1",
        "tag": "button",
        "input_type": "",
        "text": "Continue",
        "href": "",
        "form_action": "",
        "download": False,
        "is_password": False,
        "is_file": False,
        "is_submit": False,
    }
    values.update(overrides)
    return TargetInspection(**values)


def test_private_urls_are_blocked_by_default() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_custom_url("http://127.0.0.1:8080", allow_private=False)
    validate_custom_url("http://127.0.0.1:8080", allow_private=True)


def test_password_text_is_redacted_in_intervention() -> None:
    intervention = classify_sensitive_action(
        action=TypeAction(action="type", text="super-secret"),
        inspection=inspection(
            tag="input",
            input_type="password",
            is_password=True,
        ),
        current_url="https://example.com/login",
    )
    assert intervention is not None
    assert intervention.kind == "password"
    assert intervention.action["text"] == "[REDACTED]"


def test_cross_origin_click_requires_approval() -> None:
    intervention = classify_sensitive_action(
        action=ClickAction(action="left_click", coordinate=(500, 500)),
        inspection=inspection(href="https://other.example/path"),
        current_url="https://example.com/start",
    )
    assert intervention is not None
    assert intervention.kind == "cross_origin"
