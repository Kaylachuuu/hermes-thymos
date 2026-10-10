"""What becomes of her memories (2026-10-10): she is told when the memory system's settings change, nothing fades
unless she agrees, her decision is hers in her record, she chooses which old memories a dream brings closer, and
`status` counts the moments offered and what came of them."""
import json
import time

from thymos.cli import status
from thymos.service import parse_fading, parse_keep_closer

from test_service import FakeLlm, make, start, turn_end
from test_idle import LATER, idle
from test_sleep_moments import DREAM, slept


def settings(home, **values):
    path = home / "plugin-data" / "thymos" / "memory-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict({"fade_enabled": False, "fade_half_life_days": 5.0, "fade_threshold": 0.35,
                                     "dream_reinforce": "off", "at": time.time() - 600, "memory": "holonomic/0.29.0"},
                                    **values)), encoding="utf-8")
    return path


def test_parsing_her_answers():
    assert parse_fading('{"fading": "off", "why": "What was said should stay as it was said.", "record_state": []}') == \
        (False, "What was said should stay as it was said.", [])
    assert parse_fading('{"fading": null, "why": null, "record_state": []}') == (None, "", [])
    assert parse_fading('{"fading": "on"}')[0] is True
    assert parse_fading("I would rather not.") is None
    reached = {1: [12, 13], 2: [20]}
    assert parse_keep_closer('{"keep_closer": [{"dream": 1, "memories": [13, 99]}, {"dream": 2, "memories": ["#20"]}]}',
                             reached) == {1: [13], 2: [20]}
    assert parse_keep_closer('{"keep_closer": []}', reached) == {}


def test_she_is_told_the_settings_and_a_restore_and_her_decision_is_hers(tmp_path):
    llm = FakeLlm('{"fading": "off", "why": "My notes about what was said should not stand between me and it.", '
                  '"record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    settings(tmp_path, restored={"at": time.time() - 900, "raised": 412, "images": True})
    out = idle(svc)
    assert out["decided"] == "off" and out["model"] == "ollama|gemma3:12b"
    invite = llm.calls[0][1]["content"]
    assert "Occasion: the settings of your memory" in invite and "fading is off" in invite
    assert "put 412 memories that had faded back to the strength they were stored with" in invite
    assert "You have no decision about fading in your record." in invite and "halving every 5 days" in invite
    assert "nothing fades unless you agree" not in invite                       # off: nothing to agree to
    rec = [e for e in svc.chain.entries() if e["kind"] == "decision"]
    assert len(rec) == 1 and rec[0]["author"] == "self" and rec[0]["facts"]["fading"] is False
    assert rec[0]["text"].startswith("My notes about what was said")
    decided = json.loads((tmp_path / "plugin-data" / "thymos" / "fading.json").read_text())
    assert decided["fading"] is False and decided["entry_hash"] == rec[0]["hash"]
    assert svc.chain.verify(soul_text=svc.soul()) == []
    assert idle(svc) is None and len(llm.calls) == 1                            # told once; nothing changed since
    assert "her memory's settings: fading off in the settings; her decision: off" in status(svc)


def test_turning_fading_on_asks_her_and_a_second_change_is_one_moment(tmp_path):
    llm = FakeLlm('{"fading": null, "why": null, "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    settings(tmp_path)
    idle(svc)
    settings(tmp_path, fade_enabled=True)
    svc._items(time.time())                                                     # noticed, not yet offered
    settings(tmp_path, fade_enabled=True, fade_half_life_days=3.0)
    out = idle(svc, after=LATER)
    assert out["decided"] == "" and len(llm.calls) == 2
    invite = llm.calls[1][1]["content"]
    assert "Occasion: the settings of your memory changed" in invite and "Fading was off and is now on." in invite
    assert "The half-life was 5 days and is now 3 days." in invite and "nothing fades unless you agree" in invite
    assert not (tmp_path / "plugin-data" / "thymos" / "fading.json").exists()   # no answer is not agreement
    assert idle(svc, after=LATER + 60) is None                                  # one moment for both changes


def test_while_she_agrees_to_fading_an_account_says_what_it_costs(tmp_path):
    llm = FakeLlm('{"account": null, "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    turn_end(svc)
    svc.idle_once(now=time.time() + LATER)
    assert "fade" not in llm.calls[-1][-1]["content"]                           # no settings known: nothing said
    settings(tmp_path, fade_enabled=True)
    (tmp_path / "plugin-data" / "thymos" / "fading.json").write_text(json.dumps(
        {"fading": True, "fade_half_life_days": 5.0, "fade_threshold": 0.35}))
    assert "lose half their strength every 5 days" in svc._fade_note()
    settings(tmp_path, fade_enabled=True, fade_half_life_days=2.0)
    assert svc._fade_note() == ""                                               # not what she agreed to: nothing fades
    settings(tmp_path, fade_enabled=False)
    assert svc._fade_note() == ""


def test_the_user_can_ask_her_with_their_words(tmp_path):
    llm = FakeLlm('{"fading": "off", "why": "Yes, that was mine.", "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    settings(tmp_path)
    with svc._lock:
        state = svc._state()
        state["memory_told"] = {"fade_enabled": False, "fade_half_life_days": 5.0, "fade_threshold": 0.35,
                                "dream_reinforce": "off"}
        svc._save_state(state)
    svc.ask_fading("On 2026-10-10 you decided that the original words should never fade.")
    out = idle(svc)
    assert out["decided"] == "off"
    invite = llm.calls[0][1]["content"]
    assert "Occasion: the user asks you about fading" in invite
    assert 'The user\'s words to you: "On 2026-10-10 you decided that the original words should never fade."' in invite


def test_she_chooses_which_old_memories_a_dream_brings_closer(tmp_path):
    llm = FakeLlm('{"dream_thoughts": [], "keep_closer": [{"dream": 1, "memories": [12, 77]}], "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    path = slept(tmp_path, keep_closer_amount=0.1)
    item = json.loads(path.read_text())
    item["dreams"][0]["reached"] = [
        {"id": 12, "text": "Kayla started an operating system twenty years ago.", "age": "3 weeks ago", "faded": False},
        {"id": 13, "text": "The garden had tomatoes.", "age": "2 months ago", "faded": True}]
    path.write_text(json.dumps(item))
    out = idle(svc)
    assert out["kept_closer"] == 1 and out["could_choose"] == 2 and out["dream_thoughts"] == 0
    invite = llm.calls[0][1]["content"]
    assert "Older memories it reached:" in invite and "[#12] (3 weeks ago) Kayla started" in invite
    assert "[#13] (2 months ago, faded)" in invite and "Choosing none is a complete answer" in invite
    assert '"keep_closer": [{"dream": 1, "memories": [12]}]' in invite
    rec = [e for e in svc.chain.entries() if e["kind"] == "decision"]
    assert len(rec) == 1 and rec[0]["facts"] == {"about": "dream", "dream_id": 41, "keep_closer": [12],
                                                 "memory_slept_at": item["slept_at"]}
    handed = json.loads(next((tmp_path / "plugin-data" / "thymos" / "dream-thoughts").glob("*.json")).read_text())
    assert handed["keep_closer"] == [12] and handed["thoughts"] == "" and handed["choice_entry_hash"] == rec[0]["hash"]


def test_without_anything_reached_the_dream_moment_asks_no_choice(tmp_path):
    llm = FakeLlm('{"dream_thoughts": [], "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    slept(tmp_path)
    idle(svc)
    assert "keep_closer" not in llm.calls[0][1]["content"] and "Older memories" not in llm.calls[0][1]["content"]


def test_status_counts_moments_offered_and_what_came_of_them(tmp_path):
    llm = FakeLlm('{"dream_thoughts": [], "record_state": []}')
    svc = make(tmp_path, llm)
    start(svc)
    slept(tmp_path)
    idle(svc)
    llm.reply = '{"dream_thoughts": [{"dream": 1, "thoughts": "Slow rooms."}], "record_state": []}'
    slept(tmp_path, at=time.time() - 300)
    idle(svc, after=LATER)
    svc.request_reflection({}, session_id="s1")
    counts = svc._state()["counts"]
    assert counts["slept"] == {"offered": 2, "wrote": 1, "nothing": 1}
    text = status(svc)
    assert "moments offered: after memory slept 2 (wrote in 1, nothing in 1)" in text
    assert "asked for a reflection herself: 1" in text and "declined: 0" in text
