from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from .actions import (
    CallUserAction,
    ComputerAction,
    TerminateAction,
    action_to_public_dict,
)
from .computer import BrowserComputer, Screenshot
from .config import Settings
from .model_client import QwenModelClient
from .models import (
    ApprovalRequest,
    BrowserMode,
    EventLevel,
    ModelInfo,
    PendingIntervention,
    RunDetail,
    RunEvent,
    RunOutcome,
    RunStatus,
    RunSummary,
    ScenarioManifest,
    ScreenshotArtifact,
    StartRunRequest,
    StartRunResponse,
    UserInputRequest,
    VerificationStatus,
)
from .protocol import (
    COLLAPSED_SCREENSHOT_TEXT,
    ToolCallParseError,
    build_system_prompt,
    parse_tool_calls,
    redact_tool_text,
    repair_instruction,
)
from .safety import (
    SafetyIntervention,
    classify_sensitive_action,
    validate_custom_url,
)
from .scenarios import get_scenario, list_scenarios, verify_scenario


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunRejectedError(RuntimeError):
    pass


@dataclass(slots=True)
class AgentHistory:
    prompt: str
    history_n: int
    image_max: int
    screenshots: list[str] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    action_summaries: list[str] = field(default_factory=list)
    feedback: dict[int, str] = field(default_factory=dict)

    def add_screenshot(self, payload: bytes, feedback: str = "") -> None:
        self.screenshots.append(_process_image(payload))
        if feedback:
            self.feedback[len(self.screenshots) - 1] = feedback

    def add_response(self, response: str, actions: list[ComputerAction]) -> None:
        self.responses.append(response)
        self.action_summaries.append(
            json.dumps(
                [action_to_public_dict(action, redact_text=True) for action in actions],
                ensure_ascii=False,
            )
        )

    def build_messages(self) -> list[dict[str, Any]]:
        total = len(self.screenshots)
        start = max(0, total - self.history_n)
        collapsed_before = max(0, total - self.image_max)
        earlier_actions = self.action_summaries[:start]
        prompt = (
            "Please generate the next move according to the UI screenshot, instruction "
            "and previous actions.\n\n"
            f"Instruction: {self.prompt}\n\n"
            "Previous actions from omitted turns:\n"
            f"{chr(10).join(earlier_actions) if earlier_actions else 'None'}"
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": [{"type": "text", "text": build_system_prompt()}],
            }
        ]
        for index in range(start, total):
            content: list[dict[str, Any]] = []
            if index == start:
                content.append({"type": "text", "text": prompt})
            elif feedback := self.feedback.get(index):
                content.append(
                    {
                        "type": "text",
                        "text": f"<tool_response>\n{feedback}\n</tool_response>",
                    }
                )
            if index < collapsed_before:
                content.append({"type": "text", "text": COLLAPSED_SCREENSHOT_TEXT})
            else:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{self.screenshots[index]}"},
                    }
                )
            messages.append({"role": "user", "content": content})
            if index < len(self.responses):
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": self.responses[index],
                            }
                        ],
                    }
                )
        return messages


@dataclass(slots=True)
class RunContext:
    detail: RunDetail
    events: list[RunEvent]
    run_dir: Path
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    computer: BrowserComputer | None = None
    pending_future: asyncio.Future[dict[str, Any]] | None = None
    pending_element_ref: str | None = None
    action_count: int = 0
    turn_count: int = 0


class RunnerManager:
    def __init__(
        self,
        settings: Settings,
        *,
        model_client: QwenModelClient | None = None,
        computer_factory: Callable[..., BrowserComputer] | None = None,
    ):
        self.settings = settings
        self.model_client = model_client or QwenModelClient(settings)
        self.computer_factory = computer_factory or BrowserComputer
        self.runs: dict[str, RunContext] = {}
        self._lock = asyncio.Lock()
        self.settings.data_root.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        tasks = [context.task for context in self.runs.values() if context.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.model_client.close()

    def models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=model, is_default=model == self.settings.default_model)
            for model in self.settings.models
        ]

    def scenarios(self) -> list[ScenarioManifest]:
        return list_scenarios()

    async def start_run(self, request: StartRunRequest) -> StartRunResponse:
        async with self._lock:
            active = sum(
                context.detail.status
                in {
                    RunStatus.QUEUED,
                    RunStatus.RUNNING,
                    RunStatus.WAITING_FOR_APPROVAL,
                    RunStatus.WAITING_FOR_USER,
                }
                for context in self.runs.values()
            )
            if active >= self.settings.max_concurrent_runs:
                raise ValueError(
                    f"runner already has {active} active run(s); "
                    f"limit={self.settings.max_concurrent_runs}"
                )
            model = request.model or self.settings.default_model
            if model not in self.settings.models:
                raise ValueError(f"model is not in QWEN_CUA_MODELS: {model}")
            if request.browser_mode is BrowserMode.HEADFUL and self.settings.docker_mode:
                raise ValueError("headful browser mode is unavailable in Docker")

            scenario = get_scenario(request.scenario_id) if request.scenario_id else None
            if request.scenario_id and scenario is None:
                raise ValueError(f"unknown scenario: {request.scenario_id}")
            if scenario is not None:
                target_url = f"http://127.0.0.1:{self.settings.port}/labs/{scenario.lab_path}"
            else:
                target_url = str(request.target_url)
                validate_custom_url(
                    target_url,
                    allow_private=self.settings.allow_private_urls,
                )

            run_id = uuid.uuid4().hex
            run_dir = self.settings.data_root / "runs" / run_id
            for child in ("screenshots", "downloads", "uploads"):
                (run_dir / child).mkdir(parents=True, exist_ok=True)
            detail = RunDetail(
                id=run_id,
                status=RunStatus.QUEUED,
                prompt=request.prompt,
                model=model,
                scenario_id=request.scenario_id,
                target_url=target_url,
                browser_mode=request.browser_mode,
                max_turns=request.max_turns or self.settings.default_max_turns,
                started_at=_now(),
                event_stream_url=f"/api/runs/{run_id}/events",
                replay_url=f"/api/runs/{run_id}/replay",
            )
            context = RunContext(detail=detail, events=[], run_dir=run_dir)
            self.runs[run_id] = context
            await self._persist(context)
            context.task = asyncio.create_task(self._execute(context))
            return StartRunResponse(
                run_id=run_id,
                status=detail.status,
                event_stream_url=detail.event_stream_url,
                replay_url=detail.replay_url,
            )

    async def get_run(self, run_id: str) -> RunDetail:
        return self._context(run_id).detail.model_copy(deep=True)

    async def get_events(self, run_id: str) -> list[RunEvent]:
        return [event.model_copy(deep=True) for event in self._context(run_id).events]

    async def stop_run(self, run_id: str) -> RunDetail:
        context = self._context(run_id)
        if context.detail.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return context.detail.model_copy(deep=True)
        if context.task is not None:
            context.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await context.task
        return context.detail.model_copy(deep=True)

    async def resolve_approval(
        self,
        run_id: str,
        intervention_id: str,
        request: ApprovalRequest,
    ) -> RunDetail:
        context = self._context(run_id)
        pending = context.detail.pending_intervention
        if pending is None or pending.id != intervention_id:
            raise ValueError("approval is no longer pending")
        if pending.requires_file and request.decision == "approve":
            raise ValueError("this approval requires a file upload")
        self._resolve_pending(
            context,
            {"decision": request.decision},
        )
        return context.detail.model_copy(deep=True)

    async def resolve_user_input(
        self,
        run_id: str,
        request: UserInputRequest,
    ) -> RunDetail:
        context = self._context(run_id)
        pending = context.detail.pending_intervention
        if pending is None or pending.id != request.intervention_id or pending.kind != "user_input":
            raise ValueError("user input is no longer pending")
        self._resolve_pending(context, {"decision": "approve", "text": request.text})
        return context.detail.model_copy(deep=True)

    async def resolve_file_upload(
        self,
        run_id: str,
        intervention_id: str,
        *,
        filename: str,
        payload: bytes,
    ) -> RunDetail:
        context = self._context(run_id)
        pending = context.detail.pending_intervention
        if pending is None or pending.id != intervention_id or pending.kind != "file_upload":
            raise ValueError("file upload is no longer pending")
        if len(payload) > self.settings.max_upload_bytes:
            raise ValueError(f"file exceeds {self.settings.max_upload_bytes} byte upload limit")
        safe_name = Path(filename).name or "upload.bin"
        destination = context.run_dir / "uploads" / f"{uuid.uuid4().hex}-{safe_name}"
        await asyncio.to_thread(destination.write_bytes, payload)
        self._resolve_pending(
            context,
            {"decision": "approve", "file_path": str(destination)},
        )
        return context.detail.model_copy(deep=True)

    async def wait_for_events(
        self,
        run_id: str,
        *,
        after_sequence: int,
    ) -> AsyncIterator[RunEvent | None]:
        context = self._context(run_id)
        cursor = max(0, after_sequence + 1)
        while True:
            while cursor < len(context.events):
                event = context.events[cursor]
                cursor += 1
                yield event
            if context.detail.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                break
            async with context.condition:
                try:
                    await asyncio.wait_for(context.condition.wait(), timeout=15)
                except TimeoutError:
                    yield None

    async def replay(self, run_id: str) -> dict[str, Any]:
        context = self._context(run_id)
        return self._replay_payload(context)

    def screenshot_path(self, run_id: str, filename: str) -> Path:
        context = self._context(run_id)
        path = context.run_dir / "screenshots" / Path(filename).name
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def download_path(self, run_id: str, filename: str) -> Path:
        context = self._context(run_id)
        path = context.run_dir / "downloads" / Path(filename).name
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    async def _execute(self, context: RunContext) -> None:
        history = AgentHistory(
            prompt=context.detail.prompt,
            history_n=self.settings.history_n,
            image_max=self.settings.image_max,
        )
        trusted_scenario = context.detail.scenario_id is not None
        computer = self.computer_factory(
            browser_mode=context.detail.browser_mode,
            width=self.settings.viewport_width,
            height=self.settings.viewport_height,
            downloads_dir=context.run_dir / "downloads",
        )
        context.computer = computer
        try:
            context.detail.status = RunStatus.RUNNING
            await self._emit(
                context,
                type_="run_started",
                level=EventLevel.OK,
                message="Run started.",
                detail={
                    "model": context.detail.model,
                    "target_url": context.detail.target_url,
                    "browser_mode": context.detail.browser_mode,
                },
            )
            await computer.start()
            await computer.navigate(context.detail.target_url)
            initial = await self._capture(context, "initial")
            history.add_screenshot(initial.payload)

            for turn in range(1, context.detail.max_turns + 1):
                context.turn_count = turn
                await self._emit(
                    context,
                    type_="model_turn_started",
                    level=EventLevel.PENDING,
                    message=f"Model turn {turn} started.",
                )
                messages = history.build_messages()
                response = await self.model_client.generate(
                    model=context.detail.model,
                    messages=messages,
                    on_progress=lambda text: self._emit(
                        context,
                        type_="model_progress",
                        level=EventLevel.PENDING,
                        message=text,
                    ),
                )
                try:
                    actions = parse_tool_calls(response)
                except ToolCallParseError as exc:
                    repair_messages = [
                        *messages,
                        {"role": "assistant", "content": response},
                        {"role": "user", "content": repair_instruction(str(exc))},
                    ]
                    await self._emit(
                        context,
                        type_="tool_call_repair",
                        level=EventLevel.WARN,
                        message="Malformed tool call; requesting one repair.",
                        detail=str(exc),
                    )
                    response = await self.model_client.generate(
                        model=context.detail.model,
                        messages=repair_messages,
                    )
                    actions = parse_tool_calls(response)

                await self._emit(
                    context,
                    type_="model_response",
                    level=EventLevel.OK,
                    message="Model response received.",
                    detail=redact_tool_text(response),
                )
                history.add_response(response, actions)
                if not actions:
                    await self._complete_from_current_state(
                        context,
                        requested_outcome=None,
                        note="Model returned a final assistant message.",
                    )
                    return

                terminal: TerminateAction | None = None
                last_screenshot: Screenshot | None = None
                feedback_parts: list[str] = []
                for action in actions:
                    if isinstance(action, CallUserAction):
                        answer = await self._await_user_input(context, action)
                        feedback_parts.append(f"User response to call_user: {answer}")
                        continue
                    if isinstance(action, TerminateAction):
                        terminal = action
                        feedback_parts.append(f"Model requested terminate({action.status}).")
                        break

                    inspection = await computer.inspect_target(action)
                    safety = (
                        None
                        if trusted_scenario
                        else classify_sensitive_action(
                            action=action,
                            inspection=inspection,
                            current_url=computer.current_url(),
                        )
                    )
                    public_action = action_to_public_dict(
                        action,
                        redact_text=bool(inspection and inspection.is_password),
                    )
                    await self._emit(
                        context,
                        type_="action_requested",
                        level=EventLevel.PENDING,
                        message=f"Action requested: {action.action}",
                        detail=public_action,
                    )
                    if safety is not None:
                        resolution = await self._await_approval(context, safety)
                        if resolution["decision"] != "approve":
                            raise RunRejectedError("Operator rejected a sensitive action.")
                        if safety.kind == "file_upload":
                            file_path = Path(str(resolution["file_path"]))
                            await computer.set_input_file(safety.element_ref, file_path)
                        else:
                            await computer.execute(action)
                    else:
                        await computer.execute(action)
                    context.action_count += 1
                    last_screenshot = await self._capture(
                        context,
                        f"turn-{turn}-action-{context.action_count}",
                    )
                    feedback_parts.append(f"Executed {action.action}.")
                    await self._emit(
                        context,
                        type_="action_completed",
                        level=EventLevel.OK,
                        message=f"Action completed: {action.action}",
                        detail=public_action,
                        screenshot_id=context.detail.screenshots[-1].id,
                    )

                if terminal is not None:
                    await self._complete_from_current_state(
                        context,
                        requested_outcome=terminal.status,
                        note=f"Model requested terminate({terminal.status}).",
                    )
                    return
                if last_screenshot is None:
                    last_screenshot = await self._capture(context, f"turn-{turn}-feedback")
                history.add_screenshot(
                    last_screenshot.payload,
                    feedback="\n".join(feedback_parts),
                )

            await self._fail(
                context,
                f"Run exhausted the configured {context.detail.max_turns}-turn budget.",
            )
        except asyncio.CancelledError:
            context.detail.status = RunStatus.CANCELLED
            context.detail.completed_at = _now()
            context.detail.summary = RunSummary(
                outcome=RunOutcome.FAILURE,
                verification=VerificationStatus.NOT_RUN,
                turns=context.turn_count,
                actions=context.action_count,
                screenshots=len(context.detail.screenshots),
                notes=["Run cancelled by operator."],
            )
            await self._emit(
                context,
                type_="run_cancelled",
                level=EventLevel.WARN,
                message="Run cancelled.",
            )
            raise
        except RunRejectedError as exc:
            context.detail.status = RunStatus.CANCELLED
            context.detail.completed_at = _now()
            context.detail.summary = RunSummary(
                outcome=RunOutcome.FAILURE,
                verification=VerificationStatus.NOT_RUN,
                turns=context.turn_count,
                actions=context.action_count,
                screenshots=len(context.detail.screenshots),
                notes=[str(exc)],
            )
            await self._emit(
                context,
                type_="run_cancelled",
                level=EventLevel.WARN,
                message=str(exc),
            )
        except Exception as exc:
            await self._fail(context, str(exc))
        finally:
            await computer.close()
            context.computer = None
            await self._persist(context)

    async def _complete_from_current_state(
        self,
        context: RunContext,
        *,
        requested_outcome: str | None,
        note: str,
    ) -> None:
        verification = VerificationStatus.NOT_APPLICABLE
        verification_detail: str | None = None
        if requested_outcome == "failure":
            outcome = RunOutcome.FAILURE
            verification = VerificationStatus.NOT_RUN
        elif context.detail.scenario_id and context.computer:
            result = verify_scenario(
                context.detail.scenario_id,
                await context.computer.read_lab_state(),
            )
            verification = VerificationStatus.PASSED if result.passed else VerificationStatus.FAILED
            verification_detail = result.detail
            outcome = RunOutcome.SUCCESS if result.passed else RunOutcome.FAILURE
            await self._emit(
                context,
                type_="verification_completed",
                level=EventLevel.OK if result.passed else EventLevel.ERROR,
                message=(
                    "Scenario verification passed."
                    if result.passed
                    else "Scenario verification failed."
                ),
                detail=result.detail,
            )
        else:
            outcome = RunOutcome.UNVERIFIED

        context.detail.status = RunStatus.COMPLETED
        context.detail.completed_at = _now()
        context.detail.summary = RunSummary(
            outcome=outcome,
            verification=verification,
            verification_detail=verification_detail,
            turns=context.turn_count,
            actions=context.action_count,
            screenshots=len(context.detail.screenshots),
            notes=[note],
        )
        await self._emit(
            context,
            type_="run_completed",
            level=EventLevel.OK if outcome is not RunOutcome.FAILURE else EventLevel.ERROR,
            message=f"Run completed with outcome: {outcome.value}.",
            detail=context.detail.summary.model_dump(mode="json"),
        )

    async def _fail(self, context: RunContext, message: str) -> None:
        context.detail.status = RunStatus.FAILED
        context.detail.completed_at = _now()
        context.detail.summary = RunSummary(
            outcome=RunOutcome.FAILURE,
            verification=VerificationStatus.NOT_RUN,
            turns=context.turn_count,
            actions=context.action_count,
            screenshots=len(context.detail.screenshots),
            notes=[message],
        )
        await self._emit(
            context,
            type_="run_failed",
            level=EventLevel.ERROR,
            message="Run failed.",
            detail=message,
        )

    async def _capture(
        self,
        context: RunContext,
        label: str,
    ) -> Screenshot:
        if context.computer is None:
            raise RuntimeError("browser is not available")
        screenshot = await context.computer.capture()
        sequence = len(context.detail.screenshots) + 1
        filename = f"{sequence:04d}.png"
        await asyncio.to_thread(
            (context.run_dir / "screenshots" / filename).write_bytes,
            screenshot.payload,
        )
        artifact = ScreenshotArtifact(
            id=f"screenshot-{sequence}",
            sequence=sequence,
            label=label,
            captured_at=_now(),
            page_url=screenshot.page_url,
            page_title=screenshot.page_title,
            width=screenshot.width,
            height=screenshot.height,
            url=f"/api/runs/{context.detail.id}/artifacts/screenshots/{filename}",
        )
        context.detail.screenshots.append(artifact)
        await self._emit(
            context,
            type_="screenshot_captured",
            level=EventLevel.OK,
            message=f"Screenshot captured: {label}",
            detail=artifact.model_dump(mode="json"),
            screenshot_id=artifact.id,
        )
        return screenshot

    async def _await_approval(
        self,
        context: RunContext,
        safety: SafetyIntervention,
    ) -> dict[str, Any]:
        intervention = PendingIntervention(
            id=uuid.uuid4().hex,
            kind=safety.kind,  # type: ignore[arg-type]
            title=safety.title,
            message=safety.message,
            created_at=_now(),
            action=safety.action,
            requires_file=safety.requires_file,
        )
        context.detail.pending_intervention = intervention
        context.pending_element_ref = safety.element_ref
        context.pending_future = asyncio.get_running_loop().create_future()
        context.detail.status = RunStatus.WAITING_FOR_APPROVAL
        await self._emit(
            context,
            type_="approval_required",
            level=EventLevel.WARN,
            message=safety.title,
            detail=intervention.model_dump(mode="json"),
        )
        resolution = await context.pending_future
        context.pending_future = None
        context.pending_element_ref = None
        context.detail.pending_intervention = None
        context.detail.status = RunStatus.RUNNING
        await self._emit(
            context,
            type_="approval_resolved",
            level=(EventLevel.OK if resolution.get("decision") == "approve" else EventLevel.WARN),
            message=f"Approval {resolution.get('decision')}.",
        )
        return resolution

    async def _await_user_input(
        self,
        context: RunContext,
        action: CallUserAction,
    ) -> str:
        intervention = PendingIntervention(
            id=uuid.uuid4().hex,
            kind="user_input",
            title="The agent has a question",
            message=action.text,
            created_at=_now(),
            action=action_to_public_dict(action),
        )
        context.detail.pending_intervention = intervention
        context.pending_future = asyncio.get_running_loop().create_future()
        context.detail.status = RunStatus.WAITING_FOR_USER
        await self._emit(
            context,
            type_="user_input_required",
            level=EventLevel.WARN,
            message=action.text,
            detail=intervention.model_dump(mode="json"),
        )
        resolution = await context.pending_future
        context.pending_future = None
        context.detail.pending_intervention = None
        context.detail.status = RunStatus.RUNNING
        return str(resolution["text"])

    def _resolve_pending(
        self,
        context: RunContext,
        resolution: dict[str, Any],
    ) -> None:
        future = context.pending_future
        if future is None or future.done():
            raise ValueError("intervention is no longer pending")
        future.set_result(resolution)

    async def _emit(
        self,
        context: RunContext,
        *,
        type_: str,
        level: EventLevel,
        message: str,
        detail: Any | None = None,
        screenshot_id: str | None = None,
    ) -> None:
        event = RunEvent(
            id=f"{context.detail.id}:{len(context.events)}",
            run_id=context.detail.id,
            sequence=len(context.events),
            type=type_,
            level=level,
            message=message,
            detail=detail,
            screenshot_id=screenshot_id,
            created_at=_now(),
        )
        context.events.append(event)
        await self._persist(context)
        async with context.condition:
            context.condition.notify_all()

    async def _persist(self, context: RunContext) -> None:
        detail_json = context.detail.model_dump_json(indent=2)
        events_text = "".join(f"{event.model_dump_json()}\n" for event in context.events)
        replay_text = json.dumps(
            self._replay_payload(context),
            ensure_ascii=False,
            indent=2,
        )
        await asyncio.gather(
            asyncio.to_thread(
                (context.run_dir / "run.json").write_text,
                detail_json,
                encoding="utf-8",
            ),
            asyncio.to_thread(
                (context.run_dir / "events.jsonl").write_text,
                events_text,
                encoding="utf-8",
            ),
            asyncio.to_thread(
                (context.run_dir / "replay.json").write_text,
                replay_text,
                encoding="utf-8",
            ),
        )

    def _replay_payload(self, context: RunContext) -> dict[str, Any]:
        return {
            "version": 1,
            "run": context.detail.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in context.events],
            "artifacts": {
                "screenshots": [
                    screenshot.model_dump(mode="json") for screenshot in context.detail.screenshots
                ],
                "downloads": (
                    [path.name for path in (context.run_dir / "downloads").iterdir()]
                    if (context.run_dir / "downloads").exists()
                    else []
                ),
            },
        }

    def _context(self, run_id: str) -> RunContext:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError(f"unknown run: {run_id}") from exc


def _process_image(payload: bytes) -> str:
    image = Image.open(BytesIO(payload)).convert("RGB")
    width, height = image.size
    max_pixels = 16 * 16 * 4 * 12_800
    if width * height > max_pixels:
        scale = math.sqrt(max_pixels / (width * height))
        width = max(32, int(width * scale))
        height = max(32, int(height * scale))
    width = max(32, round(width / 32) * 32)
    height = max(32, round(height / 32) * 32)
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
