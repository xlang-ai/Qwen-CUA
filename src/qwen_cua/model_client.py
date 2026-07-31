from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk

from .config import Settings

ModelProgressCallback = Callable[[str], Awaitable[None]]


class QwenModelClient:
    """OpenAI-compatible multimodal Chat Completions client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=180,
            max_retries=0,
        )

    async def close(self) -> None:
        await self.client.close()

    async def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        on_progress: ModelProgressCallback | None = None,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.model_retries + 1):
            try:
                return await self._generate_once(
                    model=model,
                    messages=messages,
                    on_progress=on_progress,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.model_retries:
                    break
                if on_progress is not None:
                    await on_progress(
                        f"Model request failed; retrying ({attempt}/"
                        f"{self.settings.model_retries}): {exc}"
                    )
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        assert last_error is not None
        raise last_error

    async def _generate_once(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        on_progress: ModelProgressCallback | None,
    ) -> str:
        extra_body: dict[str, Any] = {"top_k": self.settings.top_k}
        if not self.settings.enable_thinking:
            extra_body["enable_thinking"] = False

        stream = cast(
            AsyncStream[ChatCompletionChunk],
            await self.client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                top_p=self.settings.top_p,
                stream=True,
                extra_body=extra_body,
            ),
        )
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        token_counter = 0

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = _content_text(getattr(delta, "content", None))
            model_extra = getattr(delta, "model_extra", None) or {}
            reasoning = _content_text(
                getattr(delta, "reasoning_content", None) or model_extra.get("reasoning_content")
            )
            if reasoning:
                reasoning_parts.append(reasoning)
            if content:
                content_parts.append(content)
            token_counter += 1
            if on_progress is not None and token_counter % 64 == 0:
                generated_chars = len("".join(reasoning_parts)) + len("".join(content_parts))
                await on_progress(f"Model is generating… {generated_chars:,} characters")

        reasoning_text = "".join(reasoning_parts).strip()
        content_text = "".join(content_parts).strip()
        if reasoning_text and content_text:
            return f"<think>\n{reasoning_text}\n</think>\n{content_text}"
        return content_text or reasoning_text


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text", "")) for item in value if isinstance(item, dict))
    return str(value)
