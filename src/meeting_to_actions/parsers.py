"""Read VTT, SRT and plain-text transcripts into a list of speaker turns."""

from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path

from .model import Transcript, Utterance


class TranscriptError(ValueError):
    """The file can't be read as a transcript."""


_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_CUE_TIME = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_VOICE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>", re.IGNORECASE)
_TAG = re.compile(r"</?[^>]+>")
_SPEAKER = re.compile(r"^([A-Z][\w.'-]*(?: [A-Z0-9][\w.'-]*){0,3})\s*:\s+(.*)$")
_BRACKET_SPEAKER = re.compile(r"^\[([A-Z][\w.' -]{0,40})\]\s*(.*)$")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_TXT_LINE = re.compile(
    rf"^\s*(?:\[{_TIME}\]|\(?{_TIME}\)?)\s*[-–]?\s*(.*)$"  # timestamp first
)
_TXT_NAME_TIME = re.compile(rf"^([A-Z][\w.'-]*(?: [A-Z][\w.'-]*){{0,3}})\s*[\[(]{_TIME}[\])]\s*:?\s*(.*)$")
HEADER_KEYS = {"title", "meeting", "date", "attendees", "participants", "location", "chair", "subject"}
# Words that look like a speaker label but are really the start of a sentence.
_NOT_SPEAKERS = {"Note", "Action", "Decision", "Risk", "Question", "Agreed", "Todo", "Update", "Re"}


def _seconds(h: str | None, m: str, s: str, ms: str | None) -> float:
    total = int(h or 0) * 3600 + int(m) * 60 + int(s)
    if ms:
        total += int(ms.ljust(3, "0")) / 1000
    return float(total)


def _clean(text: str) -> str:
    text = html.unescape(_TAG.sub("", text))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _split_speaker(text: str) -> tuple[str, str]:
    m = _SPEAKER.match(text)
    if m and m.group(1) not in _NOT_SPEAKERS:
        return m.group(1).strip(), m.group(2).strip()
    m = _BRACKET_SPEAKER.match(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", text


def _parse_date(text: str) -> dt.date | None:
    m = _ISO_DATE.search(text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _merge(turns: list[tuple[str, str, float | None]]) -> list[Utterance]:
    """Join consecutive cues from the same speaker into one turn."""
    out: list[Utterance] = []
    for speaker, text, start in turns:
        if not text:
            continue
        if out and speaker and out[-1].speaker == speaker:
            out[-1].marks.append((len(out[-1].text) + 1, start))
            out[-1].text = f"{out[-1].text} {text}"
            continue
        out.append(Utterance(index=len(out) + 1, speaker=speaker, text=text, start=start))
    return out


def _parse_cues(text: str, fmt: str) -> tuple[list[Utterance], dict[str, str]]:
    meta: dict[str, str] = {}
    turns: list[tuple[str, str, float | None]] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        if lines[0].startswith("NOTE"):
            note = " ".join([lines[0][4:]] + lines[1:]).strip()
            for part in re.split(r"\s*;\s*|\n", note):
                if ":" in part:
                    key, _, value = part.partition(":")
                    meta.setdefault(key.strip().lower(), value.strip())
            continue
        if lines[0].startswith(("STYLE", "REGION")):
            continue
        timing = next((i for i, ln in enumerate(lines) if _CUE_TIME.match(ln)), None)
        if timing is None:
            continue
        m = _CUE_TIME.match(lines[timing])
        start = _seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        raw = " ".join(lines[timing + 1 :])
        speaker = ""
        voice = _VOICE.search(raw)
        if voice:
            speaker = voice.group(1).strip()
        body = _clean(raw)
        if not speaker:
            speaker, body = _split_speaker(body)
        turns.append((speaker, body, start))
    if not turns:
        raise TranscriptError(f"no {fmt.upper()} cues found")
    return _merge(turns), meta


def _parse_txt(text: str) -> tuple[list[Utterance], dict[str, str]]:
    meta: dict[str, str] = {}
    turns: list[tuple[str, str, float | None]] = []
    in_header = True
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = _clean(raw)
        if not line:
            continue
        if in_header:
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() in HEADER_KEYS:
                meta[key.strip().lower()] = value.strip()
                continue
            in_header = False
        start: float | None = None
        m = _TXT_NAME_TIME.match(line)
        if m:
            speaker, body = m.group(1), m.group(6)
            start = _seconds(m.group(2), m.group(3), m.group(4), m.group(5))
        else:
            m = _TXT_LINE.match(line)
            if m and (m.group(3) or m.group(7)):
                if m.group(3):
                    start = _seconds(m.group(1), m.group(2), m.group(3), m.group(4))
                else:
                    start = _seconds(m.group(5), m.group(6), m.group(7), m.group(8))
                line = m.group(9)
            speaker, body = _split_speaker(line)
        if not speaker and turns:
            # A line with no speaker continues the previous turn.
            prev = turns[-1]
            turns[-1] = (prev[0], f"{prev[1]} {body}", prev[2])
            continue
        turns.append((speaker, body, start))
    if not turns:
        raise TranscriptError("no lines found")
    return _merge(turns), meta


def detect_format(path: Path, text: str) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"vtt", "srt", "txt"}:
        return suffix
    if text.lstrip("﻿").startswith("WEBVTT"):
        return "vtt"
    if _CUE_TIME.search(text):
        return "srt"
    return "txt"


def parse_text(text: str, fmt: str, title: str = "", source: str = "") -> Transcript:
    text = text.lstrip("﻿")
    if fmt in {"vtt", "srt"}:
        utterances, meta = _parse_cues(text, fmt)
    elif fmt == "txt":
        utterances, meta = _parse_txt(text)
    else:
        raise TranscriptError(f"unknown format {fmt!r}: use vtt, srt or txt")
    date = _parse_date(meta.get("date", ""))
    name = meta.get("title") or meta.get("meeting") or meta.get("subject") or title
    people = meta.get("attendees") or meta.get("participants") or ""
    attendees = [a.strip() for a in re.split(r"[,;]| and ", people) if a.strip()]
    return Transcript(title=name, utterances=utterances, date=date, source=source, fmt=fmt, attendees=attendees)


def load(path: str | Path, fmt: str | None = None) -> Transcript:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TranscriptError(f"{p}: file not found") from exc
    except UnicodeDecodeError as exc:
        raise TranscriptError(f"{p}: not a UTF-8 text file") from exc
    fmt = fmt or detect_format(p, text)
    title = p.stem.replace("-", " ").replace("_", " ").strip().capitalize()
    try:
        return parse_text(text, fmt, title=title, source=str(p))
    except TranscriptError as exc:
        raise TranscriptError(f"{p}: {exc}") from exc
