"""Before compression (persona-provider.md 4.2, `PRE_COMPRESS`): the memory provider leaves the messages as they
were in compressing/, and at the next idle point she may write an account of them and entries.  Her account goes
to the memory provider like any other, and the conversation's saved copy is not offered to her a second time."""
import json
import time

from thymos.service import flatten
from thymos.cli import status

from test_service import HISTORY, FakeLlm, make, start, turn_end
from test_idle import idle


def compressing(svc, session="s1", messages=HISTORY, at=None):
    folder = svc.data / "compressing"
    folder.mkdir(parents=True, exist_ok=True)
    at = time.time() if at is None else at
    path = folder / f"{int(at * 1000)}-{session}.json"
    path.write_text(json.dumps({"session_id": session, "compressed_at": at, "message_count": len(messages),
                                "messages": messages, "memory": "holonomic/0.28.0"}), encoding="utf-8")
    return path


def test_flatten_can_keep_the_oldest():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * 100} for i in range(20)]
    kept, later = flatten(msgs, 500, oldest=True)
    assert later > 0 and kept[0]["content"].startswith("m0") and kept[-1]["content"] == "[later conversation not shown]"
    assert len(kept) - 1 + later == 20


def test_she_may_write_about_a_compressed_conversation_and_it_is_not_offered_twice(tmp_path):
    llm = FakeLlm('{"account": "We went over her talk before the room filled up.", '
                  '"record_state": [{"entry": "I want to keep the talk.", "unlisted": false}]}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    path = compressing(svc)
    assert svc.due_conversations(now=time.time() + 3600) == []     # its compression is offered first
    assert "before compression: 1 waiting to be offered to her" in status(svc)
    out = idle(svc, after=150)                                      # idle, but the conversation is not yet quiet
    assert out["account"] is True and out["wrote"] == 1 and out["session_id"] == "s1"
    sent = llm.calls[-1]
    assert sent[1] == {"role": "user", "content": "How did the talk go?"}
    invite = sent[-1]["content"]
    assert "Occasion: a conversation you are in was compressed" in invite
    assert "The summary was written by the compression step, not by you" in invite
    assert '"messages_before_compression": 4' in invite and '"account"' in invite
    acc = [e for e in svc.chain.entries() if e["kind"] == "account"]
    assert len(acc) == 1 and acc[0]["session_id"] == "s1" and "compressed_at" in acc[0]["facts"]
    [handed] = list((svc.data / "accounts").glob("*.json"))
    assert json.loads(handed.read_text())["conversation_ended_at"] == acc[0]["facts"]["compressed_at"]
    assert not path.exists() and list((svc.data / "compressing" / "done").glob("*.json"))
    assert svc.chain.verify(soul_text=svc.soul()) == []
    # The saved copy of the same conversation is not offered again when it goes quiet.
    assert idle(svc) is None and len(llm.calls) == 1
    assert "before compression: 0 waiting to be offered to her; last moment" in status(svc)
    assert "she stored an account and 1 entry" in status(svc)


def test_a_compressed_conversation_waits_for_her_home_model(tmp_path):
    llm = FakeLlm('{"account": null, "record_state": []}', model="qwen3:8b")
    svc = make(tmp_path, llm)
    start(svc)
    svc._active.clear()
    path = compressing(svc)
    out = idle(svc, after=150)
    assert "not her home model" in out["problem"] and path.exists() and svc.notes() == []
    llm.model = "gemma3:12b"
    out = idle(svc, after=3600)
    assert out["account"] is False and out["wrote"] == 0 and not path.exists()
    assert [e for e in svc.chain.entries() if e["kind"] == "account"] == []      # she stored none: there is none


def test_compressed_in_place_the_conversation_is_offered_again_as_it_goes_on(tmp_path):
    svc = make(tmp_path, FakeLlm('{"account": null, "record_state": []}'))
    start(svc)
    longer = HISTORY + [{"role": "user", "content": "One more thing."}, {"role": "assistant", "content": "Go on."}]
    turn_end(svc, history=longer)
    compressing(svc, messages=longer)
    idle(svc, after=150)
    conv = json.loads(svc._conversation_path("s1").read_text())
    assert conv["offered_count"] == 6
    # The same id, now starting from the summary: shorter than before, and offered again once it is quiet.
    turn_end(svc, history=[{"role": "user", "content": "[summary of earlier conversation]"},
                           {"role": "assistant", "content": "Yes."}, {"role": "user", "content": "And then?"},
                           {"role": "assistant", "content": "Then the garden."}])
    assert [c["session_id"] for c in svc.due_conversations(now=time.time() + 3600)] == ["s1"]


def test_a_deleted_conversations_verbatim_copies_go_after_the_grace_period(tmp_path):
    svc = make(tmp_path, FakeLlm('{"account": "We talked about her talk.", "record_state": []}'))
    start(svc)
    turn_end(svc)
    compressing(svc)
    gone = {"s1"}
    svc.session_exists = lambda sid: sid not in gone
    now = time.time()
    assert svc.sweep_deleted(now) == [] and svc._conversation_path("s1").exists()       # first seen deleted
    assert svc.sweep_deleted(now + 29 * 86400) == []                                    # still in the grace period
    assert svc.sweep_deleted(now + 31 * 86400) == ["s1"]
    assert not svc._conversation_path("s1").exists() and not list((svc.data / "compressing").glob("*.json"))
    # A session that comes back (restored) before the period ends is not removed.
    start(svc, session="s2")
    turn_end(svc, session="s2")
    gone = {"s2"}
    svc.sweep_deleted(now)
    gone = set()
    svc.sweep_deleted(now + 1)
    gone = {"s2"}
    assert svc.sweep_deleted(now + 31 * 86400) == [] and svc._conversation_path("s2").exists()
