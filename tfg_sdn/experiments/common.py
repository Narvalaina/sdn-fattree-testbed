from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def repo_root() -> Path:
    # .../TFG-SDN/tfg_sdn/experiments/common.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_id(ts: Optional[str], topo: str, exp: str, tag: str) -> str:
    ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = tag.replace(" ", "_")
    return f"{ts}_{topo}_{exp}_{safe}"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def append_csv(path: Path, row: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)


def sh(cmd: list[str], timeout_s: int = 10) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def try_get_env_snapshot() -> Dict[str, Any]:
    snap: Dict[str, Any] = {"ts": iso_now()}

    def _best(cmd: list[str], key: str, timeout_s: int = 8) -> None:
        rc, out, err = sh(cmd, timeout_s=timeout_s)
        val = out if out else err
        snap[key] = {"rc": rc, "value": val}

    _best(["java", "-version"], "java_version")
    _best(["ovs-vsctl", "--version"], "ovs_vsctl_version")
    _best(["ovs-ofctl", "--version"], "ovs_ofctl_version")
    _best(["mn", "--version"], "mininet_version")
    _best(["uname", "-a"], "uname")
    _best(["git", "rev-parse", "HEAD"], "git_head")

    return snap
