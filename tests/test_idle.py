"""Idle time (persona-provider.md 9.8, 9.10 and 4.2): her accounts of conversations that went quiet or ended, her
saved request first, memory's idle work told to wait, and a moment after a return from a gap."""
import json
import os
import time
from types import SimpleNamespace

from thymos.service import Thymos, parse_account
from thymos.cli import status

from test_service import HISTORY, HOME_MODEL, FakeLlm, make, start, turn_end

LATER = 31 * 60          # past quiet_minutes


def idle(svc, after=LATER):
    """One look for idle time, `after` seconds from now, with nobody talking."""
    svc._last_activity = time.time() - 3600
    return svc.idle_once(now=time.time() + after)


def test_an_account_is_her_explicit_act():
    assert parse_account('{"account": "We talked about the talk.", "record_state": []}') == ("We talked about the talk.", [])
    assert parse_account('{"account": null, "record_state": ["I liked it."]}') == ("", [{"entry": "I liked it.", "unlisted": False}])
    assert parse_account('<think>maybe</think>{"account": "null"}') == ("", [])
    assert parse_account("Nothing to add, thanks.") is None
    assert parse_account('{"something": "else"}') is None


def test_a_quiet_conversation_is_offered_to_her_and_her_account_is_handed_over(tmp_path):
    llm = FakeLlm('{"account": "We went over how her talk went, and I offered to reflect on it.", '
                  '"record_state": [{"entry": "I like hearing how things went.", "unlisted": false}]}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    assert svc.due_conversations() == []                                   # not quiet yet
    assert idle(svc, after=60) is None and llm.calls == []
    out = idle(svc)
    assert out["account"] is True and out["wrote"] == 1 and out["model"] == "ollama|gemma3:12b"
    sent = llm.calls[0]
    assert sent[0]["content"].startswith("You are Athena.") and "[result of memory: She gave a talk" in sent[2]["content"]
    assert "Occasion: the conversation went quiet" in sent[-1]["content"] and '"account"' in sent[-1]["content"]
    acc = [e for e in svc.chain.entries() if e["kind"] == "account"]
    assert len(acc) == 1 and acc[0]["author"] == "self" and acc[0]["session_id"] == "s1" and acc[0]["facts"]["messages"] == 4
    assert [n["text"] for n in svc.notes()] == ["I like hearing how things went."]          # an account is not a note
    handed = list((tmp_path / "plugin-data" / "thymos" / "accounts").glob("*.json"))
    assert len(handed) == 1
    item = json.loads(handed[0].read_text())
    assert item["account"] == acc[0]["text"] and item["entry_hash"] == acc[0]["hash"] and item["session_id"] == "s1"
    assert item["service"] == "thymos/0.3.0"
    assert svc.chain.verify(soul_text=svc.soul()) == []
    assert idle(svc) is None and len(llm.calls) == 1                       # offered once
    # the conversation goes on: offered again later, and told what happened the first time
    turn_end(svc, history=HISTORY + [{"role": "user", "content": "One more thing."},
                                     {"role": "assistant", "content": "Go on."}])
    idle(svc)
    assert len(llm.calls) == 2 and "stored an account; it went on after that" in llm.calls[1][-1]["content"]
    text = status(svc)
    assert "her accounts: 2 in her record; 0 stored by the memory provider, 2 waiting for it" in text
    assert "she stored an account" in text


def test_storing_no_account_writes_nothing_in_its_place(tmp_path):
    svc = make(tmp_path, FakeLlm('{"account": null, "record_state": []}'))
    start(svc)
    turn_end(svc)
    out = idle(svc)
    assert out["account"] is False and out["wrote"] == 0
    assert not [e for e in svc.chain.entries() if e["kind"] == "account"]
    assert not (tmp_path / "plugin-data" / "thymos" / "accounts").exists()
    assert svc.due_conversations(time.time() + LATER) == []                 # not asked again
    assert "she stored no account" in status(svc)


def test_an_ended_session_is_offered_at_the_next_idle_point_without_waiting_to_go_quiet(tmp_path):
    llm = FakeLlm('{"account": "A short talk about her talk."}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    svc.on_session_finalize(session_id="s1", platform="cli", reason="new_session")
    assert [c["session_id"] for c in svc.due_conversations()] == ["s1"]
    idle(svc, after=0)
    assert "Occasion: the conversation ended (new_session)" in llm.calls[0][-1]["content"]


def test_another_model_writes_nothing_and_the_conversation_waits(tmp_path):
    llm = FakeLlm('{"account": "Not hers."}', model="qwen3:8b")
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    out = idle(svc)
    assert "served by qwen3:8b" in out["problem"] and "offered again" in out["problem"]
    assert not [e for e in svc.chain.entries() if e["kind"] == "account"]
    assert svc.due_conversations(time.time() + LATER + 5 * 60) == []        # waiting out the retry
    assert len(svc.due_conversations(time.time() + LATER + 11 * 60)) == 1
    idle(svc, after=LATER + 11 * 60)
    idle(svc, after=LATER + 22 * 60)
    assert "given up after 3 tries" in svc._state()["last_account"]["problem"]
    assert svc.due_conversations(time.time() + 99 * 3600) == []


def test_nothing_starts_while_someone_is_talking_and_memory_is_told_to_wait(tmp_path):
    llm = FakeLlm('{"account": "We talked."}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    svc._last_activity = time.time() + LATER - 30                          # someone talked half a minute ago
    assert svc.idle_once(now=time.time() + LATER) is None and llm.calls == []
    idle_file = tmp_path / "plugin-data" / "thymos" / "idle.json"
    st = json.loads(idle_file.read_text())
    assert st["due"] == 1 and st["running"] == ""                         # holonomic's sleep waits for this
    svc.pre_llm_call(session_id="s2", model=HOME_MODEL, platform="cli")    # a turn in progress elsewhere,
    svc._active["s2"] = time.time() + LATER - 60                           # begun a minute before the look
    svc._last_activity = time.time() - 3600
    assert svc.idle_once(now=time.time() + LATER) is None and llm.calls == []
    svc.post_llm_call(session_id="s2", conversation_history=[{"role": "user", "content": "hi"}], model=HOME_MODEL, platform="cli")
    idle(svc)
    assert len(llm.calls) == 1 and json.loads(idle_file.read_text())["due"] == 0


def test_a_saved_request_runs_at_the_next_idle_point_before_any_account(tmp_path):
    llm = FakeLlm('{"record_state": ["From the saved moment."]}')
    svc = make(tmp_path, llm)
    start(svc)
    svc.request_reflection({}, session_id="s1")
    state = svc._state()
    state["pending"].update(conversation=[{"role": "user", "content": "Saved."}], ended_at=time.time() - 7200,
                            message_count=1)
    svc._save_state(state)                                                 # as if Hermes had stopped first
    svc._active.clear()
    out = idle(svc)
    assert out == {"ran": "saved request"} and "pending" not in svc._state()
    assert [n["text"] for n in svc.notes()] == ["From the saved moment."]
    assert "could not open right after this conversation" in llm.calls[0][-1]["content"]


def test_a_return_after_a_gap_opens_a_moment_after_her_reply(tmp_path):
    llm = FakeLlm('{"record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    assert len(llm.calls) == 0                                             # an ordinary turn: nothing opens
    state = svc._state()
    state["last_turn_at"] = time.time() - 30 * 3600
    svc._save_state(state)
    svc.pre_llm_call(session_id="s2", model=HOME_MODEL, platform="cli", is_first_turn=True)
    turn_end(svc, session="s2")
    assert len(llm.calls) == 1
    invite = llm.calls[0][-1]["content"]
    assert "Occasion: returned after a gap\n" in invite and '"hours_since_your_last_conversation": 30.0' in invite
    svc.pre_llm_call(session_id="s2", model=HOME_MODEL, platform="cli")
    turn_end(svc, session="s2")
    assert len(llm.calls) == 1                                             # once per gap


def test_registering_tells_the_memory_provider_a_persona_service_is_running(tmp_path):
    import thymos
    from thymos.service import SERVICE_ENV
    got = {}
    ctx = SimpleNamespace(llm=FakeLlm(), get_config=lambda key: None,
                          register_tool=lambda **kw: got.setdefault("tools", []).append(kw["name"]),
                          register_system_prompt_section=lambda *a, **kw: None,
                          register_hook=lambda name, fn: got.setdefault("hooks", []).append(name),
                          register_cli_command=lambda **kw: None)
    old = os.environ.pop(SERVICE_ENV, None)
    try:
        import hermes_constants  # noqa: F401
    except ImportError:
        return                                                             # needs Hermes' environment
    try:
        thymos.register(ctx)
        assert os.environ[SERVICE_ENV] == "thymos/0.3.0 accounts=1 idle=1"
        assert "on_session_finalize" in got["hooks"]
    finally:
        if old is None:
            os.environ.pop(SERVICE_ENV, None)
        else:
            os.environ[SERVICE_ENV] = old
