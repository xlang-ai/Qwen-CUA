"""Qwen CUA browser agent and operator-console runner."""

from .config import Settings
from .runner import RunnerManager

__all__ = ["RunnerManager", "Settings"]
__version__ = "0.1.0"
