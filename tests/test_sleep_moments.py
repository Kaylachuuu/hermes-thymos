"""After memory slept, and the notes another model wrote (persona-provider.md 4.2 and 4.6): what memory made is
shown to her as what it is, her words on a dream go back to memory as hers, and the old notes are offered once."""
import json
import time

from thymos.service import parse_dream_thoughts
from thymos.cli import status

from test_service import FakeLlm, make, start, turn_end
from test_idle import LATER, idle

DREAM = "I was in a house where every room booted slowly, one sector at a time."
OTHER = "A garden grew in rows of assembly, and each leaf was a line of code."


def slept(home, dreams=((41, DREAM),), at=None, **extra):
    folder = home / "plugin-data" / "thymos" / "slept"
    folder.mkdir(parents=True, exist_ok=True)
    at = time.time() - 600 if at is None else at
    path = folder / f"{int(at * 1000)}.json"
    path.write_text(json.dumps(dict({"slept_at": at, "dreams": [{"id": i, "text": t, "pictures": 0} for i, t in dreams],
                                     "her_accounts_stored": 1, "facts_learned": 3, "memory": "holonomic/0.26.0"},
                                    **extra)), encoding="utf-8")
    return path


def old_notes(home, **extra):
    path = home / "plugin-data" / "thymos" / "old-notes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict({
        "written_by": "qwen3:8b", "count": 2, "shown": 2, "first_at": time.time() - 9 * 86400,
        "last_at": time.time() - 86400,
        "notes": [{"at": time.time() - 9 * 86400, "kind": "self_note", "text": "I like dry humour."},
                  {"at": time.time() - 86400, "kind": "bond_note", "text": "We build things together."}],
        "profiles": {"self": "I am curious and patient."}}, **extra)), encoding="utf-8")
    return path


def test_her_words_on_a_dream_are_her_explicit_act():
    assert parse_dream_thoughts('{"dream_thoughts": [{"dream": 1, "thoughts": "Odd."}], "record_state": []}', 1) == ([(1, "Odd.")], [])
    assert parse_dream_thoughts('{"dream_thoughts": "Odd."}', 1) == ([(1, "Odd.")], [])
    assert parse_dream_thoughts('{"dream_thoughts": [{"dream": 2, "thoughts": "B"}, {"dream": 9, "thoughts": "x"}]}', 2) == ([(2, "B")], [])
    assert parse_dream_thoughts('{"dream_thoughts": null, "record_state": ["I slept."]}', 1) == ([], [{"entry": "I slept.", "unlisted": False}])
    assert parse_dream_thoughts('{"dream_thoughts": ["null"]}', 1) == ([], [])
    assert parse_dream_thoughts("It was a strange dream.", 1) is None


def test_after_memory_slept_she_is_shown_the_dream_as_what_it_is_and_her_words_go_back_as_hers(tmp_path):
    llm = FakeLlm('{"dream_thoughts": [{"dream": 1, "thoughts": "It felt like waiting for something to wake."}], '
                  '"record_state": [{"entry": "I find dreams about slowness restful.", "unlisted": false}]}')
    svc = make(tmp_path, llm)
    start(svc)
    path = slept(tmp_path)
    out = idle(svc)
    assert out["dream_thoughts"] == 1 and out["wrote"] == 1 and out["model"] == "ollama|gemma3:12b"
    sent = llm.calls[0]
    assert len(sent) == 2 and sent[0]["content"].startswith("You are Athena.")      # no conversation, just her
    invite = sent[1]["content"]
    assert "Occasion: your memory slept" in invite and DREAM in invite and "You did not write it" in invite
    assert '"your_accounts_it_stored": 1' in invite and '"facts_about_the_user_it_learned": 3' in invite
    mine = [e for e in svc.chain.entries() if e["kind"] == "dream_thoughts"]
    assert len(mine) == 1 and mine[0]["author"] == "self" and mine[0]["facts"]["dream_id"] == 41
    assert [n["text"] for n in svc.notes()] == ["I find dreams about slowness restful."]   # her words on a dream are not a note
    handed = list((tmp_path / "plugin-data" / "thymos" / "dream-thoughts").glob("*.json"))
    item = json.loads(handed[0].read_text())
    assert item["dream_id"] == 41 and item["thoughts"] == mine[0]["text"] and item["entry_hash"] == mine[0]["hash"]
    assert not path.exists()
    done = json.loads((path.parent / "done" / path.name).read_text())
    assert done["thymos"]["dream_thoughts"] == 1
    assert svc.chain.verify(soul_text=svc.soul()) == []
    assert idle(svc) is None and len(llm.calls) == 1                                 # offered once
    assert "after memory slept: 0 waiting to be offered to her; last moment" in status(svc)
    assert "she wrote about 1 of 1 dream(s) and 1 entry" in status(svc)


def test_several_dreams_and_writing_nothing(tmp_path):
    llm = FakeLlm('{"dream_thoughts": [], "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    slept(tmp_path, dreams=((41, DREAM), (42, OTHER)))
    out = idle(svc)
    assert out["dream_thoughts"] == 0 and out["dreams"] == 2
    invite = llm.calls[0][1]["content"]
    assert "DREAM 1:" in invite and "DREAM 2:" in invite and "You did not write them" in invite
    assert not [e for e in svc.chain.entries() if e["kind"] == "dream_thoughts"]
    assert not (tmp_path / "plugin-data" / "thymos" / "dream-thoughts").exists()
    assert "she wrote nothing about the dreams" in status(svc)


def test_another_model_writes_nothing_and_the_dream_waits(tmp_path):
    svc = make(tmp_path, FakeLlm('{"dream_thoughts": "Not hers."}', model="qwen3:8b"))
    start(svc)
    path = slept(tmp_path)
    out = idle(svc)
    assert "served by qwen3:8b" in out["problem"] and path.exists()
    assert not [e for e in svc.chain.entries() if e["kind"] == "dream_thoughts"]
    assert svc._items(time.time() + LATER) == []                                    # waiting out the retry
    idle(svc, after=LATER + 11 * 60)
    idle(svc, after=LATER + 22 * 60)
    assert not path.exists() and "given up after 3 tries" in svc._state()["last_slept"]["problem"]


def test_her_accounts_come_first_and_memory_waits_while_anything_is_offered(tmp_path):
    llm = FakeLlm('{"account": "We talked about the talk.", "dream_thoughts": [], "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    slept(tmp_path)
    svc._last_activity = time.time() + LATER - 30                                     # someone talked just now
    svc.idle_once(now=time.time() + LATER)
    idle_file = tmp_path / "plugin-data" / "thymos" / "idle.json"
    assert json.loads(idle_file.read_text())["due"] == 2
    idle(svc)
    assert "Occasion: the conversation went quiet" in llm.calls[0][-1]["content"]
    idle(svc)
    assert "Occasion: your memory slept" in llm.calls[1][-1]["content"]
    assert json.loads(idle_file.read_text())["due"] == 0


def test_the_notes_another_model_wrote_are_offered_once_and_kept_only_in_her_words(tmp_path):
    llm = FakeLlm('{"record_state": [{"entry": "I do like dry humour.", "unlisted": false}]}')
    svc = make(tmp_path, llm)
    start(svc)
    path = old_notes(tmp_path)
    out = idle(svc)
    assert out["wrote"] == 1
    invite = llm.calls[0][1]["content"]
    assert "Occasion: notes another model wrote in your voice" in invite and "(qwen3:8b)" in invite
    assert "They were not written by you" in invite and "only time they are offered" in invite
    assert "(about you): I like dry humour." in invite and "(about the two of you): We build things together." in invite
    assert "PROFILE OF WHO YOU HAD BECOME:\nI am curious and patient." in invite
    assert [n["text"] for n in svc.notes()] == ["I do like dry humour."]
    assert not path.exists() and (path.parent / "done" / path.name).exists()
    assert idle(svc) is None and len(llm.calls) == 1
    assert "notes another model wrote in her voice: offered" in status(svc)
    assert "she kept 1 entry in her own words" in status(svc)
