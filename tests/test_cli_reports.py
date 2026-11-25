import csv
import io
import json

from typer.testing import CliRunner

from meeting_to_actions import cli, evaluate, reports
from meeting_to_actions.pipeline import run_file

runner = CliRunner()


def test_extract_writes_every_report(examples, tmp_path):
    out = {ext: tmp_path / f"minutes.{ext}" for ext in ("md", "html", "csv", "json")}
    jira = tmp_path / "jira.csv"
    res = runner.invoke(
        cli.app,
        [
            "extract",
            str(examples / "claims-steering.vtt"),
            "--markdown",
            str(out["md"]),
            "--html",
            str(out["html"]),
            "--csv",
            str(out["csv"]),
            "--json",
            str(out["json"]),
            "--jira",
            str(jira),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Found 2 decisions, 8 actions, 2 open questions and 3 risks." in res.output
    md = out["md"].read_text()
    assert "## Actions" in md and "| A1 | Update the pilot scope document" in md
    assert "Personal data redacted before extraction: 1 card, 1 email, 1 name, 1 phone." in md
    page = out["html"].read_text()
    assert "<script" not in page and "http" not in page.split("</style>")[1].split("<footer>")[0]
    data = json.loads(out["json"].read_text())
    assert data["summary"]["counts"]["action"] == 8
    assert data["summary"]["redacted"] == {"CARD": 1, "EMAIL": 1, "NAME": 1, "PHONE": 1}
    rows = list(csv.DictReader(io.StringIO(jira.read_text())))
    assert len(rows) == 8 and rows[0]["Assignee"] == "Grace Liu" and rows[0]["Due Date"] == "2026-11-27"
    assert "elena" not in jira.read_text().lower()


def test_no_redact_keeps_quotes_and_says_so(examples, tmp_path):
    md = tmp_path / "m.md"
    res = runner.invoke(
        cli.app, ["extract", str(examples / "claims-steering.vtt"), "--no-redact", "--markdown", str(md)]
    )
    assert res.exit_code == 0
    assert "Redaction was switched off" in md.read_text()


def test_require_owner_gate(tmp_path):
    t = tmp_path / "m.txt"
    t.write_text("Ana: We need to rotate the keys.\nBo: Yes, it's overdue.\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["extract", str(t), "--require-owner", "-q"])
    assert res.exit_code == 1 and "without an owner: A1" in res.output


def test_bad_input_exits_2(tmp_path):
    assert runner.invoke(cli.app, ["extract", str(tmp_path / "nope.vtt")]).exit_code == 2
    assert runner.invoke(cli.app, ["extract", "x.vtt", "--date", "24/11/2026"]).exit_code == 2


def test_redact_command_shows_placeholders(examples):
    res = runner.invoke(cli.app, ["redact", str(examples / "claims-steering.vtt")])
    assert res.exit_code == 0
    assert "[PHONE-1]" in res.output and "07700" not in res.output
    assert "Redacted 4 distinct values" in res.output


def test_eval_scores_against_labels(examples, tmp_path):
    out = tmp_path / "score.json"
    res = runner.invoke(
        cli.app,
        [
            "eval",
            str(examples / "vendor-review.txt"),
            str(examples / "labels" / "vendor-review.json"),
            "--json",
            str(out),
        ],
    )
    assert res.exit_code == 0
    assert json.loads(out.read_text())["overall"] == {"expected": 8, "found": 8, "matched": 8}


def test_score_counts_misses_and_extras(examples):
    result = run_file(examples / "vendor-review.txt")
    gold = [
        {"type": "decision", "text": "Go with Vendor A for the pilot"},
        {"type": "risk", "text": "Weather could close the office"},
    ]
    sc = evaluate.score(result.items, gold)
    assert sc.types["decision"].matched == 1
    assert sc.types["risk"].recall == 0 and len(sc.missed) == 1
    assert len(sc.extra) == len(result.items) - 1


def test_rules_command_lists_rules():
    res = runner.invoke(cli.app, ["rules"])
    assert res.exit_code == 0 and "self-commitment" in res.output and "IBAN" in res.output


def test_markdown_escapes_pipes(examples):
    result = run_file(examples / "vendor-review.txt")
    result.items[0].text = "a | b"
    assert "a \\| b" in reports.to_markdown(result)


def test_scores_quoted_in_the_readme(examples):
    """The README quotes these figures; keep them true."""
    cases = [
        ("claims-steering.vtt", "labels/claims-steering.json", None, (15, 15, 15)),
        ("vendor-review.txt", "labels/vendor-review.json", None, (8, 8, 8)),
        ("data-platform-standup.srt", "labels/data-platform-standup.json", "2026-11-25", (8, 8, 8)),
        ("holdout/store-ops-review.vtt", "holdout/store-ops-review.labels.json", None, (10, 8, 8)),
    ]
    import datetime as dt

    for transcript, labels, date, (expected, found, matched) in cases:
        result = run_file(examples / transcript, meeting_date=dt.date.fromisoformat(date) if date else None)
        sc = evaluate.score(result.items, evaluate.load_expected(examples / labels))
        assert (sc.expected, sc.found, sc.matched) == (expected, found, matched), transcript
    holdout = run_file(examples / "holdout/store-ops-review.vtt")
    sc = evaluate.score(holdout.items, evaluate.load_expected(examples / "holdout/store-ops-review.labels.json"))
    assert (sc.owner_right, sc.due_right, sc.actions_matched) == (4, 3, 4)
