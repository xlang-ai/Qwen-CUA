from __future__ import annotations

import json
from pathlib import Path

from qwen_cua.api import create_app
from qwen_cua.config import Settings


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = repo_root / "apps" / "web" / "openapi.json"
    schema = create_app(Settings.from_env()).openapi()
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
