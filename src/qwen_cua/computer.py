from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Page,
    Playwright,
    async_playwright,
)

from .actions import (
    CallUserAction,
    ClickAction,
    ComputerAction,
    CoordinateAction,
    KeyAction,
    ScreenshotAction,
    ScrollAction,
    TerminateAction,
    TypeAction,
    WaitAction,
)
from .models import BrowserMode


@dataclass(frozen=True, slots=True)
class Screenshot:
    payload: bytes
    page_url: str
    page_title: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TargetInspection:
    element_ref: str
    tag: str
    input_type: str
    text: str
    href: str
    form_action: str
    download: bool
    is_password: bool
    is_file: bool
    is_submit: bool


class BrowserComputer:
    def __init__(
        self,
        *,
        browser_mode: BrowserMode,
        width: int,
        height: int,
        downloads_dir: Path,
    ):
        self.browser_mode = browser_mode
        self.width = width
        self.height = height
        self.downloads_dir = downloads_dir
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.downloaded_files: list[Path] = []
        self.cursor_x = 0
        self.cursor_y = 0

    async def start(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.browser_mode is BrowserMode.HEADLESS,
            args=[f"--window-size={self.width},{self.height}"],
        )
        self.context = await self.browser.new_context(
            viewport={"width": self.width, "height": self.height},
            accept_downloads=True,
        )
        self.page = await self.context.new_page()
        self.page.on("download", self._on_download)

    async def close(self) -> None:
        if self.context is not None:
            with contextlib.suppress(Exception):
                await self.context.close()
        if self.browser is not None:
            with contextlib.suppress(Exception):
                await self.browser.close()
        if self.playwright is not None:
            with contextlib.suppress(Exception):
                await self.playwright.stop()

    async def navigate(self, url: str) -> None:
        page = self._page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    async def capture(self) -> Screenshot:
        page = self._page()
        payload = await page.screenshot(type="png")
        title = await page.title()
        return Screenshot(
            payload=payload,
            page_url=page.url,
            page_title=title,
            width=self.width,
            height=self.height,
        )

    def current_url(self) -> str:
        return self._page().url

    async def execute(self, action: ComputerAction) -> None:
        page = self._page()
        if isinstance(action, KeyAction):
            keys = [_normalize_key(key) for key in action.keys]
            if action.action == "key":
                if len(keys) > 1:
                    await page.keyboard.press("+".join(keys))
                else:
                    await page.keyboard.press(keys[0])
            elif action.action == "key_down":
                for key in keys:
                    await page.keyboard.down(key)
            else:
                for key in reversed(keys):
                    await page.keyboard.up(key)
            return
        if isinstance(action, TypeAction):
            await page.keyboard.insert_text(action.text)
            return
        if isinstance(action, CoordinateAction):
            x, y = self.scale_coordinate(action.coordinate)
            if action.action == "mouse_move":
                await page.mouse.move(x, y)
                self.cursor_x, self.cursor_y = x, y
            else:
                await page.mouse.down(button="left")
                await page.mouse.move(x, y, steps=max(2, int(action.duration * 20)))
                await page.mouse.up(button="left")
                self.cursor_x, self.cursor_y = x, y
            return
        if isinstance(action, ClickAction):
            if action.coordinate is not None:
                x, y = self.scale_coordinate(action.coordinate)
                await page.mouse.move(x, y)
                self.cursor_x, self.cursor_y = x, y
            if action.action == "left_click":
                await page.mouse.click(
                    *self._coordinate_or_current(action.coordinate),
                    button="left",
                )
            elif action.action == "right_click":
                await page.mouse.click(
                    *self._coordinate_or_current(action.coordinate),
                    button="right",
                )
            elif action.action == "middle_click":
                await page.mouse.click(
                    *self._coordinate_or_current(action.coordinate),
                    button="middle",
                )
            elif action.action == "double_click":
                await page.mouse.dblclick(*self._coordinate_or_current(action.coordinate))
            elif action.action == "triple_click":
                await page.mouse.click(
                    *self._coordinate_or_current(action.coordinate),
                    click_count=3,
                )
            elif action.action == "left_mouse_down":
                await page.mouse.down(button="left")
            else:
                await page.mouse.up(button="left")
            return
        if isinstance(action, ScrollAction):
            if action.action == "scroll":
                await page.mouse.wheel(0, -action.pixels)
            else:
                await page.mouse.wheel(action.pixels, 0)
            return
        if isinstance(action, WaitAction):
            await asyncio.sleep(action.time)
            return
        if isinstance(action, ScreenshotAction):
            return
        if isinstance(action, (TerminateAction, CallUserAction)):
            raise ValueError(f"{action.action} must be handled by the runner")
        raise TypeError(f"unsupported action type: {type(action).__name__}")

    async def inspect_target(self, action: ComputerAction) -> TargetInspection | None:
        coordinate = getattr(action, "coordinate", None)
        if coordinate is None and not isinstance(action, TypeAction):
            return None
        element_ref = f"qwen-cua-{uuid.uuid4().hex}"
        result = await self._page().evaluate(
            """
            ({ lookupKind, x, y, ref }) => {
              let element = lookupKind === "point"
                ? document.elementFromPoint(x, y)
                : document.activeElement;
              if (!element) return null;
              element = element.closest(
                'a,button,input,textarea,select,[role="button"],[onclick]'
              ) || element;
              element.setAttribute("data-qwen-cua-ref", ref);
              const tag = (element.tagName || "").toLowerCase();
              const inputType = (element.getAttribute("type") || "").toLowerCase();
              const form = element.form || element.closest("form");
              const text = (
                element.getAttribute("aria-label") ||
                element.innerText ||
                element.value ||
                ""
              ).trim().slice(0, 240);
              return {
                element_ref: ref,
                tag,
                input_type: inputType,
                text,
                href: element.href || "",
                form_action: form ? (form.action || "") : "",
                download: element.hasAttribute("download"),
                is_password: tag === "input" && inputType === "password",
                is_file: tag === "input" && inputType === "file",
                is_submit:
                  (tag === "button" && (!inputType || inputType === "submit")) ||
                  (tag === "input" && inputType === "submit"),
              };
            }
            """,
            {
                "lookupKind": "point" if coordinate is not None else "active",
                "x": self.scale_coordinate(coordinate)[0] if coordinate is not None else 0,
                "y": self.scale_coordinate(coordinate)[1] if coordinate is not None else 0,
                "ref": element_ref,
            },
        )
        return TargetInspection(**result) if result else None

    async def set_input_file(self, element_ref: str, path: Path) -> None:
        locator = self._page().locator(f'[data-qwen-cua-ref="{element_ref}"]')
        await locator.set_input_files(str(path))

    async def read_lab_state(self) -> dict[str, Any] | None:
        return cast(
            dict[str, Any] | None,
            await self._page().evaluate(
                "() => window.__QWEN_CUA_STATE__ ? window.__QWEN_CUA_STATE__() : null"
            ),
        )

    def scale_coordinate(self, coordinate: tuple[int, int]) -> tuple[int, int]:
        x = round(coordinate[0] / 999 * (self.width - 1))
        y = round(coordinate[1] / 999 * (self.height - 1))
        return x, y

    def _coordinate_or_current(self, coordinate: tuple[int, int] | None) -> tuple[int, int]:
        if coordinate is None:
            return self.cursor_x, self.cursor_y
        return self.scale_coordinate(coordinate)

    def _page(self) -> Page:
        if self.page is None:
            raise RuntimeError("browser computer is not started")
        return self.page

    def _on_download(self, download: Download) -> None:
        async def save() -> None:
            filename = Path(download.suggested_filename).name
            destination = self.downloads_dir / filename
            await download.save_as(destination)
            self.downloaded_files.append(destination)

        asyncio.create_task(save())


def _normalize_key(key: str) -> str:
    lookup = key.strip().lower()
    mapping = {
        "ctrl": "Control",
        "control": "Control",
        "cmd": "Meta",
        "command": "Meta",
        "meta": "Meta",
        "option": "Alt",
        "alt": "Alt",
        "shift": "Shift",
        "enter": "Enter",
        "return": "Enter",
        "esc": "Escape",
        "escape": "Escape",
        "space": "Space",
        "tab": "Tab",
        "backspace": "Backspace",
        "delete": "Delete",
        "up": "ArrowUp",
        "down": "ArrowDown",
        "left": "ArrowLeft",
        "right": "ArrowRight",
        "pageup": "PageUp",
        "pagedown": "PageDown",
    }
    return mapping.get(lookup, key if len(key) == 1 else key.title())
