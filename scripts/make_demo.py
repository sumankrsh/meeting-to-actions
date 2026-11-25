"""Record real CLI output into docs/demo.svg.

Run from the project root:  python scripts/make_demo.py

It extracts the bundled claims steering-group transcript, then scores the
extractor on the held-out transcript, and saves what the terminal showed.
"""

from __future__ import annotations

import io
import os
import shlex
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from meeting_to_actions import cli

ROOT = Path(__file__).resolve().parents[1]


def run(console: Console, command: str) -> None:
    console.print(f"[bold green]$[/bold green] [bold]{command}[/bold]")
    result = CliRunner().invoke(cli.app, shlex.split(command)[1:])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    console.print()


def main() -> None:
    console = Console(record=True, width=112, file=io.StringIO(), force_terminal=True, highlight=False)
    cli.console = console
    os.chdir(ROOT)
    run(console, "meeting-to-actions extract examples/claims-steering.vtt")
    run(
        console,
        "meeting-to-actions eval examples/holdout/store-ops-review.vtt examples/holdout/store-ops-review.labels.json",
    )
    (ROOT / "docs").mkdir(exist_ok=True)
    console.save_svg(str(ROOT / "docs" / "demo.svg"), title="meeting-to-actions")
    print("wrote docs/demo.svg")


if __name__ == "__main__":
    main()
