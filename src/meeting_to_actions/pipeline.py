"""Load, redact and extract in one call."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import extract as rules
from . import llm, parsers
from .model import Result, Transcript
from .redact import redact


def run(
    transcript: Transcript,
    *,
    do_redact: bool = True,
    extractor: str = "rules",
    meeting_date: dt.date | None = None,
    attendees: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    if meeting_date:
        transcript.date = meeting_date
    for name in attendees or []:
        if name not in transcript.attendees:
            transcript.attendees.append(name)
    redactions = []
    if do_redact:
        transcript, redactions = redact(transcript)
    notes: list[str] = []
    if extractor == "rules":
        items = rules.extract(transcript)
    elif extractor == "llm":
        items, notes = llm.extract(transcript, env)
    else:
        raise ValueError(f"unknown extractor {extractor!r}: use rules or llm")
    if transcript.date is None:
        notes.append("No meeting date found: relative due dates such as 'Friday' are shown as said, not as dates.")
    return Result(
        transcript=transcript, items=items, redactions=redactions, extractor=extractor, redacted=do_redact, notes=notes
    )


def run_file(path: str | Path, **kwargs) -> Result:
    fmt = kwargs.pop("fmt", None)
    return run(parsers.load(path, fmt), **kwargs)
