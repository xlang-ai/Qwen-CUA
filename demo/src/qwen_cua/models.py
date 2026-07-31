from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrowserMode(str, Enum):
    HEADLESS = "headless"
    HEADFUL = "headful"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNVERIFIED = "unverified"


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"
    NOT_RUN = "not_run"


class EventLevel(str, Enum):
    OK = "ok"
    PENDING = "pending"
    WARN = "warn"
    ERROR = "error"


class ScenarioManifest(ApiModel):
    id: str
    title: str
    description: str
    default_prompt: str
    lab_path: str
    tags: list[str]


class ModelInfo(ApiModel):
    id: str
    is_default: bool


class StartRunRequest(ApiModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    model: str | None = None
    scenario_id: str | None = None
    target_url: HttpUrl | None = None
    browser_mode: BrowserMode = BrowserMode.HEADLESS
    max_turns: int | None = Field(default=None, ge=1, le=100)
    custom_url_risk_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> StartRunRequest:
        if bool(self.scenario_id) == bool(self.target_url):
            raise ValueError("provide exactly one of scenario_id or target_url")
        if self.target_url is not None and not self.custom_url_risk_acknowledged:
            raise ValueError("custom_url_risk_acknowledged must be true for custom URLs")
        return self


class StartRunResponse(ApiModel):
    run_id: str
    status: RunStatus
    event_stream_url: str
    replay_url: str


class ScreenshotArtifact(ApiModel):
    id: str
    sequence: int
    label: str
    captured_at: datetime
    page_url: str
    page_title: str
    width: int
    height: int
    url: str


class PendingIntervention(ApiModel):
    id: str
    kind: Literal[
        "custom_url",
        "password",
        "file_upload",
        "download",
        "form_submit",
        "cross_origin",
        "user_input",
    ]
    title: str
    message: str
    created_at: datetime
    action: dict[str, Any] | None = None
    requires_file: bool = False


class RunSummary(ApiModel):
    outcome: RunOutcome
    verification: VerificationStatus
    verification_detail: str | None = None
    turns: int
    actions: int
    screenshots: int
    notes: list[str] = Field(default_factory=list)


class RunDetail(ApiModel):
    id: str
    status: RunStatus
    prompt: str
    model: str
    scenario_id: str | None = None
    target_url: str
    browser_mode: BrowserMode
    max_turns: int
    started_at: datetime
    completed_at: datetime | None = None
    event_stream_url: str
    replay_url: str
    screenshots: list[ScreenshotArtifact] = Field(default_factory=list)
    pending_intervention: PendingIntervention | None = None
    summary: RunSummary | None = None


class RunEvent(ApiModel):
    id: str
    run_id: str
    sequence: int
    type: str
    level: EventLevel
    message: str
    created_at: datetime
    detail: Any | None = None
    screenshot_id: str | None = None


class ApprovalRequest(ApiModel):
    decision: Literal["approve", "reject"]


class UserInputRequest(ApiModel):
    intervention_id: str
    text: str = Field(min_length=1, max_length=4_000)


class ScenarioResetResponse(ApiModel):
    scenario_id: str
    reset_at: datetime
