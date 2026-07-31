from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    web_origin: str
    data_root: Path
    labs_root: Path
    base_url: str
    api_key: str
    models: tuple[str, ...]
    default_model: str
    enable_thinking: bool
    max_tokens: int
    temperature: float
    top_p: float
    top_k: int
    model_retries: int
    max_concurrent_runs: int
    allow_private_urls: bool
    docker_mode: bool
    default_max_turns: int
    history_n: int
    image_max: int
    viewport_width: int
    viewport_height: int
    max_upload_bytes: int

    @classmethod
    def from_env(cls) -> Settings:
        repo_root = Path(__file__).resolve().parents[2]
        models = tuple(
            model.strip()
            for model in os.getenv("QWEN_CUA_MODELS", "Qwen/Qwen-CUA").split(",")
            if model.strip()
        )
        if not models:
            models = ("Qwen/Qwen-CUA",)
        default_model = os.getenv("QWEN_CUA_DEFAULT_MODEL", models[0]).strip()
        if default_model not in models:
            models = (default_model, *models)
        return cls(
            host=os.getenv("QWEN_CUA_HOST", "127.0.0.1"),
            port=_env_int("QWEN_CUA_PORT", 4001),
            web_origin=os.getenv("QWEN_CUA_WEB_ORIGIN", "http://127.0.0.1:3000"),
            data_root=Path(os.getenv("QWEN_CUA_DATA_ROOT", repo_root / "data")).resolve(),
            labs_root=Path(os.getenv("QWEN_CUA_LABS_ROOT", repo_root / "labs")).resolve(),
            base_url=os.getenv("QWEN_CUA_BASE_URL", "http://127.0.0.1:8000/v1"),
            api_key=os.getenv("QWEN_CUA_API_KEY", "dummy"),
            models=models,
            default_model=default_model,
            enable_thinking=_env_bool("QWEN_CUA_ENABLE_THINKING", True),
            max_tokens=_env_int("QWEN_CUA_MAX_TOKENS", 32768),
            temperature=_env_float("QWEN_CUA_TEMPERATURE", 0.6),
            top_p=_env_float("QWEN_CUA_TOP_P", 0.95),
            top_k=_env_int("QWEN_CUA_TOP_K", 20),
            model_retries=max(1, _env_int("QWEN_CUA_MODEL_RETRIES", 3)),
            max_concurrent_runs=max(1, _env_int("QWEN_CUA_MAX_CONCURRENT_RUNS", 1)),
            allow_private_urls=_env_bool("QWEN_CUA_ALLOW_PRIVATE_URLS", False),
            docker_mode=_env_bool("QWEN_CUA_DOCKER", False),
            default_max_turns=min(
                100,
                max(1, _env_int("QWEN_CUA_DEFAULT_MAX_TURNS", 50)),
            ),
            history_n=max(1, _env_int("QWEN_CUA_HISTORY_N", 50)),
            image_max=max(1, _env_int("QWEN_CUA_IMAGE_MAX", 20)),
            viewport_width=max(640, _env_int("QWEN_CUA_VIEWPORT_WIDTH", 1920)),
            viewport_height=max(480, _env_int("QWEN_CUA_VIEWPORT_HEIGHT", 1080)),
            max_upload_bytes=max(
                1,
                _env_int("QWEN_CUA_MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
            ),
        )
