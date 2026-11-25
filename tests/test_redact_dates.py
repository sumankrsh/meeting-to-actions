import datetime as dt

from meeting_to_actions import dates, parsers
from meeting_to_actions.redact import iban_ok, luhn_ok, redact, redact_text


def test_detectors_replace_personal_data_with_stable_placeholders():
    table, counts = {}, {}
    text, found = redact_text(
        "Mail jo.bloggs@example.com or call +44 7700 900461. Mail jo.bloggs@example.com again.", table, counts
    )
    assert text == "Mail [EMAIL-1] or call [PHONE-1]. Mail [EMAIL-1] again."
    assert found.count("[EMAIL-1]") == 2


def test_checksums_avoid_false_positives():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert iban_ok("GB82 WEST 1234 5698 7654 32")
    assert not iban_ok("GB00 WEST 1234 5698 7654 32")
    table, counts = {}, {}
    text, _ = redact_text("Card 4111 1111 1111 1112 and claim CLM-2026-004417 on 2026-11-24.", table, counts)
    assert "[" not in text  # bad checksum, a claim reference and a date are left alone


def test_titled_names_ip_and_iban(examples):
    t, log = redact(parsers.load(examples / "vendor-review.txt"))
    assert {r.kind for r in log} == {"IBAN"}
    assert "GB82" not in " ".join(u.text for u in t.utterances)
    t, log = redact(parsers.load(examples / "claims-steering.vtt"))
    joined = " ".join(u.text for u in t.utterances)
    assert "Elena Varga" not in joined and "07700 900461" not in joined and "4111" not in joined
    assert sorted({r.kind for r in log}) == ["CARD", "EMAIL", "NAME", "PHONE"]
    # Speaker labels are kept so actions can be assigned.
    assert "Grace Liu" in t.speakers


def test_redaction_keeps_cue_timestamps_aligned(examples):
    t, _ = redact(parsers.load(examples / "claims-steering.vtt"))
    turn = next(u for u in t.utterances if "[EMAIL-1]" in u.text)
    assert turn.time_at(turn.text.index("[EMAIL-1]")) == "00:00:53"


TUESDAY = dt.date(2026, 11, 24)


def test_relative_due_dates_follow_the_documented_rules():
    cases = {
        "by Friday": dt.date(2026, 11, 27),
        "next Tuesday": dt.date(2026, 12, 1),
        "next Wednesday": dt.date(2026, 12, 2),
        "tomorrow": dt.date(2026, 11, 25),
        "by end of week": dt.date(2026, 11, 27),
        "end of the month": dt.date(2026, 11, 30),
        "next week": dt.date(2026, 11, 30),
        "in 2 weeks": dt.date(2026, 12, 8),
        "within three days": dt.date(2026, 11, 27),
        "by the 5th of December": dt.date(2026, 12, 5),
        "on March 3": dt.date(2027, 3, 3),
        "by 2027-01-15": dt.date(2027, 1, 15),
        "this afternoon": TUESDAY,
        "on Tuesday": dt.date(2026, 12, 1),
    }
    for phrase, expected in cases.items():
        assert dates.find_due(f"I'll do it {phrase}.", TUESDAY)[0] == expected, phrase


def test_due_phrase_kept_without_a_meeting_date():
    assert dates.find_due("Send it by Thursday", None) == (None, "by Thursday")
    assert dates.find_due("Send it on 12 December 2026", None) == (dt.date(2026, 12, 12), "12 December 2026")
    assert dates.find_due("No date here", TUESDAY) == (None, "")
