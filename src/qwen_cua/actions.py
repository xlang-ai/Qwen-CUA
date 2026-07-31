from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

Coordinate = tuple[int, int]


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyAction(ActionBase):
    action: Literal["key", "key_down", "key_up"]
    keys: list[str] = Field(min_length=1, max_length=8)

    @field_validator("keys")
    @classmethod
    def normalize_keys(cls, keys: list[str]) -> list[str]:
        normalized = [str(key).strip().lower() for key in keys if str(key).strip()]
        if not normalized:
            raise ValueError("keys must contain at least one non-empty key")
        return normalized


class TypeAction(ActionBase):
    action: Literal["type"]
    text: str = Field(max_length=10_000)


class CoordinateAction(ActionBase):
    action: Literal["mouse_move", "left_click_drag"]
    coordinate: Coordinate
    duration: float = Field(default=0.5, ge=0, le=30)

    @field_validator("coordinate")
    @classmethod
    def validate_coordinate(cls, value: Coordinate) -> Coordinate:
        if any(point < 0 or point > 999 for point in value):
            raise ValueError("relative coordinates must be between 0 and 999")
        return value


class ClickAction(ActionBase):
    action: Literal[
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "left_mouse_down",
        "left_mouse_up",
    ]
    coordinate: Coordinate | None = None

    @field_validator("coordinate")
    @classmethod
    def validate_coordinate(cls, value: Coordinate | None) -> Coordinate | None:
        if value is not None and any(point < 0 or point > 999 for point in value):
            raise ValueError("relative coordinates must be between 0 and 999")
        return value


class ScrollAction(ActionBase):
    action: Literal["scroll", "hscroll"]
    pixels: int = Field(ge=-10_000, le=10_000)


class WaitAction(ActionBase):
    action: Literal["wait"]
    time: float = Field(default=1.0, ge=0, le=30)


class ScreenshotAction(ActionBase):
    action: Literal["screenshot"]


class TerminateAction(ActionBase):
    action: Literal["terminate"]
    status: Literal["success", "failure"] = "success"


class CallUserAction(ActionBase):
    action: Literal["call_user"]
    text: str = Field(min_length=1, max_length=4_000)


ComputerAction = Annotated[
    KeyAction
    | TypeAction
    | CoordinateAction
    | ClickAction
    | ScrollAction
    | WaitAction
    | ScreenshotAction
    | TerminateAction
    | CallUserAction,
    Field(discriminator="action"),
]

computer_action_adapter: TypeAdapter[ComputerAction] = TypeAdapter(ComputerAction)


def action_to_public_dict(
    action: ComputerAction, *, redact_text: bool = False
) -> dict[str, object]:
    payload = action.model_dump(mode="json")
    if redact_text and isinstance(action, TypeAction):
        payload["text"] = "[REDACTED]"
    return payload
