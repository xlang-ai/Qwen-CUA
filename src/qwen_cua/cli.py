from __future__ import annotations

import asyncio
import json
from typing import Annotated

import httpx
import typer
import uvicorn
from pydantic import HttpUrl

from .config import Settings
from .models import BrowserMode, StartRunRequest
from .runner import RunnerManager

app = typer.Typer(
    no_args_is_help=True,
    help="Run and inspect Qwen computer-use browser sessions.",
)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
) -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "qwen_cua.api:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=False,
    )


@app.command()
def doctor() -> None:
    async def check() -> None:
        settings = Settings.from_env()
        typer.echo(f"Endpoint: {settings.base_url}")
        typer.echo(f"Models: {', '.join(settings.models)}")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{settings.base_url.rstrip('/')}/models")
                response.raise_for_status()
            typer.secho("Model endpoint: reachable", fg=typer.colors.GREEN)
        except Exception as exc:
            typer.secho(f"Model endpoint: unavailable ({exc})", fg=typer.colors.RED)
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            await browser.close()
            await playwright.stop()
            typer.secho("Playwright Chromium: ready", fg=typer.colors.GREEN)
        except Exception as exc:
            typer.secho(
                f"Playwright Chromium: unavailable ({exc})",
                fg=typer.colors.RED,
            )

    asyncio.run(check())


@app.command("run")
def run_command(
    prompt: Annotated[str, typer.Option(help="Task instruction")],
    scenario: Annotated[str | None, typer.Option()] = "kanban-reprioritize",
    url: Annotated[str | None, typer.Option()] = None,
    model: Annotated[str | None, typer.Option()] = None,
    headful: Annotated[bool, typer.Option()] = False,
    max_turns: Annotated[int, typer.Option(min=1, max=100)] = 50,
) -> None:
    if bool(scenario) == bool(url):
        raise typer.BadParameter("provide exactly one of --scenario or --url")

    async def execute() -> None:
        settings = Settings.from_env()
        manager = RunnerManager(settings)
        try:
            request = StartRunRequest(
                prompt=prompt,
                model=model,
                scenario_id=scenario,
                target_url=HttpUrl(url) if url else None,
                browser_mode=(BrowserMode.HEADFUL if headful else BrowserMode.HEADLESS),
                max_turns=max_turns,
                custom_url_risk_acknowledged=bool(url),
            )
            started = await manager.start_run(request)
            async for event in manager.wait_for_events(
                started.run_id,
                after_sequence=-1,
            ):
                if event is not None:
                    typer.echo(event.model_dump_json())
            detail = await manager.get_run(started.run_id)
            typer.echo(json.dumps(detail.model_dump(mode="json"), indent=2))
            typer.echo(f"Replay: {settings.data_root / 'runs' / started.run_id / 'replay.json'}")
        finally:
            await manager.close()

    asyncio.run(execute())
