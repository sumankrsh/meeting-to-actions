"""Replace personal data in transcript text with numbered placeholders.

Redaction runs before any extraction, so quotes in the reports (and anything
sent to an optional LLM) only ever contain the placeholders. The same value
gets the same placeholder throughout one transcript.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .model import Redaction, Transcript, Utterance


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    v = value.replace(" ", "").upper()
    if not 15 <= len(v) <= 34:
        return False
    moved = v[4:] + v[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return int(number) % 97 == 1


def _card(m: re.Match) -> bool:
    digits = re.sub(r"\D", "", m.group(0))
    return 13 <= len(digits) <= 19 and luhn_ok(digits)


def _phone(m: re.Match) -> bool:
    digits = re.sub(r"\D", "", m.group(0))
    return 10 <= len(digits) <= 15


def _iban(m: re.Match) -> bool:
    return iban_ok(m.group(0))


def _ip(m: re.Match) -> bool:
    return all(0 <= int(p) <= 255 for p in m.group(0).split("."))


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern
    check: Callable[[re.Match], bool] | None = None


# Order matters: more specific detectors run first so a card number isn't
# taken for a phone number.
DETECTORS: list[Detector] = [
    Detector("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    Detector("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban),
    Detector("CARD", re.compile(r"(?<![\d-])\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}(?![\d-])"), _card),
    Detector("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    Detector("NINO", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b")),
    Detector("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), _ip),
    Detector("PHONE", re.compile(r"(?<![\w-])\+?\(?\d[\d ().-]{8,}\d(?![\w-])"), _phone),
    Detector("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.? [A-Z][a-z]+(?:[ -][A-Z][a-z]+)?\b")),
]

KINDS = [d.kind for d in DETECTORS]


def redact_text(text: str, table: dict[tuple[str, str], str], counts: dict[str, int]) -> tuple[str, list[str]]:
    """Redact one string. `table` maps (kind, value) to its placeholder across calls."""
    found: list[str] = []
    for det in DETECTORS:

        def repl(m: re.Match, det: Detector = det) -> str:
            if det.check and not det.check(m):
                return m.group(0)
            key = (det.kind, re.sub(r"\s+", "", m.group(0)).lower())
            if key not in table:
                counts[det.kind] = counts.get(det.kind, 0) + 1
                table[key] = f"[{det.kind}-{counts[det.kind]}]"
            found.append(table[key])
            return table[key]

        text = det.pattern.sub(repl, text)
    return text, found


def redact(transcript: Transcript) -> tuple[Transcript, list[Redaction]]:
    """Return a redacted copy of the transcript and a list of what was replaced.

    Speaker labels are kept: they are needed to assign owners.
    """
    table: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    log: list[Redaction] = []
    out: list[Utterance] = []
    for u in transcript.utterances:
        # Redact cue by cue so the cue offsets (marks) stay right.
        cuts = [0] + [pos for pos, _ in u.marks]
        segments = [u.text[a : b - 1] for a, b in zip(cuts, cuts[1:], strict=False)] + [u.text[cuts[-1] :]]
        pieces: list[str] = []
        marks: list[tuple[int, float | None]] = []
        for k, segment in enumerate(segments):
            text, found = redact_text(segment, table, counts)
            for placeholder in found:
                kind = placeholder[1:].split("-")[0]
                log.append(Redaction(kind=kind, placeholder=placeholder, utterance=u.index))
            if k > 0:
                marks.append((len(" ".join(pieces)) + 1, u.marks[k - 1][1]))
            pieces.append(text)
        out.append(Utterance(index=u.index, speaker=u.speaker, text=" ".join(pieces), start=u.start, marks=marks))
    copy = Transcript(
        title=transcript.title,
        utterances=out,
        date=transcript.date,
        source=transcript.source,
        fmt=transcript.fmt,
        attendees=list(transcript.attendees),
    )
    return copy, log
