from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def _base_data_dir() -> Path:
    # Keep writable data outside source tree when possible, but default to project-local.
    configured = os.environ.get("TRADING_BOT_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[4] / "data" / "trading").resolve()


def equities_path_for_user(user_id: str) -> Path:
    base = _base_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"equities.{user_id}.json"


def load_equities(user_id: str) -> Dict[str, Any]:
    path = equities_path_for_user(user_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_equities(user_id: str, equities: Dict[str, Any]) -> None:
    path = equities_path_for_user(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(equities, indent=2, sort_keys=True), encoding="utf-8")

