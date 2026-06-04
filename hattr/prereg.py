from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


HASH_KEYS = ("tasks", "conditions", "scorer", "analysis")


def config_hash(raw: dict[str, Any]) -> str:
    normalized = {key: raw.get(key) for key in HASH_KEYS}
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preregister(study_path: Path, raw: dict[str, Any], timestamp: str) -> Path:
    out = study_path.with_suffix(".prereg.json")
    data = {"study": str(study_path), "hash": config_hash(raw), "timestamp": timestamp}
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    subprocess.run(["git", "add", str(out)], check=False)
    subprocess.run(
        ["git", "commit", "-m", f"Preregister {study_path.name}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return out


def drift_warning(study_path: Path, raw: dict[str, Any]) -> str | None:
    prereg_path = study_path.with_suffix(".prereg.json")
    if not prereg_path.exists():
        return None
    with prereg_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    old_hash = data.get("hash")
    current = config_hash(raw)
    if old_hash != current:
        return f"preregistration drift: current config hash {current} != {old_hash}"
    return None

