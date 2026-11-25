"""Write results as Markdown minutes, a single-file HTML page, CSV, a Jira import CSV or JSON."""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter

from . import __version__
from .model import Item, Result

TITLES = {"decision": "Decisions", "action": "Actions", "question": "Open questions", "risk": "Risks"}


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def needs_review(result: Result) -> list[Item]:
    return [i for i in result.items if i.flags or i.confidence == "low"]


def redaction_counts(result: Result) -> dict[str, int]:
    """Distinct values replaced, per kind."""
    distinct = {(r.kind, r.placeholder) for r in result.redactions}
    return dict(sorted(Counter(kind for kind, _ in distinct).items()))


def summary(result: Result) -> dict:
    t = result.transcript
    return {
        "title": t.title,
        "date": t.date.isoformat() if t.date else None,
        "source": t.source,
        "format": t.fmt,
        "turns": len(t.utterances),
        "people": t.people,
        "extractor": result.extractor,
        "counts": {k: len(result.of_type(k)) for k in TITLES},
        "needs_review": len(needs_review(result)),
        "redacted": redaction_counts(result),
    }


def _when(item: Item) -> str:
    ev = item.evidence
    return ev.timestamp or f"turn {ev.utterance}"


def _due(item: Item) -> str:
    if item.due:
        return f"{item.due.isoformat()} ({item.due.strftime('%a')})"
    return f'"{item.due_text}"' if item.due_text else ""


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _redaction_line(result: Result) -> str:
    if not result.redacted:
        return "Redaction was switched off: quotes are shown as spoken."
    counts = redaction_counts(result)
    if not counts:
        return "Personal data redacted before extraction: none detected."
    parts = ", ".join(f"{n} {k.lower()}" for k, n in counts.items())
    return f"Personal data redacted before extraction: {parts}."


def to_markdown(result: Result) -> str:
    t = result.transcript
    s = summary(result)
    out = [f"# {t.title or 'Meeting'}", ""]
    meta = []
    if t.date:
        meta.append(f"**Date:** {t.date.isoformat()} ({t.date.strftime('%A')})")
    meta.append(f"**People:** {', '.join(t.people) or 'not stated'}")
    meta.append(f"**Source:** `{t.source or 'stdin'}` ({t.fmt}, {len(t.utterances)} turns)")
    out.append("  \n".join(meta))
    out.append("")
    c = s["counts"]
    out.append(
        f"{_n(c['decision'], 'decision')}, {_n(c['action'], 'action')}, {_n(c['question'], 'open question')}, "
        f"{_n(c['risk'], 'risk')}. {_n(s['needs_review'], 'item')} to look at again."
    )
    out.append("")
    out.append(_redaction_line(result))
    out.append("")
    out.append(
        f"> Draft minutes extracted by meeting-to-actions {__version__} ({result.extractor} extractor). "
        "Check each item against its quote before sending."
    )
    out.append("")

    def section(kind: str, header: list[str], row) -> None:
        items = result.of_type(kind)
        out.extend([f"## {TITLES[kind]}", ""])
        if not items:
            out.extend(["None found.", ""])
            return
        out.append("| " + " | ".join(header) + " |")
        out.append("|" + "---|" * len(header))
        for item in items:
            out.append("| " + " | ".join(_md(str(v)) for v in row(item)) + " |")
        out.append("")

    section("decision", ["ID", "Decision", "Said by", "At"], lambda i: [i.id, i.text, i.evidence.speaker, _when(i)])
    section(
        "action",
        ["ID", "Action", "Owner", "Due", "Confidence", "Check"],
        lambda i: [i.id, i.text, i.owner or "**unassigned**", _due(i), i.confidence, ", ".join(i.flags)],
    )
    section("question", ["ID", "Question", "Raised by", "At"], lambda i: [i.id, i.text, i.evidence.speaker, _when(i)])
    section("risk", ["ID", "Risk", "Raised by", "At"], lambda i: [i.id, i.text, i.evidence.speaker, _when(i)])

    out.extend(["## Evidence", ""])
    for item in result.items:
        ev = item.evidence
        again = f" Also mentioned at {', '.join(item.also_at)}." if item.also_at else ""
        confirmed = f" Taken on by {item.confirmed_by}." if item.confirmed_by else ""
        out.append(
            f'- **{item.id}** ({_when(item)}, {ev.speaker or "unknown speaker"}): "{ev.quote}"{confirmed}{again}'
        )
    out.append("")
    for note in result.notes:
        out.append(f"_Note: {note}_  ")
    return "\n".join(out).rstrip() + "\n"


CSV_FIELDS = ["id", "type", "text", "owner", "due", "due_text", "confidence", "flags", "timestamp", "speaker", "quote"]


def to_csv(result: Result) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_FIELDS)
    for i in result.items:
        ev = i.evidence
        w.writerow(
            [
                i.id,
                i.type,
                i.text,
                i.owner,
                i.due.isoformat() if i.due else "",
                i.due_text,
                i.confidence,
                "; ".join(i.flags),
                ev.timestamp,
                ev.speaker,
                ev.quote,
            ]
        )
    return buf.getvalue()


JIRA_FIELDS = ["Summary", "Issue Type", "Assignee", "Due Date", "Labels", "Description"]


def to_jira_csv(result: Result, issue_type: str = "Task", label: str = "meeting-action") -> str:
    """Actions only, one row each, ready for Jira's CSV import wizard."""
    t = result.transcript
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(JIRA_FIELDS)
    for i in result.of_type("action"):
        ev = i.evidence
        lines = [
            f"From: {t.title}" + (f" ({t.date.isoformat()})" if t.date else ""),
            f'Said by {ev.speaker or "unknown"} at {_when(i)}: "{ev.quote}"',
        ]
        if i.confirmed_by:
            lines.append(f"Taken on by {i.confirmed_by}")
        if i.flags:
            lines.append("Check: " + ", ".join(i.flags))
        w.writerow(
            [
                i.text[:250],
                issue_type,
                i.owner,
                i.due.isoformat() if i.due else "",
                label,
                "\n".join(lines),
            ]
        )
    return buf.getvalue()


def to_json(result: Result) -> str:
    data = {
        "tool": f"meeting-to-actions {__version__}",
        "summary": summary(result),
        "items": [i.to_dict() for i in result.items],
        "redactions": [{"kind": r.kind, "placeholder": r.placeholder, "turn": r.utterance} for r in result.redactions],
        "notes": result.notes,
    }
    return json.dumps(data, indent=2) + "\n"


_CSS = """
:root{--bg:#faf8f4;--fg:#22201c;--muted:#6b665d;--line:#e4dfd5;--card:#fff;--accent:#b4462b;--warn:#8a5a00;
--warnbg:#fff3d6;--ok:#2f6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a18;--fg:#ece8e1;--muted:#a59f94;--line:#3a3732;--card:#24221f;
--accent:#e0805f;--warn:#f0c060;--warnbg:#3a3020;--ok:#7fc08a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 48px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.meta{color:var(--muted);margin:0 0 16px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
gap:10px;margin:16px 0}.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile b{display:block;font-size:26px}.tile span{color:var(--muted);font-size:13px}
.note{background:var(--card);border-left:3px solid var(--accent);padding:8px 12px;margin:12px 0;color:var(--muted)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line)}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.id{font-weight:600;white-space:nowrap}.q{display:block;color:var(--muted);font-size:13px;margin-top:4px}
.flag{display:inline-block;background:var(--warnbg);color:var(--warn);border-radius:4px;padding:0 6px;
font-size:12px;margin:2px 4px 0 0}.conf-high{color:var(--ok)}.conf-low{color:var(--warn)}
.ph{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}footer{color:var(--muted);font-size:13px;margin-top:32px}
"""


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def to_html(result: Result) -> str:
    t = result.transcript
    s = summary(result)
    c = s["counts"]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_e(t.title or 'Meeting')} minutes</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{_e(t.title or 'Meeting')}</h1>",
    ]
    meta = []
    if t.date:
        meta.append(f"{t.date.isoformat()} ({t.date.strftime('%A')})")
    meta.append(f"{len(t.utterances)} turns from {_e(t.source or 'stdin')}")
    meta.append(_e(", ".join(t.people)))
    parts.append(f"<p class='meta'>{' · '.join(meta)}</p>")
    parts.append("<div class='tiles'>")
    for label, n in [
        ("decisions", c["decision"]),
        ("actions", c["action"]),
        ("open questions", c["question"]),
        ("risks", c["risk"]),
        ("need a closer look", s["needs_review"]),
    ]:
        parts.append(f"<div class='tile'><b>{n}</b><span>{label}</span></div>")
    parts.append("</div>")
    red = _redaction_line(result)
    if red:
        parts.append(f"<p class='note'>{_e(red)}</p>")
    parts.append(
        f"<p class='note'>Draft minutes from the {_e(result.extractor)} extractor. "
        "Each item shows the words it came from: check them before you send these on.</p>"
    )

    def row(item: Item, cells: list[str]) -> str:
        ev = item.evidence
        quote = f"<span class='q'>{_e(ev.speaker or 'unknown')} at {_e(_when(item))}: “{_e(ev.quote)}”</span>"
        if item.confirmed_by:
            quote += f"<span class='q'>Taken on by {_e(item.confirmed_by)}</span>"
        return f"<tr><td class='id'>{_e(item.id)}</td><td>{_e(item.text)}{quote}</td>" + "".join(cells) + "</tr>"

    for kind in TITLES:
        items = result.of_type(kind)
        parts.append(f"<h2>{TITLES[kind]}</h2>")
        if not items:
            parts.append("<p class='meta'>None found.</p>")
            continue
        if kind == "action":
            head = "<tr><th>ID</th><th>Action</th><th>Owner</th><th>Due</th><th>Confidence</th></tr>"
        else:
            head = f"<tr><th>ID</th><th>{TITLES[kind][:-1]}</th></tr>"
        parts.append(f"<div class='wrap'><table>{head}")
        for item in items:
            if kind == "action":
                flags = "".join(f"<span class='flag'>{_e(f)}</span>" for f in item.flags)
                owner = _e(item.owner) if item.owner else "<span class='flag'>unassigned</span>"
                cells = [
                    f"<td>{owner}</td>",
                    f"<td>{_e(_due(item))}</td>",
                    f"<td><span class='conf-{_e(item.confidence)}'>{_e(item.confidence)}</span><br>{flags}</td>",
                ]
            else:
                cells = []
            parts.append(row(item, cells))
        parts.append("</table></div>")
    for note in result.notes:
        parts.append(f"<p class='note'>{_e(note)}</p>")
    parts.append(
        f"<footer>meeting-to-actions {__version__}. This page has no scripts and makes no external requests.</footer>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts) + "\n"
