"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import type {
  ModelInfo,
  PendingIntervention,
  RunDetail,
  RunEvent,
  ScenarioManifest,
  ScreenshotArtifact,
  StartRunRequest,
  StartRunResponse,
} from "@/lib/contracts";

type TargetMode = "scenario" | "url";
type PendingAction = "start" | "stop" | "approval" | "input" | null;

export function OperatorConsole({
  runnerBaseUrl,
}: {
  runnerBaseUrl: string;
}) {
  const [scenarios, setScenarios] = useState<ScenarioManifest[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [runnerOnline, setRunnerOnline] = useState(false);
  const [issue, setIssue] = useState<string | null>(null);
  const [targetMode, setTargetMode] = useState<TargetMode>("scenario");
  const [scenarioId, setScenarioId] = useState("");
  const [targetUrl, setTargetUrl] = useState("https://example.com");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [browserMode, setBrowserMode] = useState<"headless" | "headful">(
    "headless",
  );
  const [maxTurns, setMaxTurns] = useState(50);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [selectedScreenshotId, setSelectedScreenshotId] = useState<
    string | null
  >(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [userInput, setUserInput] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);
  const activityRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchJson<ScenarioManifest[]>(`${runnerBaseUrl}/api/scenarios`),
      fetchJson<ModelInfo[]>(`${runnerBaseUrl}/api/models`),
      fetchJson(`${runnerBaseUrl}/health`),
    ])
      .then(([scenarioData, modelData]) => {
        if (cancelled) return;
        setScenarios(scenarioData);
        setModels(modelData);
        setScenarioId(scenarioData[0]?.id ?? "");
        setPrompt(scenarioData[0]?.default_prompt ?? "");
        setModel(
          modelData.find((item) => item.is_default)?.id ??
            modelData[0]?.id ??
            "",
        );
        setRunnerOnline(true);
        setIssue(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setRunnerOnline(false);
        setIssue(toMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [runnerBaseUrl]);

  useEffect(() => {
    if (!run || terminalStatuses.has(run.status)) {
      eventSourceRef.current?.close();
      return;
    }
    eventSourceRef.current?.close();
    const source = new EventSource(
      `${runnerBaseUrl}${run.event_stream_url}`,
    );
    eventSourceRef.current = source;
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((current) => {
        if (current.some((item) => item.id === event.id)) return current;
        return [...current, event].sort((a, b) => a.sequence - b.sequence);
      });
      void refreshRun(run.id);
    };
    source.onerror = () => {
      void refreshRun(run.id).then((detail) => {
        if (detail && !terminalStatuses.has(detail.status)) {
          setIssue("Live event stream disconnected; the console will reconnect.");
        }
      });
    };
    return () => source.close();
    // Reconnect only when the run id changes; EventSource handles transient retries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, runnerBaseUrl]);

  useEffect(() => {
    const latest = run?.screenshots?.at(-1);
    if (latest) setSelectedScreenshotId(latest.id);
  }, [run?.screenshots]);

  useEffect(() => {
    activityRef.current?.scrollTo?.({
      top: activityRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [events]);

  const selectedScenario = scenarios.find((item) => item.id === scenarioId);
  const screenshots = run?.screenshots ?? [];
  const selectedScreenshot =
    screenshots.find((item) => item.id === selectedScreenshotId) ??
    screenshots.at(-1) ??
    null;
  const selectedIndex = selectedScreenshot
    ? screenshots.findIndex((item) => item.id === selectedScreenshot.id)
    : -1;
  const locked = Boolean(
    run && !terminalStatuses.has(run.status),
  );
  const canStart =
    runnerOnline &&
    !locked &&
    pendingAction === null &&
    prompt.trim().length > 0 &&
    model.length > 0 &&
    (targetMode === "scenario"
      ? Boolean(scenarioId)
      : Boolean(targetUrl && riskAcknowledged));

  async function refreshRun(runId: string) {
    try {
      const detail = await fetchJson<RunDetail>(
        `${runnerBaseUrl}/api/runs/${runId}`,
      );
      setRun(detail);
      if (terminalStatuses.has(detail.status)) {
        eventSourceRef.current?.close();
        setIssue(null);
      }
      return detail;
    } catch (error) {
      setIssue(toMessage(error));
      return null;
    }
  }

  async function startRun() {
    if (!canStart) return;
    setPendingAction("start");
    setIssue(null);
    setEvents([]);
    setRun(null);
    setSelectedScreenshotId(null);
    const request: StartRunRequest = {
      prompt,
      model,
      browser_mode: browserMode,
      max_turns: maxTurns,
      scenario_id: targetMode === "scenario" ? scenarioId : null,
      target_url: targetMode === "url" ? targetUrl : null,
      custom_url_risk_acknowledged:
        targetMode === "url" && riskAcknowledged,
    };
    try {
      const started = await fetchJson<StartRunResponse>(
        `${runnerBaseUrl}/api/runs`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(request),
        },
      );
      await refreshRun(started.run_id);
    } catch (error) {
      setIssue(toMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function stopRun() {
    if (!run) return;
    setPendingAction("stop");
    try {
      const detail = await fetchJson<RunDetail>(
        `${runnerBaseUrl}/api/runs/${run.id}/stop`,
        { method: "POST" },
      );
      setRun(detail);
    } catch (error) {
      setIssue(toMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function resolveApproval(decision: "approve" | "reject") {
    const intervention = run?.pending_intervention;
    if (!run || !intervention) return;
    setPendingAction("approval");
    try {
      const detail = await fetchJson<RunDetail>(
        `${runnerBaseUrl}/api/runs/${run.id}/approvals/${intervention.id}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision }),
        },
      );
      setRun(detail);
    } catch (error) {
      setIssue(toMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function uploadApprovedFile(file: File) {
    const intervention = run?.pending_intervention;
    if (!run || !intervention) return;
    setPendingAction("approval");
    const data = new FormData();
    data.append("file", file);
    try {
      const detail = await fetchJson<RunDetail>(
        `${runnerBaseUrl}/api/runs/${run.id}/interventions/${intervention.id}/file`,
        { method: "POST", body: data },
      );
      setRun(detail);
    } catch (error) {
      setIssue(toMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  async function sendUserInput(event: FormEvent) {
    event.preventDefault();
    const intervention = run?.pending_intervention;
    if (!run || !intervention || !userInput.trim()) return;
    setPendingAction("input");
    try {
      const detail = await fetchJson<RunDetail>(
        `${runnerBaseUrl}/api/runs/${run.id}/user-input`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            intervention_id: intervention.id,
            text: userInput,
          }),
        },
      );
      setRun(detail);
      setUserInput("");
    } catch (error) {
      setIssue(toMessage(error));
    } finally {
      setPendingAction(null);
    }
  }

  function changeScenario(nextId: string) {
    setScenarioId(nextId);
    const next = scenarios.find((item) => item.id === nextId);
    if (next) setPrompt(next.default_prompt);
  }

  const stageTitle = run
    ? run.status.replaceAll("_", " ")
    : runnerOnline
      ? "Ready for a run"
      : "Runner offline";
  const turnCount = events.filter(
    (event) => event.type === "model_turn_started",
  ).length;
  const actionCount = events.filter(
    (event) => event.type === "action_completed",
  ).length;

  return (
    <main className="appShell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark" aria-hidden="true" />
          <div>
            <strong>Qwen CUA</strong>
            <span>Browser Agent Studio</span>
          </div>
        </div>
        <div className={`runnerStatus ${runnerOnline ? "online" : "offline"}`}>
          <span />
          {runnerOnline ? "Runner online" : "Runner offline"}
        </div>
      </header>

      <section className="workspace">
        <aside className="sideRail">
          <section className="panel composer">
          <div className="composerTop">
            <div className="panelHeading">
              <p>New browser run</p>
              <h1>What should Qwen do?</h1>
            </div>

            <div className="segmented" aria-label="Target mode">
              <button
                className={targetMode === "scenario" ? "active" : ""}
                disabled={locked}
                onClick={() => setTargetMode("scenario")}
                type="button"
              >
                Safe lab
              </button>
              <button
                className={targetMode === "url" ? "active" : ""}
                disabled={locked}
                onClick={() => setTargetMode("url")}
                type="button"
              >
                Custom URL
              </button>
            </div>
          </div>

          <div className="targetBlock">
            {targetMode === "scenario" ? (
              <label className="field targetField">
                <span>Scenario</span>
                <select
                  disabled={locked}
                  onChange={(event) => changeScenario(event.target.value)}
                  value={scenarioId}
                >
                  {scenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.title}
                    </option>
                  ))}
                </select>
                <small>{selectedScenario?.description}</small>
              </label>
            ) : (
              <div className="customTarget">
                <label className="field targetField">
                  <span>Target URL</span>
                  <input
                    disabled={locked}
                    onChange={(event) => setTargetUrl(event.target.value)}
                    type="url"
                    value={targetUrl}
                  />
                </label>
                <label className="riskCheck">
                  <input
                    checked={riskAcknowledged}
                    disabled={locked}
                    onChange={(event) =>
                      setRiskAcknowledged(event.target.checked)
                    }
                    type="checkbox"
                  />
                  <span>
                    I understand that arbitrary websites may contain prompt
                    injection or unsafe actions.
                  </span>
                </label>
              </div>
            )}
          </div>

          <label className="field promptField">
            <span className="srOnly">What should Qwen do?</span>
            <textarea
              disabled={locked}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Describe the outcome you want…"
              rows={3}
              value={prompt}
            />
          </label>

          <div className="composerBottom">
            <details className="advanced">
              <summary>
                <span>Run settings</span>
                <span className="settingsSummary">
                  {model || "Model"}
                  <span aria-hidden="true">⌄</span>
                </span>
              </summary>
              <div className="advancedGrid">
                <label className="field">
                  <span>Model</span>
                  <select
                    disabled={locked}
                    onChange={(event) => setModel(event.target.value)}
                    value={model}
                  >
                    {models.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.id}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Browser</span>
                  <select
                    disabled={locked}
                    onChange={(event) =>
                      setBrowserMode(
                        event.target.value as "headless" | "headful",
                      )
                    }
                    value={browserMode}
                  >
                    <option value="headless">Headless</option>
                    <option value="headful">Visible</option>
                  </select>
                </label>
                <label className="field">
                  <span>Turn budget · {maxTurns}</span>
                  <input
                    disabled={locked}
                    max={100}
                    min={1}
                    onChange={(event) =>
                      setMaxTurns(Number(event.target.value))
                    }
                    type="range"
                    value={maxTurns}
                  />
                </label>
              </div>
            </details>

            <div className="primaryActions">
              <button
                className="startButton"
                disabled={!canStart}
                onClick={() => void startRun()}
                type="button"
              >
                <span className="buttonIcon" aria-hidden="true">
                  {pendingAction === "start" ? "···" : "↗"}
                </span>
                {pendingAction === "start" ? "Starting…" : "Start"}
              </button>
              <button
                className="stopButton"
                disabled={!locked || pendingAction !== null}
                onClick={() => void stopRun()}
                type="button"
              >
                <span aria-hidden="true">■</span>
                {pendingAction === "stop" ? "Stopping…" : "Stop"}
              </button>
            </div>
          </div>
          </section>

          {run ? (
            <details className="runDrawer" open={locked}>
              <summary>
                <span className="drawerTitle">
                  <span className={locked ? "drawerDot live" : "drawerDot"} />
                  <span>
                    <strong>Run details</strong>
                    <small>
                      {events.length} events · {screenshots.length} frames
                    </small>
                  </span>
                </span>
                <span className="drawerAction">
                  {locked ? "Live" : "Review"}
                  <span aria-hidden="true">⌄</span>
                </span>
              </summary>
              <div className="runDrawerContent">
                <section className="activityPanel">
                  <div className="activityHeader">
                    <div>
                      <p>Agent trace</p>
                      <h2>Activity</h2>
                    </div>
                    <span className={locked ? "traceLive" : ""}>
                      {locked ? "Live" : `${events.length} events`}
                    </span>
                  </div>
                  <div className="activityFeed" ref={activityRef}>
                    {events.length === 0 ? (
                      <div className="emptyActivity">
                        <strong>Waiting for the first action</strong>
                        <span>
                          Observations and checkpoints will appear here.
                        </span>
                      </div>
                    ) : (
                      events.map((event) => (
                        <ActivityRow
                          event={event}
                          key={event.id}
                          onScreenshot={(id) =>
                            setSelectedScreenshotId(id)
                          }
                        />
                      ))
                    )}
                  </div>
                </section>
                <ScreenshotTimeline
                  runnerBaseUrl={runnerBaseUrl}
                  screenshots={screenshots}
                  selected={selectedScreenshot}
                  onSelect={setSelectedScreenshotId}
                />
              </div>
            </details>
          ) : null}
        </aside>

        <section className="stageColumn">
          <div className="stageHeader">
            <div>
              <p>Live browser</p>
              <h2>{stageTitle}</h2>
              <span>
                {selectedScreenshot?.page_url ??
                  run?.target_url ??
                  "Choose a task and start the agent."}
              </span>
            </div>
            <div className="stageMeta">
              {run ? (
                <div className="runMetrics">
                  <span>
                    <strong>{turnCount}</strong> turns
                  </span>
                  <span>
                    <strong>{actionCount}</strong> actions
                  </span>
                  <span>
                    <strong>{screenshots.length}</strong> frames
                  </span>
                </div>
              ) : null}
              {run?.summary ? (
                <span className={`outcome ${run.summary.outcome}`}>
                  {run.summary.outcome}
                </span>
              ) : null}
              {run ? <span>{run.model}</span> : null}
              {run ? (
                <a
                  className="replayLink"
                  href={`${runnerBaseUrl}${run.replay_url}`}
                  rel="noreferrer"
                  target="_blank"
                >
                  Replay JSON
                </a>
              ) : null}
            </div>
          </div>

          {issue ? <div className="issueBanner">{issue}</div> : null}
          {run?.pending_intervention ? (
            <InterventionCard
              intervention={run.pending_intervention}
              pending={pendingAction !== null}
              userInput={userInput}
              onApprove={() => void resolveApproval("approve")}
              onReject={() => void resolveApproval("reject")}
              onFile={(file) => void uploadApprovedFile(file)}
              onInputChange={setUserInput}
              onSubmitInput={sendUserInput}
            />
          ) : null}

          <div className="browserFrame">
            <div className="browserChrome">
              <div className="windowDots" aria-hidden="true">
                <span className="windowDot red" />
                <span className="windowDot amber" />
                <span className="windowDot green" />
              </div>
              <div className="browserControls" aria-hidden="true">
                <span>‹</span>
                <span>›</span>
                <span>↻</span>
              </div>
              <div className="addressBar">
                <span aria-hidden="true">⌁</span>
                <strong>
                  {selectedScreenshot?.page_url ?? "about:blank"}
                </strong>
              </div>
              <div className="browserMenu" aria-hidden="true">•••</div>
            </div>
            <div className="browserCanvas">
              {selectedScreenshot ? (
                // Artifact images are dynamic and intentionally bypass Next image optimization.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  alt={`Browser frame ${selectedIndex + 1}`}
                  src={`${runnerBaseUrl}${selectedScreenshot.url}`}
                />
              ) : (
                <div className="stageEmpty">
                  <div className="stageGlyph" aria-hidden="true" />
                  <h3>Browser ready</h3>
                  <p>Start a mission and Qwen&apos;s view will appear here.</p>
                </div>
              )}
              {selectedScreenshot ? (
                <span className="canvasBadge">
                  Frame {selectedIndex + 1} of {screenshots.length}
                </span>
              ) : null}
            </div>
          </div>

        </section>
      </section>
    </main>
  );
}

function ActivityRow({
  event,
  onScreenshot,
}: {
  event: RunEvent;
  onScreenshot: (id: string) => void;
}) {
  const detail =
    event.detail == null
      ? ""
      : typeof event.detail === "string"
        ? event.detail
        : JSON.stringify(event.detail, null, 2);
  return (
    <details className={`activityRow level-${event.level}`}>
      <summary>
        <span className="eventDot" />
        <span className="eventCopy">
          <strong>{event.message}</strong>
          <small>
            {humanize(event.type)} ·{" "}
            {new Date(event.created_at).toLocaleTimeString()}
          </small>
        </span>
        {event.screenshot_id ? (
          <button
            onClick={(click) => {
              click.preventDefault();
              onScreenshot(event.screenshot_id!);
            }}
            type="button"
          >
            Frame
          </button>
        ) : null}
      </summary>
      {detail ? <pre>{detail}</pre> : null}
    </details>
  );
}

function InterventionCard({
  intervention,
  pending,
  userInput,
  onApprove,
  onReject,
  onFile,
  onInputChange,
  onSubmitInput,
}: {
  intervention: PendingIntervention;
  pending: boolean;
  userInput: string;
  onApprove: () => void;
  onReject: () => void;
  onFile: (file: File) => void;
  onInputChange: (value: string) => void;
  onSubmitInput: (event: FormEvent) => void;
}) {
  return (
    <section className="intervention">
      <div className="interventionIcon">!</div>
      <div>
        <p>Operator intervention</p>
        <h3>{intervention.title}</h3>
        <span>{intervention.message}</span>
        {intervention.kind === "user_input" ? (
          <form className="interventionInput" onSubmit={onSubmitInput}>
            <input
              onChange={(event) => onInputChange(event.target.value)}
              placeholder="Type your response"
              value={userInput}
            />
            <button disabled={pending || !userInput.trim()} type="submit">
              Send
            </button>
          </form>
        ) : intervention.requires_file ? (
          <div className="interventionActions">
            <label className="fileButton">
              Choose file
              <input
                disabled={pending}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onFile(file);
                }}
                type="file"
              />
            </label>
            <button disabled={pending} onClick={onReject} type="button">
              Reject
            </button>
          </div>
        ) : (
          <div className="interventionActions">
            <button
              className="approve"
              disabled={pending}
              onClick={onApprove}
              type="button"
            >
              Approve once
            </button>
            <button disabled={pending} onClick={onReject} type="button">
              Reject
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

function ScreenshotTimeline({
  runnerBaseUrl,
  screenshots,
  selected,
  onSelect,
}: {
  runnerBaseUrl: string;
  screenshots: ScreenshotArtifact[];
  selected: ScreenshotArtifact | null;
  onSelect: (id: string) => void;
}) {
  const frames = useMemo(() => screenshots, [screenshots]);
  return (
    <section className="timeline">
      <div className="timelineHeader">
        <div>
          <p>Run review</p>
          <h3>Screenshot timeline</h3>
        </div>
        <span>{frames.length} frames</span>
      </div>
      <div className="filmstrip">
        {frames.length === 0 ? (
          <div className="emptyFilmstrip">No captured frames yet.</div>
        ) : (
          frames.map((frame) => (
            <button
              className={frame.id === selected?.id ? "selected" : ""}
              key={frame.id}
              onClick={() => onSelect(frame.id)}
              type="button"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img alt="" src={`${runnerBaseUrl}${frame.url}`} />
              <span>
                <strong>Frame {frame.sequence}</strong>
                <small>{frame.label}</small>
              </span>
            </button>
          ))
        )}
      </div>
    </section>
  );
}

const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

async function fetchJson<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      payload?.detail ?? payload?.error ?? `Request failed (${response.status})`,
    );
  }
  return payload as T;
}

function toMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function humanize(value: string) {
  return value.replaceAll("_", " ");
}
