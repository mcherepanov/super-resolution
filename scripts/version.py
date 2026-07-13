"""Версия приложения из version.properties (корень репозитория или /app)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _version_path() -> Path:
    override = os.environ.get("VERSION_FILE", "").strip()
    if override:
        return Path(override)
    for candidate in (_REPO_ROOT / "version.properties", Path("/app/version.properties")):
        if candidate.is_file():
            return candidate
    return _REPO_ROOT / "version.properties"


@lru_cache
def get_version_info() -> dict[str, str | int]:
    path = _version_path()
    props: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    version = props.get("version", "0.0")
    prerelease = props.get("prerelease", "").strip()
    build_raw = props.get("build", "0")
    try:
        build = int(build_raw)
    except ValueError:
        build = 0
    label = f"{version}-{prerelease}" if prerelease else version
    return {
        "version": version,
        "prerelease": prerelease,
        "build": build,
        "label": label,
        "display": f"{label} · {build}" if build else label,
    }
