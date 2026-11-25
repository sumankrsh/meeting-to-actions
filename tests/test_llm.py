import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meeting_to_actions import llm, parsers
from meeting_to_actions.pipeline import run


def answer_for(items):
    return json.dumps({"items": items})


class FakeChat(BaseHTTPRequestHandler):
    reply = ""
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeChat.seen.append((self.path, self.headers.get("Authorization"), body))
        out = json.dumps({"choices": [{"message": {"content": FakeChat.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeChat)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    FakeChat.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_llm_items_are_checked_against_the_redacted_transcript(examples, server):
    FakeChat.reply = "Here you go:\n" + answer_for(
        [
            {
                "type": "action",
                "text": "Update scope doc",
                "owner": "Grace",
                "line": 6,
                "quote": "can you update the pilot scope document and send it round by Friday?",
            },
            {"type": "action", "text": "Invented", "owner": "Grace Liu", "line": 6, "quote": "words nobody said"},
            {"type": "decision", "text": "x", "line": 999, "quote": "x"},
            {"type": "action", "text": "Mystery", "owner": "Zed", "line": 10, "quote": "Thanks Tom."},
            {"type": "gossip", "text": "x", "line": 1, "quote": "Morning all."},
        ]
    )
    env = {"M2A_LLM_BASE_URL": server, "M2A_LLM_MODEL": "test-model", "M2A_LLM_API_KEY": "fake-key-for-tests"}
    result = run(parsers.load(examples / "claims-steering.vtt"), extractor="llm", env=env)
    path, auth, body = FakeChat.seen[0]
    assert path == "/v1/chat/completions" and auth == "Bearer fake-key-for-tests" and body["model"] == "test-model"
    sent = body["messages"][1]["content"]
    assert "elena.varga@example.com" not in sent and "[EMAIL-1]" in sent
    a1, a2 = result.items
    assert (a1.owner, str(a1.due), a1.rule) == ("Grace Liu", "2026-11-27", "llm")
    assert a2.owner == "" and "no owner" in a2.flags
    assert sum("dropped" in n for n in result.notes) == 3
    assert any("'Zed' is not in the meeting" in n for n in result.notes)


def test_llm_needs_configuration(examples):
    with pytest.raises(llm.LLMError, match="M2A_LLM_BASE_URL"):
        run(parsers.load(examples / "vendor-review.txt"), extractor="llm", env={})


def test_llm_rejects_non_json_answers(examples):
    t = parsers.load(examples / "vendor-review.txt")
    with pytest.raises(llm.LLMError, match="no JSON"):
        llm.parse_answer("I could not find anything.", t)
