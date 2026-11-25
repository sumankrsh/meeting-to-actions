import datetime as dt

from meeting_to_actions import parsers
from meeting_to_actions.pipeline import run, run_file

DATE = dt.date(2026, 11, 24)


def items(text: str, date=DATE):
    return run(parsers.parse_text(text, "txt"), meeting_date=date).items


def only(found, kind):
    return [i for i in found if i.type == kind]


def test_claims_example_end_to_end(examples):
    result = run_file(examples / "claims-steering.vtt")
    counts = {k: len(result.of_type(k)) for k in ("decision", "action", "question", "risk")}
    assert counts == {"decision": 2, "action": 8, "question": 2, "risk": 3}
    a1 = result.of_type("action")[0]
    assert (a1.owner, a1.due, a1.confidence) == ("Grace Liu", dt.date(2026, 11, 27), "high")
    assert a1.confirmed_by.startswith('Grace Liu at 00:00:50: "Sure, will do."')
    # Every quote is the redacted text of the turn it points at.
    turns = {u.index: u.text for u in result.transcript.utterances}
    for item in result.items:
        assert item.evidence.quote in turns[item.evidence.utterance]
        assert "@" not in item.evidence.quote


def test_self_commitment_owner_is_the_speaker():
    (a,) = only(items("Ana Diaz: I'll send the revised budget to finance by Friday."), "action")
    assert (a.owner, a.text, a.due) == (
        "Ana Diaz",
        "Send the revised budget to finance by Friday",
        dt.date(2026, 11, 27),
    )


def test_meeting_talk_is_not_a_commitment():
    found = items("Ana Diaz: I'll be honest, it's been a long week. Let me share my screen. I'll hand over to Bo.")
    assert only(found, "action") == []


def test_request_owned_by_addressee_and_confirmed_by_reply():
    (a,) = only(items("Ana Diaz: Bo, can you book the room for Thursday?\nBo Chen: Sure."), "action")
    assert (a.owner, a.confidence, a.due) == ("Bo Chen", "high", dt.date(2026, 11, 26))


def test_declined_request_is_dropped():
    found = items("Ana Diaz: Bo, could you draft the memo?\nBo Chen: Sorry, I can't this week.")
    assert only(found, "action") == []


def test_unanswered_request_without_name_is_low_confidence():
    (a,) = only(items("Ana Diaz: Can you check the vendor contract?\nBo Chen: The contract is long."), "action")
    assert (a.owner, a.confidence) == ("Bo Chen", "low")


def test_unowned_need_is_taken_by_whoever_volunteers():
    found = items("Ana Diaz: Someone needs to tell the analysts.\nBo Chen: Leave it with me.")
    (a,) = only(found, "action")
    assert a.owner == "Bo Chen" and "no owner" not in a.flags
    (b,) = only(items("Ana Diaz: We need to update the runbook.\nBo Chen: Agreed, it's old."), "action")
    assert b.owner == "" and "no owner" in b.flags and b.confidence == "low"


def test_named_owner_and_action_marker():
    found = items("Ana Diaz: Bo will test the backup restore next week.\nBo Chen: Action: Ana to sign off the plan.")
    a, b = only(found, "action")
    assert (a.owner, a.due) == ("Bo Chen", dt.date(2026, 11, 30))
    assert b.owner == "Ana Diaz" and b.rule == "action-marker"


def test_scratch_that_withdraws_the_last_action():
    found = items("Ana Diaz: I'll send the deck on Friday. Actually, scratch that, I'll send it next Monday instead.")
    (a,) = only(found, "action")
    assert a.due == dt.date(2026, 11, 30)


def test_decisions_questions_and_risks():
    found = items(
        "Ana Diaz: So we've agreed to move the launch to March.\n"
        "Bo Chen: Who approves the budget?\n"
        "Ana Diaz: Good question, I'm not sure.\n"
        "Bo Chen: Pricing is still TBD.\n"
        "Ana Diaz: I'm worried the supplier might slip. There's no risk on the API side."
    )
    (d,) = only(found, "decision")
    assert d.text == "Move the launch to March" and d.confidence == "high"
    assert [q.text for q in only(found, "question")] == ["Who approves the budget", "Pricing is still TBD"]
    (r,) = only(found, "risk")
    assert "supplier might slip" in r.text


def test_repeats_are_merged_and_flags_raised():
    found = items(
        "Ana Diaz: Bo will draft the risk note.\nBo Chen: OK.\nAna Diaz: To recap, Bo will draft the risk note."
    )
    (a,) = only(found, "action")
    assert a.also_at == ["turn 3"]
    assert a.flags == ["no due date"]


def test_unresolved_due_without_meeting_date(examples):
    result = run_file(examples / "data-platform-standup.srt")
    retry = next(i for i in result.of_type("action") if "retry" in i.text)
    assert retry.due is None and retry.due_text == "by Thursday" and "due date not resolved" in retry.flags
    assert any("No meeting date" in n for n in result.notes)
    dated = run_file(examples / "data-platform-standup.srt", meeting_date=dt.date(2026, 11, 25))
    assert next(i for i in dated.of_type("action") if "retry" in i.text).due == dt.date(2026, 11, 26)


def test_attendee_who_did_not_speak_can_own_actions():
    t = parsers.parse_text("Ana Diaz: Cara will prepare the audit pack.", "txt")
    (a,) = only(run(t, attendees=["Cara Novak"], meeting_date=DATE).items, "action")
    assert a.owner == "Cara Novak" and "owner not in meeting" not in a.flags


def test_bare_yes_does_not_volunteer_but_i_can_do_that_does():
    (a,) = only(items("Ana Diaz: Someone should email the auditors.\nBo Chen: I can do that."), "action")
    assert a.owner == "Bo Chen" and a.confirmed_by.startswith("Bo Chen")
