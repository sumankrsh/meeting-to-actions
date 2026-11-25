"""Rule-based extraction of decisions, actions, open questions and risks.

The extractor reads the (already redacted) transcript one sentence at a time,
in order, and keeps a little conversational state:

- a request ("Priya, can you send the file?") becomes an action owned by the
  person asked; their reply confirms it ("Sure") or withdraws it ("I can't")
- an unowned "we need to ..." waits for someone to say "I'll take that"
- "scratch that" withdraws the speaker's last item

Every item keeps the sentence it came from, so a reviewer can check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dates import find_due
from .model import Evidence, Item, Transcript, Utterance

# --- patterns -------------------------------------------------------------

DECISION = re.compile(
    r"\b(?:decision(?: is)?\s*[:-]|agreed\s*[:-]|(?:so,? )?we(?:'ve| have)? (?:all )?(?:decided|agreed)|"
    r"(?:it's|it is|that's) (?:decided|agreed|settled|approved)|we(?:'re| are) agreed|"
    r"let's go with|we(?:'ll| will) go with|we(?:'re| are) going (?:with|ahead)|final decision|"
    r"we(?:'ll| will) stick with|(?:the )?committee approved|we(?:'ve| have) approved|approved:)",
    re.I,
)
DECISION_STRONG = re.compile(r"decision|agreed|decided|approved|settled", re.I)
DECISION_LEAD = re.compile(
    r"^.*?\b(?:decision(?: is)?\s*[:-]|agreed\s*[:-]|approved:|we(?:'ve| have)? (?:all )?(?:decided|agreed)"
    r"(?: that| to| on)?|(?:it's|it is|that's) (?:decided|agreed|settled|approved)(?: that|:)?|"
    r"we(?:'re| are) agreed(?: that|:|,)?|final decision(?: is)?(?: that|:)?)\s*",
    re.I,
)
MARKER = re.compile(r"^(?:action(?: item)?|todo|to-do|next step)s?\s*(?:for ([A-Z][\w'-]+))?\s*[:-]\s*(.+)$", re.I)
SELF = re.compile(
    r"\b(I'll|I will|I'm going to|I am going to|I can|let me|leave (?:it|that|this) with me|I'm on it|"
    r"I've got (?:it|that|this)|I'll own|I'll take)\b\s*(.*)$",
    re.I,
)
REQUEST = re.compile(r"\b(?:can|could|would|will) you(?: please)?\b\s*(.*)$", re.I)
PLEASE = re.compile(r"^(?:and |also |so )?please\s+(.*)$", re.I)
GROUP = re.compile(
    r"\b(?:we need to|we should|we must|we have to|we'll need to|someone (?:needs|has) to|"
    r"somebody (?:needs|has) to|someone should|somebody should)\s+(.*)$",
    re.I,
)
RETRACT = re.compile(
    r"\b(?:scratch that|never mind|ignore (?:that|what I said)|forget (?:that|it)|let's not do that|"
    r"on second thought|actually,? no)\b[,.;:!-]*\s*",
    re.I,
)
ACCEPT = re.compile(
    r"^(?:yes|yep|yeah|sure|of course|will do|on it|no problem|happy to|ok|okay|absolutely|"
    r"consider it done|can do|i can do that|i'll do that|that's fine|fine)\b",
    re.I,
)
DECLINE = re.compile(
    r"^(?:sorry,?\s*|i'm afraid,?\s*|unfortunately,?\s*)?(?:no\b|nope|i can't|i cannot|i won't|not me|not this week|i don't have (?:the )?(?:time|access))",
    re.I,
)
RISK = re.compile(
    r"\b(risks?|concern(?:ed|s)?|worried|worry|blockers?|blocked|blocking|might slip|could slip|"
    r"will slip|at risk|delays?|delayed|dependency|bottleneck|exposure|won't make|can't meet|(?:will|could|might) be late|keeps? failing)\b",
    re.I,
)
RISK_NEGATED = re.compile(
    r"\b(?:no|not|low|isn't|aren't|don't see|nothing|without)\b(?:\W+\w+){0,3}?\W+"
    r"(?:risks?|concerns?|concerned|worried|worry|blockers?|blocked|delays?|delayed|slip)\b",
    re.I,
)
OPEN = re.compile(
    r"\b(open question|still (?:need|have) to (?:decide|confirm|figure out|work out|agree)|"
    r"to be (?:decided|confirmed)|tbd|tbc|parking lot|park (?:that|this|it)|unclear (?:whether|if|who|how)|"
    r"we don't know (?:yet|whether|if|who)|nobody knows|no one knows|not decided)\b",
    re.I,
)
UNSURE = re.compile(
    r"\b(not sure|don't know|no idea|good question|come back to (?:that|this|it)|take (?:it|that|this) offline|"
    r"can't say|unclear|need to check|nobody knows|no one knows|not yet|depends)\b",
    re.I,
)
# Openings that are talk about the meeting itself, not commitments.
NOT_COMMITMENTS = re.compile(
    r"^(?:be (?!\w+ing\b)\w+|say|admit|tell you|guess|think|see|hear|know|try to keep|keep (?:it|this) short|"
    r"share (?:my|the) screen|stop sharing|pull (?:it|that) up|bring (?:it|that) up|go back|go first|jump in|"
    r"hand (?:it )?over|pass (?:it )?(?:to|over)|start|recap|summari[sz]e|introduce|explain|ask (?:a|one|you) (?:quick )?question|add|repeat|"
    r"live with|bet|leave it there|just say|be quick|wrap up|mute|unmute|confirm (?:I|that I) can hear|"
    r"remember|imagine|understand|agree|follow)\b",
    re.I,
)
NOT_REQUESTS = re.compile(
    r"^(?:hear|see (?:my|the) screen|see (?:it|that|this)|repeat|say that again|go back|remind me|explain|clarify|"
    r"talk me through|walk me through|mute|unmute|share your screen|confirm you can hear)\b",
    re.I,
)
ANAPHORIC_START = re.compile(r"^(?:that's|that is|it's|it is|this is|that one's|that)\b", re.I)
BACK_REFERENCE = re.compile(r"\b(?:park|come back to|revisit|parking lot for) (?:that|this|it)\b", re.I)
ANAPHOR = re.compile(r"\b(?:it|that|this|them|those|that one|this one)\b", re.I)
LEADING_FILLER = re.compile(r"^(?:and|also|so|then|just|ok|okay|right|sure|yes|yeah|great|fine|first)\b[,]?\s*", re.I)
TRAILING_FILLER = re.compile(r"[,;]?\s*(?:please|thanks|thank you|if that's ok|if you can|ok|okay)?[?.!]*$", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(
    "the a an to of and or for on in at by with from this that it is be we i you our your will can "
    "please just also then so".split()
)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def _tidy(text: str) -> str:
    text = LEADING_FILLER.sub("", text.strip())
    text = TRAILING_FILLER.sub("", text).strip(" ,;:-")
    return text[:1].upper() + text[1:] if text else text


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Word overlap (Jaccard) between two texts, ignoring common words."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def same_item(a: str, b: str) -> bool:
    """True when two texts describe the same item: a high overlap, or one contained in the other."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    shared = len(wa & wb)
    return shared / len(wa | wb) >= 0.6 or (min(len(wa), len(wb)) >= 2 and shared / min(len(wa), len(wb)) >= 0.8)


# --- people -----------------------------------------------------------------


class People:
    """Maps names said in the meeting ("Priya") to people ("Priya Raman")."""

    def __init__(self, names: list[str]):
        self.names = [n for n in names if n]
        index: dict[str, str] = {}
        for name in self.names:
            index.setdefault(name.lower(), name)
        for name in self.names:
            first = name.split()[0].lower()
            index.setdefault(first, name)
        self.index = index
        alternatives = sorted(index, key=len, reverse=True)
        body = "|".join(re.escape(a) for a in alternatives) or r"(?!x)x"
        self.pattern = re.compile(rf"\b({body})\b", re.I)
        self.vocative = re.compile(rf"^(?:\w+,\s*)?({body}),\s*(.*)$", re.I)
        self.named = re.compile(
            rf"(?<!\bto )(?<!\bwith )(?<!\bfrom )(?<!\bfor )(?<!\band )\b({body})(?:'s team)? "
            rf"(?:will|'ll|is going to|to|needs to|has to|has agreed to|is taking|is on)\s+(.*)$",
            re.I,
        )

    def resolve(self, said: str) -> str:
        return self.index.get(said.lower(), said)


# --- extraction ---------------------------------------------------------------


@dataclass
class _Pending:
    item: Item
    utterance: int
    awaiting: str  # the person whose reply settles it; "" means anyone may take it


@dataclass
class _State:
    items: list[Item] = field(default_factory=list)
    pending: list[_Pending] = field(default_factory=list)
    last_question: tuple[int, str] | None = None


def _evidence(u: Utterance, quote: str) -> Evidence:
    offset = u.text.find(quote)
    stamp = u.time_at(offset) if offset >= 0 else u.timestamp
    return Evidence(utterance=u.index, speaker=u.speaker, timestamp=stamp, quote=quote)


def _make(kind: str, text: str, u: Utterance, quote: str, rule: str, confidence: str, ref) -> Item:
    item = Item(type=kind, text=_tidy(text), evidence=_evidence(u, quote), rule=rule, confidence=confidence)
    if kind == "action":
        item.due, item.due_text = find_due(quote, ref)
    return item


def _expire(state: _State, now: int) -> None:
    state.pending = [p for p in state.pending if now - p.utterance <= 2]


def _accept(state: _State, speaker: str, sentence: str, u: Utterance, ref) -> bool:
    """Let `speaker` take on a pending request or unowned action. True if one was found."""
    for p in reversed(state.pending):
        if p.utterance == u.index:
            continue
        if p.awaiting and p.awaiting != speaker:
            continue
        if not p.awaiting and p.item.evidence.speaker == speaker:
            continue
        p.item.owner = speaker
        p.item.confidence = "high"
        p.item.rule += "+accepted"
        stamp = _evidence(u, sentence).timestamp or f"turn {u.index}"
        p.item.confirmed_by = f'{speaker} at {stamp}: "{sentence}"'
        if not p.item.due:
            due, text = find_due(sentence, ref)
            if due or text:
                p.item.due, p.item.due_text = due, text
        state.pending.remove(p)
        return True
    return False


def _decline(state: _State, speaker: str) -> bool:
    for p in reversed(state.pending):
        if p.awaiting == speaker and p.item in state.items:
            state.items.remove(p.item)
            state.pending.remove(p)
            return True
    return False


def _retract(state: _State, speaker: str, now: int) -> None:
    for item in reversed(state.items):
        if now - item.evidence.utterance > 2:
            break
        if item.type in {"action", "decision"} and (item.evidence.speaker == speaker or item.owner == speaker):
            state.items.remove(item)
            state.pending = [p for p in state.pending if p.item is not item]
            return


def _next_speaker(utterances: list[Utterance], pos: int) -> str:
    me = utterances[pos].speaker
    for u in utterances[pos + 1 : pos + 3]:
        if u.speaker and u.speaker != me:
            return u.speaker
    return ""


def _reply(utterances: list[Utterance], pos: int) -> Utterance | None:
    me = utterances[pos].speaker
    for u in utterances[pos + 1 : pos + 2]:
        if u.speaker != me:
            return u
    return None


def _sentence_items(
    s: str, u: Utterance, pos: int, transcript: Transcript, people: People, state: _State, first: bool
) -> None:
    ref = transcript.date
    speaker = u.speaker

    m = RETRACT.search(s)
    if m:
        _retract(state, speaker, u.index)
        s = s[m.end() :].strip()
        if not s:
            return

    if first:
        for p in list(state.pending):
            if p.awaiting == speaker and DECLINE.match(s):
                _decline(state, speaker)
                return
        if ACCEPT.match(s) and len(s.split()) <= 4:
            # A bare "Sure" settles a request made to this speaker. It doesn't volunteer
            # for an unowned "we need to ...": that takes "I'll take that" or similar.
            if any(p.awaiting == speaker for p in state.pending):
                _accept(state, speaker, s, u, ref)
                return
            if not SELF.search(s):
                return

    if DECISION.search(s) and not s.rstrip().endswith("?"):
        text = DECISION_LEAD.sub("", s, count=1) or s
        conf = "high" if DECISION_STRONG.search(s) else "medium"
        state.items.append(_make("decision", text, u, s, "decision-phrase", conf, ref))
        return

    m = MARKER.match(s)
    if m:
        owner_said, rest = m.group(1), m.group(2)
        named = people.named.match(rest)
        item = _make("action", named.group(2) if named else rest, u, s, "action-marker", "high", ref)
        if owner_said:
            item.owner = people.resolve(owner_said)
        elif named:
            item.owner = people.resolve(named.group(1))
        state.items.append(item)
        if not item.owner:
            item.confidence = "medium"
            state.pending.append(_Pending(item, u.index, ""))
        return

    m = people.vocative.match(s)
    vocative = people.resolve(m.group(1)) if m else ""
    body = m.group(2) if m else s

    m = REQUEST.search(body)
    if m and not NOT_REQUESTS.match(m.group(1)) and m.group(1):
        addressee = vocative or _next_speaker(transcript.utterances, pos)
        if addressee and addressee != speaker:
            reply = _reply(transcript.utterances, pos)
            conf = "medium" if vocative else "low"
            if reply is not None and reply.speaker == addressee:
                head = sentences(reply.text)[0] if reply.text else ""
                if DECLINE.match(head):
                    return
            item = _make("action", m.group(1), u, s, "request", conf, ref)
            item.owner = addressee
            state.items.append(item)
            state.pending.append(_Pending(item, u.index, addressee))
            return

    m = PLEASE.match(body)
    if m and vocative and vocative != speaker:
        item = _make("action", m.group(1), u, s, "request", "medium", ref)
        item.owner = vocative
        state.items.append(item)
        state.pending.append(_Pending(item, u.index, vocative))
        return

    m = people.named.search(s)
    if m and not s.rstrip().endswith("?"):
        owner = people.resolve(m.group(1))
        item = _make("action", m.group(2), u, s, "named-owner", "high" if owner == speaker else "medium", ref)
        item.owner = owner
        state.items.append(item)
        return

    m = SELF.search(s)
    if m and not s.rstrip().endswith("?") and speaker:
        phrase, rest = m.group(1), m.group(2).strip()
        rest_clean = _tidy(rest)
        pure = (
            not rest_clean
            or re.match(r"(?i)^(?:with me|on it|got (?:it|that))$", rest_clean)
            or re.match(r"(?i)^leave|^i'm on it|^i've got", phrase)
        )
        short_anaphor = len(rest_clean.split()) <= 8 and ANAPHOR.search(rest_clean)
        if pure or (short_anaphor and any(p.awaiting in (speaker, "") for p in state.pending)):
            if _accept(state, speaker, s, u, ref):
                return
            if pure or re.match(r"(?i)^(?:do|take|handle|own|pick up|sort) (?:it|that|this)\b", rest_clean):
                return
        if rest_clean and not NOT_COMMITMENTS.match(rest_clean):
            if phrase.lower() == "i can" and not re.match(r"(?i)^(?:send|draft|share|book|set|run|get|do|write)", rest):
                pass  # "I can see why" — too ambiguous to call a commitment
            else:
                item = _make("action", rest, u, s, "self-commitment", "high", ref)
                item.owner = speaker
                state.items.append(item)
                return

    m = GROUP.search(s)
    if m and not re.match(r"(?i)^(?:be|have|probably|maybe|not)\b", m.group(1)) and not s.rstrip().endswith("?"):
        item = _make("action", m.group(1), u, s, "group-need", "low", ref)
        state.items.append(item)
        state.pending.append(_Pending(item, u.index, ""))
        return

    if OPEN.search(s):
        text = s
        points_back = ANAPHORIC_START.match(s) or BACK_REFERENCE.search(s)
        if state.last_question and u.index - state.last_question[0] <= 2 and points_back:
            text = state.last_question[1]  # "That's still an open question" points back at the question
        state.items.append(_make("question", text, u, s, "open-marker", "high", ref))
        return

    if s.rstrip().endswith("?"):
        if state.last_question and state.last_question[0] == u.index:
            state.last_question = (u.index, f"{state.last_question[1]} {s}")
        else:
            state.last_question = (u.index, s)
        reply = _reply(transcript.utterances, pos)
        if reply is not None and reply.text and UNSURE.search(sentences(reply.text)[0]):
            state.items.append(_make("question", s, u, s, "unanswered-question", "medium", ref))
        return

    if RISK.search(s) and not RISK_NEGATED.search(s):
        if len(s.split()) <= 6 and ANAPHORIC_START.match(s):
            return  # "That's my worry too" agrees with a risk already raised
        last = state.items[-1] if state.items else None
        if last and last.type == "risk" and last.evidence.utterance == u.index:
            # Consecutive risk sentences in one turn describe one risk.
            last.evidence.quote = f"{last.evidence.quote} {s}"
            last.text = f"{last.text}. {_tidy(s)}"
            return
        state.items.append(_make("risk", s, u, s, "risk-phrase", "medium", ref))


def _flag(item: Item, transcript: Transcript) -> None:
    item.flags = []
    if item.type != "action":
        return
    if not item.owner:
        item.flags.append("no owner")
    elif item.owner not in transcript.people:
        item.flags.append("owner not in meeting")
    if not item.due_text:
        item.flags.append("no due date")
    elif item.due is None:
        item.flags.append("due date not resolved")


def _dedupe(items: list[Item]) -> list[Item]:
    kept: list[Item] = []
    for item in items:
        twin = next(
            (k for k in kept if k.type == item.type and k.owner == item.owner and same_item(k.text, item.text)),
            None,
        )
        if twin is None and item.type == "action" and item.owner:
            # An owned action that restates an earlier unowned one ("we should purge it" ...
            # "Action: Tom to purge the test data") replaces it.
            loose = next(
                (
                    k
                    for k in kept
                    if k.type == "action"
                    and not k.owner
                    and item.evidence.utterance - k.evidence.utterance <= 2
                    and similarity(k.text, item.text) >= 0.3
                ),
                None,
            )
            if loose is not None:
                kept[kept.index(loose)] = item
                continue
        if twin is None:
            kept.append(item)
            continue
        twin.also_at.append(item.evidence.timestamp or f"turn {item.evidence.utterance}")
        if item.confidence == "high":
            twin.confidence = "high"
        if not twin.due and item.due:
            twin.due, twin.due_text = item.due, item.due_text
    return kept


PREFIX = {"decision": "D", "action": "A", "question": "Q", "risk": "R"}


def number(items: list[Item], transcript: Transcript) -> list[Item]:
    """Flag, de-duplicate and number items (D1, A1, Q1, R1) in transcript order."""
    items = sorted(items, key=lambda i: i.evidence.utterance)
    for item in items:
        _flag(item, transcript)
    items = _dedupe(items)
    counters: dict[str, int] = {}
    for item in items:
        counters[item.type] = counters.get(item.type, 0) + 1
        item.id = f"{PREFIX[item.type]}{counters[item.type]}"
    return items


def extract(transcript: Transcript) -> list[Item]:
    people = People(transcript.people)
    state = _State()
    for pos, u in enumerate(transcript.utterances):
        _expire(state, u.index)
        for k, s in enumerate(sentences(u.text)):
            _sentence_items(s, u, pos, transcript, people, state, first=k == 0)
    return number(state.items, transcript)
