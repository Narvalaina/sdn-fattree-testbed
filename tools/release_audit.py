#!/usr/bin/env python3
"""Portable pre-release audit for the curated public repository."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_NAMES = {".gitignore", ".gitattributes", "tfgctl"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".csv", ".txt", ".example", ".sh"}
FORBIDDEN_DIRS = {"__pycache__", ".pytest_cache", ".venv", "venv", "runs", "00_Governance", "build", "dist"}
FORBIDDEN_PATTERNS = {
    "linux_home_path": re.compile("/" + "home" + r"/[A-Za-z0-9_.-]+/"),
    "windows_user_path": re.compile(r"[A-Za-z]:\\\\Users\\\\", re.IGNORECASE),
    "cloud_sync_path": re.compile("One" + "Drive", re.IGNORECASE),
    "legacy_hostname": re.compile("Lenovo" + "-G50-80", re.IGNORECASE),
    "legacy_username": re.compile("nar" + "valaina", re.IGNORECASE),
    "hardcoded_admin_admin": re.compile("admin" + ":" + "admin", re.IGNORECASE),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
REQUIRED_FILES = [
    "README.md",
    ".gitignore",
    ".gitattributes",
    ".env.example",
    "pyproject.toml",
    "docs/architecture.md",
    "docs/environment.md",
    "docs/methodology.md",
    "docs/reproducibility.md",
    "docs/results.md",
    "docs/limitations.md",
    "docs/script_catalog.md",
    "docs/linux_validation.md",
    "docs/rights_and_reuse.md",
    "tools/native_linux_release_check.sh",
    "data/README.md",
    "data/SHA256SUMS",
]


def iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in {".git"} for part in rel.parts):
            continue
        if path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_required(errors: list[str]) -> None:
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required file: {rel}")


def audit_forbidden_dirs(errors: list[str]) -> None:
    for path in ROOT.rglob("*"):
        if path.is_dir() and (path.name in FORBIDDEN_DIRS or path.name.endswith(".egg-info")):
            errors.append(f"forbidden directory present: {path.relative_to(ROOT)}")


def audit_text(errors: list[str]) -> None:
    for path in iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Historical CSVs may contain byte-preserved line endings, but are UTF-8/ASCII.
            text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {path.relative_to(ROOT)}")


def audit_data_hashes(errors: list[str]) -> None:
    manifest = ROOT / "data" / "SHA256SUMS"
    if not manifest.is_file():
        return
    for lineno, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            expected, rel = raw.split(maxsplit=1)
        except ValueError:
            errors.append(f"invalid SHA256SUMS line {lineno}")
            continue
        rel = rel.lstrip("*")
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"hashed file missing: {rel}")
            continue
        observed = sha256(path)
        if observed != expected:
            errors.append(f"hash mismatch: {rel}")


def main() -> int:
    errors: list[str] = []
    audit_required(errors)
    audit_forbidden_dirs(errors)
    audit_text(errors)
    audit_data_hashes(errors)

    if errors:
        print("RELEASE AUDIT: FAIL")
        for item in errors:
            print(f" - {item}")
        return 1

    print("RELEASE AUDIT: PASS")
    print(" - required public files present")
    print(" - no forbidden cache/private-governance directories")
    print(" - no known machine-specific/private patterns")
    print(" - committed data hashes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
