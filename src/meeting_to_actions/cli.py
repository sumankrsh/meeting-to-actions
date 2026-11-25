"""Command-line interface: extract, redact, eval and rules."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, evaluate, parsers, reports
from .llm import LLMError
from .model import Result
from .pipeline import run
from .redact import KINDS, redact

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Decisions, actions with owners and dates, open questions and risks from a meeting transcript, offline.",
)
console = Console(highlight=False)

FORMATS = ("vtt", "srt", "txt")


def _fail(message: str) -> None:
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(2)


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        _fail(f"--date must be YYYY-MM-DD, got {value!r}")
    return None


def _load(path: Path, fmt: str | None):
    if fmt and fmt not in FORMATS:
        _fail(f"--format must be one of {', '.join(FORMATS)}")
    try:
        return parsers.load(path, fmt)
    except parsers.TranscriptError as exc:
        _fail(str(exc))


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _print_result(result: Result) -> None:
    t = result.transcript
    s = reports.summary(result)
    c = s["counts"]
    when = f"{t.date.isoformat()} ({t.date.strftime('%a')})" if t.date else "no date"
    console.print(
        f"[bold]{t.title or 'Meeting'}[/bold]  [dim]{when} · {len(t.utterances)} turns · {len(t.people)} people[/dim]"
    )
    console.print(reports._redaction_line(result), style="dim")
    console.print()

    narrow = console.width < 90
    for kind in reports.TITLES:
        items = result.of_type(kind)
        if not items:
            continue
        table = Table(
            title=f"{reports.TITLES[kind]} ({len(items)})", title_justify="left", show_lines=False, expand=False
        )
        table.add_column("ID", no_wrap=True, style="bold")
        table.add_column(reports.TITLES[kind][:-1] if kind != "question" else "Question", overflow="fold")
        if kind == "action":
            table.add_column("Owner", overflow="fold")
            table.add_column("Due", no_wrap=True)
            if not narrow:
                table.add_column("Check", overflow="fold", style="yellow")
        if not narrow:
            table.add_column("At", no_wrap=True, style="dim")
        for i in items:
            row = [i.id, i.text]
            if kind == "action":
                row.append(i.owner or "[yellow]unassigned[/yellow]")
                due = (
                    i.due.strftime("%a %d %b")
                    if i.due
                    else (f'"{i.due_text}"' if i.due_text else "[yellow]none[/yellow]")
                )
                row.append(due)
                if not narrow:
                    row.append(", ".join(i.flags))
            if not narrow:
                row.append(i.evidence.timestamp or str(i.evidence.utterance))
            table.add_row(*row)
        console.print(table)
    review = reports.needs_review(result)
    console.print(
        f"Found {_n(c['decision'], 'decision')}, {_n(c['action'], 'action')}, "
        f"{_n(c['question'], 'open question')} and {_n(c['risk'], 'risk')}."
    )
    if review:
        console.print(f"[yellow]{len(review)} need a closer look:[/yellow] {', '.join(i.id for i in review)}")
    for note in result.notes:
        console.print(f"[dim]Note: {note}[/dim]")


@app.command()
def extract(
    transcript: Path = typer.Argument(..., help="A .vtt, .srt or .txt transcript."),
    date: str | None = typer.Option(None, "--date", help="Meeting date (YYYY-MM-DD) if the transcript doesn't say."),
    attendee: list[str] = typer.Option([], "--attendee", help="Someone who can own actions but didn't speak."),
    fmt: str | None = typer.Option(None, "--format", help="Force the input format: vtt, srt or txt."),
    markdown: Path | None = typer.Option(None, "--markdown", help="Write Markdown minutes here."),
    html: Path | None = typer.Option(None, "--html", help="Write single-file HTML minutes here."),
    csv_path: Path | None = typer.Option(None, "--csv", help="Write every item as CSV here."),
    jira: Path | None = typer.Option(None, "--jira", help="Write actions as a Jira CSV import file here."),
    json_path: Path | None = typer.Option(None, "--json", help="Write everything as JSON here."),
    no_redact: bool = typer.Option(False, "--no-redact", help="Keep personal data in quotes (not recommended)."),
    extractor: str = typer.Option("rules", "--extractor", help="rules (offline, default) or llm (see README)."),
    require_owner: bool = typer.Option(False, "--require-owner", help="Exit 1 if any action has no owner."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print the files written."),
) -> None:
    """Extract decisions, actions, open questions and risks from a transcript."""
    if extractor not in ("rules", "llm"):
        _fail("--extractor must be rules or llm")
    t = _load(transcript, fmt)
    try:
        result = run(t, do_redact=not no_redact, extractor=extractor, meeting_date=_date(date), attendees=attendee)
    except LLMError as exc:
        _fail(str(exc))
    if not quiet:
        _print_result(result)
    writers = [
        (markdown, reports.to_markdown),
        (html, reports.to_html),
        (csv_path, reports.to_csv),
        (jira, reports.to_jira_csv),
        (json_path, reports.to_json),
    ]
    for path, writer in writers:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(writer(result), encoding="utf-8")
            console.print(f"[green]wrote[/green] {path}")
    unowned = [i.id for i in result.of_type("action") if not i.owner]
    if require_owner and unowned:
        console.print(f"[red]FAIL[/red] {len(unowned)} action(s) without an owner: {', '.join(unowned)}")
        raise typer.Exit(1)


@app.command("redact")
def redact_cmd(
    transcript: Path = typer.Argument(..., help="A .vtt, .srt or .txt transcript."),
    fmt: str | None = typer.Option(None, "--format", help="Force the input format: vtt, srt or txt."),
    out: Path | None = typer.Option(None, "--out", help="Write the redacted transcript here as text."),
) -> None:
    """Show the transcript as the extractor sees it, with personal data replaced."""
    t, log = redact(_load(transcript, fmt))
    lines = [f"[{u.timestamp or u.index}] {u.speaker + ': ' if u.speaker else ''}{u.text}" for u in t.utterances]
    if out:
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.print(f"[green]wrote[/green] {out}")
    else:
        for line in lines:
            console.print(line, markup=False)
    distinct = sorted({(r.kind, r.placeholder) for r in log})
    if distinct:
        console.print(f"Redacted {len(distinct)} distinct values: {', '.join(p for _, p in distinct)}")
    else:
        console.print("No personal data detected.")


@app.command("eval")
def eval_cmd(
    transcript: Path = typer.Argument(..., help="A .vtt, .srt or .txt transcript."),
    expected: Path = typer.Argument(..., help="Hand-labelled items as JSON."),
    date: str | None = typer.Option(None, "--date", help="Meeting date (YYYY-MM-DD) if the transcript doesn't say."),
    json_path: Path | None = typer.Option(None, "--json", help="Write the scores as JSON here."),
) -> None:
    """Score the extractor against hand-labelled items for one transcript."""
    t = _load(transcript, None)
    try:
        gold = evaluate.load_expected(expected)
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    result = run(t, meeting_date=_date(date))
    sc = evaluate.score(result.items, gold)
    table = Table(title=f"{t.title}: extractor vs labels", title_justify="left")
    for col in ("Type", "Labelled", "Found", "Matched", "Precision", "Recall"):
        table.add_column(col, justify="left" if col == "Type" else "right")
    for kind, ts in sc.types.items():
        table.add_row(kind, str(ts.expected), str(ts.found), str(ts.matched), f"{ts.precision:.0%}", f"{ts.recall:.0%}")
    console.print(table)
    console.print(
        f"Matched {sc.matched} of {sc.expected} labelled items; {sc.found - sc.matched} extra. "
        f"Owner right on {sc.owner_right}/{sc.actions_matched} matched actions, "
        f"due date right on {sc.due_right}/{sc.actions_matched}."
    )
    for g in sc.missed:
        console.print(f"[yellow]missed[/yellow] {g['type']}: {g['text']}")
    for i in sc.extra:
        console.print(f"[yellow]extra[/yellow]  {i.id} {i.type}: {i.text}")
    if json_path:
        json_path.write_text(json.dumps(sc.to_dict(), indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]wrote[/green] {json_path}")


RULES = [
    ("decision-phrase", "decision", '"we\'ve agreed", "decision:", "let\'s go with", "we\'re going with" ...'),
    ("request", "action", '"Name, can you ..." or "can you ..." answered by the next speaker; a refusal drops it'),
    ("self-commitment", "action", '"I\'ll ...", "I will ...", "let me ..." by the speaker, minus meeting talk'),
    ("named-owner", "action", '"Tom will ...", "Tom to ..." for someone in the meeting'),
    ("action-marker", "action", '"Action: ...", "Next step: ...", optionally "Action for Tom: ..."'),
    ("group-need", "action", '"we need to ...", "someone should ...": unowned until someone says "I\'ll take that"'),
    ("open-marker", "question", '"open question", "still need to decide", "TBD", "park that" ...'),
    ("unanswered-question", "question", 'a question whose reply is "not sure", "good question", "no idea" ...'),
    ("risk-phrase", "risk", '"risk", "concern", "worried", "blocker", "might slip" ..., unless negated'),
    ("retraction", "-", '"scratch that", "never mind" withdraws the speaker\'s last action or decision'),
]


@app.command("rules")
def rules_cmd() -> None:
    """List the extraction rules and the personal-data detectors."""
    table = Table(title="Extraction rules", title_justify="left")
    table.add_column("Rule", style="bold", no_wrap=True)
    table.add_column("Makes")
    table.add_column("Fires on", overflow="fold")
    for row in RULES:
        table.add_row(*row)
    console.print(table)
    console.print(f"Redaction detectors: {', '.join(KINDS)}")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"meeting-to-actions {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
