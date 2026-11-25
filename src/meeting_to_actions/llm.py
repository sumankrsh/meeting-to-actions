"""Optional extractor that asks an OpenAI-compatible chat API for the items.

Only the redacted transcript is sent. The model's answer is not trusted: every
item must point at a transcript turn and quote it word for word, or it is
dropped. Owners must be people in the meeting, and due dates are re-checked
with the same date rules as the offline extractor.

Configure with environment variables:

    M2A_LLM_BASE_URL   e.g. http://localhost:11434/v1 (a local Ollama) or a hosted endpoint
    M2A_LLM_MODEL      the model name the endpoint expects
    M2A_LLM_API_KEY    optional; sent as a Bearer token
    M2A_LLM_TIMEOUT    optional; seconds, default 60
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

from .dates import find_due
from .extract import People, number
from .model import ITEM_TYPES, Evidence, Item, Transcript


class LLMError(RuntimeError):
    """The LLM extractor is not configured or the endpoint failed."""


SYSTEM_PROMPT = """You extract meeting minutes from a transcript.
Return only JSON: {"items": [{"type": "decision|action|question|risk", "text": "...", "owner": "name or empty",
"due": "YYYY-MM-DD or empty", "line": <turn number>, "quote": "exact words copied from that turn"}]}.
Rules: decisions are things the meeting agreed; actions are tasks someone committed to or was asked to do and
accepted; questions are left open; risks are concerns or blockers raised. "quote" must be copied exactly from
the numbered turn. Owners must be people in the meeting. Do not invent items."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


def build_prompt(transcript: Transcript) -> str:
    lines = [f"Meeting: {transcript.title}"]
    if transcript.date:
        lines.append(f"Date: {transcript.date.isoformat()} ({transcript.date.strftime('%A')})")
    lines.append(f"People: {', '.join(transcript.people) or 'unknown'}")
    lines.append("")
    for u in transcript.utterances:
        lines.append(f"[{u.index}] {u.speaker or 'Unknown'}: {u.text}")
    return "\n".join(lines)


def call_chat(prompt: str, env: dict[str, str] | None = None) -> str:
    env = dict(os.environ if env is None else env)
    base = env.get("M2A_LLM_BASE_URL", "").rstrip("/")
    model = env.get("M2A_LLM_MODEL", "")
    if not base or not model:
        raise LLMError("set M2A_LLM_BASE_URL and M2A_LLM_MODEL to use --extractor llm")
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
    }
    headers = {"Content-Type": "application/json"}
    if env.get("M2A_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {env['M2A_LLM_API_KEY']}"
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=float(env.get("M2A_LLM_TIMEOUT", "60"))) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM request to {base} failed: {exc}") from exc
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("LLM response has no choices[0].message.content") from exc


def parse_answer(answer: str, transcript: Transcript) -> tuple[list[Item], list[str]]:
    """Validate the model's JSON against the transcript. Returns (items, notes about dropped items)."""
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        raise LLMError("LLM answer contains no JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM answer is not valid JSON: {exc}") from exc
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    turns = {u.index: u for u in transcript.utterances}
    people = People(transcript.people)
    items: list[Item] = []
    notes: list[str] = []
    for n, raw in enumerate(raw_items, 1):
        if not isinstance(raw, dict):
            notes.append(f"item {n}: not an object, dropped")
            continue
        kind = str(raw.get("type", "")).lower()
        if kind not in ITEM_TYPES:
            notes.append(f"item {n}: unknown type {kind!r}, dropped")
            continue
        try:
            turn = turns[int(raw.get("line"))]
        except (TypeError, ValueError, KeyError):
            notes.append(f"item {n}: points at no transcript turn, dropped")
            continue
        quote = str(raw.get("quote", "")).strip()
        if not quote or _norm(quote) not in _norm(turn.text):
            notes.append(f"item {n}: quote not found in turn {turn.index}, dropped")
            continue
        item = Item(
            type=kind,
            text=str(raw.get("text") or quote).strip(),
            evidence=Evidence(utterance=turn.index, speaker=turn.speaker, timestamp=turn.timestamp, quote=quote),
            rule="llm",
            confidence="medium",
        )
        if kind == "action":
            owner = str(raw.get("owner") or "").strip()
            if owner:
                resolved = people.resolve(owner)
                if resolved in transcript.people:
                    item.owner = resolved
                else:
                    notes.append(f"item {n}: owner {owner!r} is not in the meeting, cleared")
            due_raw = str(raw.get("due") or "").strip()
            item.due, item.due_text = find_due(quote, transcript.date)
            if not item.due and due_raw:
                try:
                    item.due, item.due_text = dt.date.fromisoformat(due_raw), due_raw
                except ValueError:
                    notes.append(f"item {n}: due {due_raw!r} is not a date, ignored")
        items.append(item)
    return number(items, transcript), notes


def extract(transcript: Transcript, env: dict[str, str] | None = None) -> tuple[list[Item], list[str]]:
    return parse_answer(call_chat(build_prompt(transcript), env), transcript)
