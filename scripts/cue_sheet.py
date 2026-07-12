"""Парсинг и валидация CUE sheet."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_CUE_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "latin-1")
_QUOTED = re.compile(r'^(\w+)\s+"((?:[^"\\]|\\.)*)"\s*$')
_FILE_RE = re.compile(
    r'^FILE\s+"((?:[^"\\]|\\.)*)"\s+(\w+)',
    re.IGNORECASE,
)
_TRACK_RE = re.compile(r'^TRACK\s+(\d+)\s+AUDIO', re.IGNORECASE)
_INDEX_RE = re.compile(
    r'^INDEX\s+(\d+)\s+(\d+):(\d{2}):(\d{2})$',
    re.IGNORECASE,
)


@dataclass
class CueTrack:
    number: int
    title: str = ""
    performer: str = ""
    index01_sec: float = 0.0
    end_sec: float | None = None


@dataclass
class CueFileEntry:
    cue_name: str
    resolved: Path | None
    audio_format: str = ""
    tracks: list[CueTrack] = field(default_factory=list)


@dataclass
class CueSheet:
    cue_path: Path
    files: list[CueFileEntry] = field(default_factory=list)

    @property
    def track_count(self) -> int:
        return sum(len(f.tracks) for f in self.files)

    @property
    def is_multi_file(self) -> bool:
        return len(self.files) > 1


def _read_cue_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in _CUE_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def index_to_seconds(minutes: int, seconds: int, frames: int) -> float:
    return minutes * 60 + seconds + frames / 75.0


def _resolve_audio(cue_dir: Path, name: str, input_dir: Path) -> Path | None:
    basename = Path(name.replace("\\", "/")).name
    candidates = [
        cue_dir / basename,
        input_dir / basename,
    ]
    seen: set[Path] = set()
    for cand in candidates:
        resolved = cand.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and input_dir.resolve() in resolved.parents:
            return resolved
    return None


def parse_cue(path: Path, *, input_dir: Path | None = None) -> CueSheet:
    path = path.resolve()
    base_dir = input_dir or path.parent
    text = _read_cue_text(path)
    sheet = CueSheet(cue_path=path)

    current_file: CueFileEntry | None = None
    current_track: CueTrack | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("REM"):
            continue

        m_file = _FILE_RE.match(line)
        if m_file:
            if current_file is not None:
                sheet.files.append(current_file)
            name = m_file.group(1).replace(r"\\", "\\").replace(r"\"", '"')
            current_file = CueFileEntry(
                cue_name=name,
                resolved=_resolve_audio(path.parent, name, base_dir),
                audio_format=m_file.group(2).upper(),
            )
            current_track = None
            continue

        if current_file is None:
            continue

        m_track = _TRACK_RE.match(line)
        if m_track:
            current_track = CueTrack(number=int(m_track.group(1)))
            current_file.tracks.append(current_track)
            continue

        if current_track is None:
            continue

        m_idx = _INDEX_RE.match(line)
        if m_idx:
            idx_num = int(m_idx.group(1))
            if idx_num == 1:
                current_track.index01_sec = index_to_seconds(
                    int(m_idx.group(2)),
                    int(m_idx.group(3)),
                    int(m_idx.group(4)),
                )
            continue

        m_q = _QUOTED.match(line)
        if m_q:
            key, val = m_q.group(1).upper(), m_q.group(2)
            if key == "TITLE":
                current_track.title = val
            elif key == "PERFORMER":
                current_track.performer = val

    if current_file is not None:
        sheet.files.append(current_file)

    _fill_track_ends(sheet)
    return sheet


def _fill_track_ends(sheet: CueSheet) -> None:
    for entry in sheet.files:
        for i, track in enumerate(entry.tracks):
            if i + 1 < len(entry.tracks):
                track.end_sec = entry.tracks[i + 1].index01_sec
            else:
                track.end_sec = None


def validate_cue(
    path: Path,
    input_dir: Path,
) -> tuple[bool, list[str], CueSheet | None]:
    """Вернуть (ok, missing_basenames, sheet)."""
    if not path.is_file():
        return False, [path.name], None
    try:
        sheet = parse_cue(path, input_dir=input_dir)
    except OSError as exc:
        return False, [str(exc)], None

    if not sheet.files:
        return False, ["нет директив FILE"], None

    missing: list[str] = []
    for entry in sheet.files:
        if entry.resolved is None:
            missing.append(Path(entry.cue_name).name)

    return len(missing) == 0, missing, sheet


def cue_info_dict(sheet: CueSheet) -> dict:
    return {
        "cue": sheet.cue_path.name,
        "files": [Path(f.cue_name).name for f in sheet.files],
        "resolved": [f.resolved.name if f.resolved else "" for f in sheet.files],
        "tracks": sheet.track_count,
        "multi_file": sheet.is_multi_file,
    }


def split_output_dir(audio_path: Path) -> Path:
    return audio_path.parent / audio_path.stem


def safe_track_name(number: int, title: str, performer: str = "") -> str:
    label = title or f"Track {number:02d}"
    if performer:
        label = f"{performer} - {label}"
    safe = re.sub(r'[\\/:*?"<>|]', "_", label).strip()
    safe = re.sub(r"\s+", " ", safe)
    return f"{number:02d} - {safe[:100]}" if safe else f"{number:02d}"
