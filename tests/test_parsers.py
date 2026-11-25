import datetime as dt

import pytest

from meeting_to_actions import parsers


def test_vtt_voice_tags_title_date_and_merged_cues(examples):
    t = parsers.load(examples / "claims-steering.vtt")
    assert t.fmt == "vtt"
    assert t.title == "Claims modernisation steering group"
    assert t.date == dt.date(2026, 11, 24)
    assert t.speakers == ["Priya Raman", "Grace Liu", "Daniel Mensah", "Tom Okafor", "Aisha Khan"]
    first = t.utterances[0]
    # Two consecutive cues from Priya are one turn, and the second cue's start is kept.
    assert first.text.startswith("Morning all.") and "First item is the document triage pilot" in first.text
    offset = first.text.index("First item")
    assert first.time_at(offset) == "00:00:09"
    assert first.timestamp == "00:00:04"


def test_srt_with_name_prefixes(examples):
    t = parsers.load(examples / "data-platform-standup.srt")
    assert t.fmt == "srt"
    assert t.date is None
    assert t.utterances[0].speaker == "Marcus Bell"
    assert t.utterances[0].text == "Quick one today. Sofia, you first."


def test_txt_header_timestamps_and_attendees(examples):
    t = parsers.load(examples / "vendor-review.txt")
    assert t.title == "Document-AI vendor review"
    assert t.date == dt.date(2026, 11, 20)
    assert t.attendees == ["Hannah Cole", "Ravi Nair", "Olga Brandt", "Sam Whitfield"]
    assert t.utterances[1].start == 12.0
    assert t.utterances[1].speaker == "Ravi Nair"


def test_txt_without_timestamps_and_continuation_lines():
    text = "Ana: We should ship it.\nand carry on here\nBo: Fine.\n"
    t = parsers.parse_text(text, "txt")
    assert [u.speaker for u in t.utterances] == ["Ana", "Bo"]
    assert t.utterances[0].text == "We should ship it. and carry on here"
    assert t.utterances[0].start is None


def test_bad_files_raise_clear_errors(tmp_path):
    with pytest.raises(parsers.TranscriptError, match="file not found"):
        parsers.load(tmp_path / "missing.vtt")
    empty = tmp_path / "empty.vtt"
    empty.write_text("WEBVTT\n\n", encoding="utf-8")
    with pytest.raises(parsers.TranscriptError, match="no VTT cues"):
        parsers.load(empty)


def test_numbered_speaker_labels():
    t = parsers.parse_text("Speaker 1: I'll send the notes.\nSpeaker 2: Thanks.\n", "txt")
    assert t.speakers == ["Speaker 1", "Speaker 2"]
