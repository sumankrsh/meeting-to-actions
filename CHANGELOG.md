# Changelog

## 0.1.0 (2026-11-25)

First release.

- `extract`: decisions, actions with owners and due dates, open questions and risks from WebVTT, SRT or plain-text transcripts. Every item keeps its quote, speaker and timestamp. Terminal, Markdown, single-file HTML, CSV, Jira CSV import and JSON outputs. `--require-owner` exits 1 when an action has no owner.
- Ten extraction rules, including requests settled by the addressee's reply, unowned needs taken by a volunteer, and "scratch that" retractions. `rules` lists them.
- Redaction of emails, phone numbers, card numbers, IBANs, SSNs, UK NI numbers, IPv4 addresses and titled names before extraction. `redact` shows what the extractor sees.
- Due dates resolved against the meeting date.
- `eval`: precision and recall per item type against hand-labelled items.
- An optional extractor for OpenAI-compatible chat endpoints that only receives the redacted transcript and drops items whose quote can't be found.
- Four synthetic transcripts with labels, one of them held out from tuning.
