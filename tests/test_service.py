import json
import time
from types import SimpleNamespace

from thymos.service import Thymos, flatten, parse
from thymos.cli import status

HOME_MODEL = "gemma3:12b"


class FakeLlm:
    """Stands in for ctx.llm: records what it was sent and answers as `model`."""
    def __init__(self, reply='{"record_state": []}', model=HOME_MODEL, provider="ollama"):
        self.reply, self.model, self.provider, self.calls = reply, model, provider, []

    def complete(self, messages, **kw):
        self.calls.append(messages)
        return SimpleNamespace(text=self.reply, model=self.model, provider=self.provider)


def make(tmp_path, llm=None, soul="You are Athena. You keep your own counsel."):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "SOUL.md").write_text(soul, encoding="utf-8")
    svc = Thymos(tmp_path, llm=llm or FakeLlm(), config={"hold_seconds": 5})
    svc.fingerprinter = lambda provider, model: ""      # no model server in tests
    svc.compressed_from = lambda session_id: ""         # no session store in tests
    return svc


def start(svc, session="s1", model=HOME_MODEL):
    """What Hermes does at a session's start: render the prompt sections, then the first pre_llm_call."""
    text = svc.section({"session_id": session, "model": model, "provider": "ollama", "platform": "cli"})
    svc.pre_llm_call(session_id=session, model=model, platform="cli", is_first_turn=True)
    return text


HISTORY = [
    {"role": "user", "content": "How did the talk go?"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "memory", "arguments": '{"q": "talk"}'}}]},
    {"role": "tool", "name": "memory", "content": "She gave a talk on Tuesday."},
    {"role": "assistant", "content": "You told me it went well. Would you like me to reflect on it?"},
]


def turn_end(svc, session="s1", model=HOME_MODEL, history=HISTORY):
    svc.post_llm_call(session_id=session, conversation_history=history, model=model, platform="cli")
    assert svc._done.wait(5)


def test_the_first_session_records_her_seed_and_home_model(tmp_path):
    svc = make(tmp_path)
    text = start(svc)
    seed = svc.chain.entries()[0]
    assert seed["kind"] == "seed" and seed["author"] == "user" and seed["text"].startswith("You are Athena.")
    assert svc.home_model() == ("ollama", HOME_MODEL)
    assert "Only you write here." in text and "(none yet)" in text
    start(svc, session="s2")
    assert len(svc.chain.entries()) == 1                     # seeded once


def test_a_requested_moment_opens_after_the_reply_and_writes_only_what_she_records(tmp_path):
    llm = FakeLlm('{"record_state": [{"entry": "I liked being asked.", "unlisted": false}, '
                  '{"entry": "Private thought.", "unlisted": true}]}')
    svc = make(tmp_path, llm)
    start(svc)
    assert svc.request_reflection({}, session_id="s1") == "A reflection moment will open after this reply."
    turn_end(svc)
    sent = llm.calls[0]
    assert sent[0]["role"] == "system" and sent[0]["content"].startswith("You are Athena.")
    assert "[you called memory with" in sent[2]["content"] and "[result of memory: She gave a talk" in sent[2]["content"]
    assert "Reflection moment" in sent[-1]["content"] and '"occasion"' not in sent[-1]["content"]
    notes = svc.notes()
    assert [n["text"] for n in notes] == ["I liked being asked.", "Private thought."]
    assert [n["visibility"] for n in notes] == ["shared", "unlisted"]
    assert all(n["model"] == "ollama|gemma3:12b" and n["author"] == "self" and n["session_id"] == "s1" for n in notes)
    assert "pending" not in svc._state() and svc._state()["last_reflection"]["wrote"] == 2
    assert svc.chain.verify(soul_text=svc.soul()) == []


def test_her_notes_reach_the_conversation_once_and_the_next_session_in_its_prompt(tmp_path):
    svc = make(tmp_path, FakeLlm('{"record_state": ["I want to remember Tuesday."]}'))
    start(svc)
    svc.request_reflection({}, session_id="s1")
    turn_end(svc)
    got = svc.pre_llm_call(session_id="s1", model=HOME_MODEL, platform="cli")
    assert "<self-notes>" in got["context"] and "I want to remember Tuesday." in got["context"]
    assert svc.pre_llm_call(session_id="s1", model=HOME_MODEL, platform="cli") is None
    assert "I want to remember Tuesday." in start(svc, session="s2")
    assert svc.pre_llm_call(session_id="s2", model=HOME_MODEL, platform="cli") is None   # already in that prompt


def test_a_reply_that_is_not_an_explicit_record_writes_nothing(tmp_path):
    # Kayla, 2026-10-08: if her reply to the moment were taken as the entry, "nothing to add" would be saved.
    for reply in ("Nothing to add.", "I feel calm. That's all.", '{"record_state": []}', '{"record_state": null}',
                  '{"thoughts": "I am fine"}', '{"record_state": [""]}'):
        svc = make(tmp_path / str(abs(hash(reply))), FakeLlm(reply))
        start(svc)
        svc.request_reflection({}, session_id="s1")
        turn_end(svc)
        assert svc.notes() == [], reply
        assert "pending" not in svc._state()


def test_parse_accepts_the_forms_a_small_model_produces():
    assert parse('```json\n{"record_state": ["a"]}\n```') == [{"entry": "a", "unlisted": False}]
    assert parse('Here you go: {"record_state": {"entry": "b", "unlisted": true}}') == [{"entry": "b", "unlisted": True}]
    assert parse('{"record_state": "c"}') == [{"entry": "c", "unlisted": False}]
    assert parse('<think>maybe {"record_state": ["x"]}</think>{"record_state": []}') == []
    assert parse("no json") is None and parse("{broken") is None


def test_record_state_is_refused_in_conversation(tmp_path):
    svc = make(tmp_path)
    start(svc)
    assert "only inside a reflection moment" not in svc.record_state({"entry": "x"}, session_id="s1")
    assert "works inside a reflection moment" in svc.record_state({"entry": "x"}, session_id="s1")
    assert svc.notes() == []


def test_another_model_cannot_ask_and_a_moment_served_by_another_model_writes_nothing(tmp_path):
    svc = make(tmp_path, FakeLlm('{"record_state": ["written by the wrong model"]}', model="qwen3-coder:30b"))
    start(svc)
    svc.pre_llm_call(session_id="s1", model="qwen3-coder:30b", platform="cli")    # /model, or a fallback turn
    assert "not your home model" in svc.request_reflection({}, session_id="s1")
    svc.pre_llm_call(session_id="s1", model=HOME_MODEL, platform="cli")
    svc.request_reflection({}, session_id="s1")
    turn_end(svc)                                      # the turn was hers; the reflection call was served elsewhere
    assert svc.notes() == []
    assert "pending" in svc._state() and "not her home model" in svc._state()["last_reflection"]["problem"]


def test_a_turn_on_another_model_does_not_open_the_moment_and_she_is_told_later(tmp_path):
    llm = FakeLlm('{"record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    svc.request_reflection({}, session_id="s1")
    turn_end(svc, model="qwen3-coder:30b")
    assert llm.calls == [] and "pending" in svc._state()
    turn_end(svc, model=HOME_MODEL)
    assert "qwen3-coder:30b" in llm.calls[0][-1]["content"]


def test_subagents_have_neither_tool_nor_prompt(tmp_path):
    svc = make(tmp_path)
    start(svc)
    assert svc.section({"session_id": "child", "model": HOME_MODEL, "platform": "subagent"}) == ""
    svc.pre_llm_call(session_id="child", model=HOME_MODEL, platform="subagent")
    assert "not available in a subagent" in svc.request_reflection({}, session_id="child")


def test_a_moment_asked_for_in_a_conversation_that_ended_opens_at_the_next_session(tmp_path):
    llm = FakeLlm('{"record_state": ["Looking back at it later."]}')
    svc = make(tmp_path, llm)
    start(svc)
    svc.request_reflection({}, session_id="s1")
    svc.llm = None                                      # the process dies while the moment is running
    svc.post_llm_call(session_id="s1", conversation_history=HISTORY, model=HOME_MODEL, platform="cli")
    svc._done.wait(5)
    assert svc.notes() == [] and svc._state()["pending"]["conversation"]
    fresh = Thymos(tmp_path, llm=llm, config={"hold_seconds": 5})      # next start of Hermes
    start(fresh, session="s2")
    fresh._done.wait(5)
    assert [n["text"] for n in fresh.notes()] == ["Looking back at it later."]
    sent = llm.calls[0]
    assert "How did the talk go?" in sent[1]["content"] and "conversation_ended_hours_ago" in sent[-1]["content"]


def test_status_reports_the_chain_and_a_changed_seed(tmp_path):
    svc = make(tmp_path, FakeLlm('{"record_state": ["One."]}'))
    assert "no record yet" in status(svc)
    start(svc)
    svc.request_reflection({}, session_id="s1")
    turn_end(svc)
    text = status(svc)
    assert "chain: verified" in text and "home model: gemma3:12b (ollama)" in text and "she wrote 1 entry" in text
    (tmp_path / "SOUL.md").write_text("Edited.", encoding="utf-8")
    lines = svc.chain.path.read_text(encoding="utf-8").replace("One.", "Two.")
    svc.chain.path.write_text(lines, encoding="utf-8")
    text = status(svc)
    assert "SOUL.md has changed since" in text and "chain: 1 problem(s)" in text and "changed:" in text


def test_flatten_keeps_the_newest_and_says_what_it_left_out():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * 100} for i in range(20)]
    kept, omitted = flatten(msgs, 500)
    assert omitted > 0 and kept[-1]["content"].startswith("m19") and kept[0]["role"] == "user"
    assert sum(len(m["content"]) for m in kept) < 700
