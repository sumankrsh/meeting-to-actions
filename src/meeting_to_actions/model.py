"""Data types shared by the parsers, the extractors and the reports."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

ITEM_TYPES = ("decision", "action", "question", "risk")


@dataclass
class Utterance:
    """One speaker turn: a VTT/SRT cue or one line of a text transcript."""

    index: int  # 1-based position in the transcript
    speaker: str  # "" when the transcript doesn't say who spoke
    text: str
    start: float | None = None  # seconds from the start of the meeting
    # (character offset, seconds) for each cue merged into this turn after the first
    marks: list[tuple[int, float | None]] = field(default_factory=list)

    @property
    def timestamp(self) -> str:
        return format_seconds(self.start)

    def time_at(self, offset: int) -> str:
        """Timestamp of the cue that contains the character at `offset`."""
        start = self.start
        for pos, seconds in self.marks:
            if pos <= offset:
                start = seconds
        return format_seconds(start)


@dataclass
class Transcript:
    title: str
    utterances: list[Utterance]
    date: dt.date | None = None
    source: str = ""
    fmt: str = ""  # vtt, srt or txt
    attendees: list[str] = field(default_factory=list)  # from a header, plus --attendee

    @property
    def people(self) -> list[str]:
        """Everyone who can own an action: speakers first, then listed attendees."""
        out = list(self.speakers)
        for name in self.attendees:
            if name and name not in out:
                out.append(name)
        return out

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for u in self.utterances:
            if u.speaker and u.speaker not in seen:
                seen.append(u.speaker)
        return seen


@dataclass
class Evidence:
    """Where in the transcript an item came from."""

    utterance: int
    speaker: str
    timestamp: str
    quote: str


@dataclass
class Item:
    type: str  # one of ITEM_TYPES
    text: str
    evidence: Evidence
    owner: str = ""
    due: dt.date | None = None
    due_text: str = ""
    confidence: str = "medium"  # high, medium or low
    flags: list[str] = field(default_factory=list)
    rule: str = ""
    also_at: list[str] = field(default_factory=list)  # timestamps of repeats merged into this item
    confirmed_by: str = ""  # the reply that accepted a request or took an unowned action
    id: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["due"] = self.due.isoformat() if self.due else None
        return d


@dataclass
class Redaction:
    kind: str
    placeholder: str
    utterance: int


@dataclass
class Result:
    transcript: Transcript
    items: list[Item]
    redactions: list[Redaction]
    extractor: str = "rules"
    redacted: bool = True
    notes: list[str] = field(default_factory=list)

    def of_type(self, kind: str) -> list[Item]:
        return [i for i in self.items if i.type == kind]


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
