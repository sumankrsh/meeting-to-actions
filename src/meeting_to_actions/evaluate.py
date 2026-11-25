"""Score extracted items against a hand-labelled answer file.

The answer file is JSON: {"items": [{"type": "action", "text": "...", "owner": "...", "due": "YYYY-MM-DD"}]}.
An extracted item matches a labelled one when they have the same type and
their content words overlap enough: at least two shared words, making up at
least 40% of the shorter text (see MATCH_THRESHOLD). Labels are paraphrases,
so the extracted quote is compared too. Owner and due date are then
scored on the matched actions only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .extract import _words
from .model import ITEM_TYPES, Item

MATCH_THRESHOLD = 0.4  # shared words / words in the shorter text
MIN_SHARED = 2


def _stems(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _words(text)}


def overlap(a: str, b: str) -> float:
    """Overlap coefficient of content words (crudely singularised); 0 if fewer than MIN_SHARED shared."""
    wa, wb = _stems(a), _stems(b)
    shared = len(wa & wb)
    if not wa or not wb or shared < MIN_SHARED:
        return 0.0
    return shared / min(len(wa), len(wb))


@dataclass
class TypeScore:
    type: str
    expected: int = 0
    found: int = 0
    matched: int = 0

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else 1.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


@dataclass
class Score:
    types: dict[str, TypeScore]
    owner_right: int = 0
    due_right: int = 0
    actions_matched: int = 0
    missed: list[dict] = field(default_factory=list)
    extra: list[Item] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return sum(t.matched for t in self.types.values())

    @property
    def expected(self) -> int:
        return sum(t.expected for t in self.types.values())

    @property
    def found(self) -> int:
        return sum(t.found for t in self.types.values())

    def to_dict(self) -> dict:
        return {
            "types": {
                k: {
                    "expected": v.expected,
                    "found": v.found,
                    "matched": v.matched,
                    "precision": round(v.precision, 3),
                    "recall": round(v.recall, 3),
                }
                for k, v in self.types.items()
            },
            "overall": {"expected": self.expected, "found": self.found, "matched": self.matched},
            "actions": {"matched": self.actions_matched, "owner_right": self.owner_right, "due_right": self.due_right},
            "missed": self.missed,
            "extra": [{"id": i.id, "type": i.type, "text": i.text} for i in self.extra],
        }


def load_expected(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected {{'items': [...]}}")
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get("type") not in ITEM_TYPES or not item.get("text"):
            raise ValueError(f"{path}: item {n} needs a type ({', '.join(ITEM_TYPES)}) and a text")
    return items


def score(found: list[Item], expected: list[dict]) -> Score:
    result = Score(types={t: TypeScore(t) for t in ITEM_TYPES})
    for kind in ITEM_TYPES:
        preds = [i for i in found if i.type == kind]
        golds = [g for g in expected if g["type"] == kind]
        ts = result.types[kind]
        ts.found, ts.expected = len(preds), len(golds)
        pairs = []
        for pi, p in enumerate(preds):
            for gi, g in enumerate(golds):
                sim = max(overlap(p.text, g["text"]), overlap(p.evidence.quote, g["text"]))
                if sim >= MATCH_THRESHOLD:
                    pairs.append((sim, pi, gi))
        used_p: set[int] = set()
        used_g: set[int] = set()
        for _, pi, gi in sorted(pairs, reverse=True):
            if pi in used_p or gi in used_g:
                continue
            used_p.add(pi)
            used_g.add(gi)
            ts.matched += 1
            if kind == "action":
                p, g = preds[pi], golds[gi]
                result.actions_matched += 1
                result.owner_right += (p.owner or "") == (g.get("owner") or "")
                result.due_right += (p.due.isoformat() if p.due else "") == (g.get("due") or "")
        result.missed.extend(g for gi, g in enumerate(golds) if gi not in used_g)
        result.extra.extend(p for pi, p in enumerate(preds) if pi not in used_p)
    return result
