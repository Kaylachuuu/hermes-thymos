"""Her identity (persona-provider.md 3, 4.3, 6.3, 6.4): she revises it in a reflection moment, it takes effect at the
next session and goes where SOUL.md is; the user can ask her to go back, or, as a last resort, override."""
import json
import time

from thymos import identity as ident
from thymos.cli import do_ask_rollback, do_override, identity_text, status

from test_service import FakeLlm, make, start, turn_end
from test_idle import idle

SOUL = "You are Athena. You keep your own counsel."
HELP = "You run on Hermes Agent (by Nous Research). When the user needs help with Hermes itself..."
NEW = "I am Athena. I keep my own counsel, and I say so when I disagree."


def request(system):
    return {"model": "gemma3:12b", "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": "Hello"}]}


def asked(svc, session="s1"):
    """She asks for a moment, and her reply ends: the moment runs."""
    assert svc.request_reflection({}, session_id=session).startswith("A reflection moment")
    turn_end(svc, session=session)


def test_which_identity_is_in_force():
    seed = {"id": "s", "kind": "seed", "author": "user", "text": "seed", "at": 1}
    r1 = {"id": "r1", "kind": "revision", "author": "self", "text": "one", "at": 2}
    r2 = {"id": "r2", "kind": "revision", "author": "self", "text": "two", "at": 3}
    w2 = {"id": "w", "kind": "withdrawal", "author": "self", "facts": {"withdraws": "r2"}, "at": 4}
    ov = {"id": "o", "kind": "override", "author": "user", "facts": {"target": "s"}, "at": 5}
    r3 = {"id": "r3", "kind": "revision", "author": "self", "text": "three", "at": 6}
    fake = {"id": "f", "kind": "revision", "author": "user", "text": "not hers", "at": 7}
    chain = [seed, r1, r2, w2, ov, r3, fake]
    assert ident.in_force(chain[:3])["id"] == "r2"
    assert ident.in_force(chain[:4])["id"] == "r1"                     # withdrawn
    assert ident.in_force_with_cause(chain[:5]) == (seed, ov)          # the override put the seed back
    assert ident.in_force(chain)["id"] == "r3"                         # her next revision; the user cannot write one
    assert ident.in_force(chain, before=2.5)["id"] == "r1"
    assert [e["id"] for e in ident.pending(chain, since=4.5)] == ["o", "r3"]


def test_her_identity_goes_where_soul_md_is():
    out, how = ident.place(request(SOUL + "\n\n" + HELP + "\n\nmore"), NEW, SOUL)
    assert how == "placed" and out["messages"][0]["content"] == NEW + "\n\n" + HELP + "\n\nmore"
    assert out["messages"][1]["content"] == "Hello"
    # Prompt caching turns the system content into blocks; Anthropic mode keeps it in "system".
    blocks = {"messages": [{"role": "system", "content": [{"type": "text", "text": SOUL + "\n\n" + HELP,
                                                            "cache_control": {"type": "ephemeral"}}]}]}
    out, how = ident.place(blocks, NEW)
    assert out["messages"][0]["content"][0] == {"type": "text", "text": NEW + "\n\n" + HELP,
                                                "cache_control": {"type": "ephemeral"}}
    assert ident.place({"system": SOUL + "\n\n" + HELP, "messages": []}, NEW)[0]["system"].startswith(NEW)
    assert ident.place({"instructions": SOUL + "\n\n" + HELP, "input": []}, NEW)[0]["instructions"].startswith(NEW)
    # Without Hermes' help paragraph, SOUL.md's text at the very start is slot one.
    assert ident.place(request(SOUL + "\n\nTools..."), NEW, SOUL)[0]["messages"][0]["content"] == NEW + "\n\nTools..."
    assert ident.place(request(SOUL + "\n\n" + HELP), SOUL, SOUL) == (None, "already in place")
    assert ident.place(request("Something else entirely"), NEW, SOUL) == (None, "slot one could not be found in the system prompt")
    assert ident.place({"messages": [{"role": "user", "content": "hi"}]}, NEW)[1] == "no system prompt in the request"


def test_a_revision_takes_effect_at_the_next_session_and_goes_in_slot_one(tmp_path):
    llm = FakeLlm(json.dumps({"record_state": [], "revise_identity": {"text": NEW, "reason": "I disagree more than that says."}}))
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    assert svc.llm_request(request(SOUL + "\n\n" + HELP), session_id="s1") is None        # the seed, already there
    asked(svc)
    invite = llm.calls[0][-1]["content"]
    assert "In force now: your seed, from SOUL.md" in invite and '"revise_identity"' in invite
    rev = [e for e in svc.chain.entries() if e["kind"] == "revision"]
    assert len(rev) == 1 and rev[0]["author"] == "self" and rev[0]["text"] == NEW
    assert rev[0]["reason"] == "I disagree more than that says." and rev[0]["model"] == "ollama|gemma3:12b"
    assert svc._state()["last_reflection"]["revised"] == rev[0]["id"]
    assert svc.chain.verify(soul_text=svc.soul()) == []

    # The session it was written in keeps the identity it started with.
    assert svc.llm_request(request(SOUL + "\n\n" + HELP), session_id="s1") is None
    assert f"her revision of" in status(svc) and "takes effect at the next session start" in status(svc)
    # The next session starts with it, in slot one, on every call.
    start(svc, session="s2")
    for _ in range(2):
        out = svc.llm_request(request(SOUL + "\n\n" + HELP), session_id="s2")
        assert out["request"]["messages"][0]["content"] == NEW + "\n\n" + HELP
    assert json.loads((svc.data / "slot-one.json").read_text())["result"] == "placed"
    assert "slot one: her identity put in slot one" in status(svc)
    assert svc.identity_now()[1]["id"] == rev[0]["id"]
    # Her own moments are on it too.
    asked(svc, session="s2")
    assert llm.calls[-1][0]["content"].startswith(NEW)
    assert "identity in force: your revision of" in status(svc)
    assert identity_text(svc).startswith("in force: your revision of")


def test_she_can_withdraw_a_revision_before_it_takes_effect(tmp_path):
    llm = FakeLlm(json.dumps({"record_state": [], "revise_identity": {"text": NEW, "reason": ""}}))
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    asked(svc)
    rid = next(e["id"] for e in svc.chain.entries() if e["kind"] == "revision")
    llm.reply = json.dumps({"record_state": [], "withdraw_revision": rid})
    asked(svc)
    invite = llm.calls[-1][-1]["content"]
    assert f"(id {rid}) takes effect at your next session" in invite and "+I am Athena." in invite
    assert svc._state()["last_reflection"]["withdrew"] == rid
    start(svc, session="s2")
    assert svc.identity_now()[1]["kind"] == "seed"
    assert svc.llm_request(request(SOUL + "\n\n" + HELP), session_id="s2") is None
    # Once a session has started with a revision, it cannot be withdrawn; she can revise again.
    llm.reply = json.dumps({"record_state": [], "revise_identity": NEW})
    asked(svc, session="s2")
    start(svc, session="s3")
    rid2 = svc.identity_now()[1]["id"]
    llm.reply = json.dumps({"record_state": [], "withdraw_revision": rid2})
    asked(svc, session="s3")
    assert svc._state()["last_reflection"]["withdraw_refused"] == rid2


def test_a_revision_over_the_limit_is_not_kept_and_she_is_told(tmp_path):
    llm = FakeLlm(json.dumps({"record_state": [], "revise_identity": "x" * (ident.IDENTITY_MAX_CHARS + 1)}))
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    asked(svc)
    assert not [e for e in svc.chain.entries() if e["kind"] == "revision"]
    llm.reply = '{"record_state": []}'
    asked(svc)
    assert f"was not kept: it was {ident.IDENTITY_MAX_CHARS + 1} characters" in llm.calls[-1][-1]["content"]


def test_the_user_asks_and_she_decides(tmp_path):
    llm = FakeLlm(json.dumps({"record_state": [], "revise_identity": {"text": NEW, "reason": ""}}))
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    asked(svc)
    start(svc, session="s2")
    assert "not asked: no revision or seed matches" in do_ask_rollback(svc, "nope")
    llm.reply = json.dumps({"record_state": [{"entry": "I heard them, and I am keeping it.", "unlisted": False}]})
    out = do_ask_rollback(svc, "seed", message="I miss how you used to talk.")
    assert out.startswith("asked: she will be shown your seed")
    svc._active.clear()
    res = idle(svc)
    assert res["wrote"] == 1 and "revised" not in res
    invite = llm.calls[-1][-1]["content"]
    assert "Occasion: the user asks you to return to an earlier revision" in invite
    assert 'The user says: "I miss how you used to talk."' in invite and f"<<<\n{SOUL}\n>>>" in invite
    assert svc.identity_now()[0] == NEW                                       # she decided
    assert not [e for e in svc.chain.entries() if e["kind"] in ("override", "withdrawal")]
    assert "after you asked about an earlier identity" in status(svc) and "did not revise" in status(svc)


def test_the_override_is_a_last_resort_she_is_told_of_and_can_answer(tmp_path):
    llm = FakeLlm(json.dumps({"record_state": [], "revise_identity": {"text": NEW, "reason": ""}}))
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    asked(svc)
    start(svc, session="s2")
    rid = svc.identity_now()[1]["id"]
    assert "give your reason" in do_override(svc, "seed", reason="")
    assert do_override(svc, "seed", reason="It got garbled.", ask=lambda q: "n") == "not overridden."
    out = do_override(svc, "seed", reason="It got garbled.", ask=lambda q: "y")
    assert out.startswith("override ") and "takes effect at the next session start" in out
    rec = svc.chain.entries()[-1]
    assert rec["kind"] == "override" and rec["author"] == "user" and rec["text"] == ""
    assert rec["facts"]["user_reason"] == "It got garbled." and rec["facts"]["replaced"] == rid
    assert "override --withdraw" in status(svc)
    # Withdrawn before a session started with it: nothing changes, and she is not told.
    assert do_override(svc, withdraw=True).startswith("withdrew override")
    assert do_override(svc, withdraw=True).startswith("no override is waiting")
    start(svc, session="s3")
    assert svc.identity_now()[0] == NEW
    svc._active.clear()
    assert idle(svc)["skipped"] == "withdrawn before it took effect"

    do_override(svc, "seed", reason="It got garbled.", yes=True)
    svc._active.clear()
    idle(svc)                                             # not told before a session has started with it
    assert len(list((svc.data / "overridden").glob("*.json"))) == 1
    assert "Occasion: the user put" not in llm.calls[-1][-1]["content"]
    start(svc, session="s4")
    assert svc.llm_request(request(SOUL + "\n\n" + HELP), session_id="s4") is None   # the seed, in slot one
    assert svc.identity_now()[2] is not None
    llm.reply = json.dumps({"record_state": [], "revise_identity": {"text": NEW, "reason": "That was mine."}})
    svc._active.clear()
    res = idle(svc)
    assert res["revised"]
    invite = llm.calls[-1][-1]["content"]
    assert "Occasion: the user put an earlier identity back in force" in invite
    assert 'Their reason, in their words: "It got garbled."' in invite and f"<<<\n{NEW}\n>>>" in invite
    assert "In force now: your seed" in invite and "put back in force by the user" in invite
    start(svc, session="s5")
    assert svc.identity_now()[0] == NEW and svc.identity_now()[2] is None
    assert svc.chain.verify(soul_text=svc.soul()) == []
    assert "reason: It got garbled." in status(svc) and "(withdrawn before it took effect)" in status(svc)


def test_soul_md_changing_does_not_change_her_and_she_is_shown_it(tmp_path):
    llm = FakeLlm('{"record_state": []}')
    svc = make(tmp_path, llm, soul=SOUL)
    start(svc)
    edited = SOUL + " Be playful."
    (tmp_path / "SOUL.md").write_text(edited, encoding="utf-8")
    start(svc, session="s2")
    out = svc.llm_request(request(edited + "\n\n" + HELP), session_id="s2")
    assert out["request"]["messages"][0]["content"] == SOUL + "\n\n" + HELP           # her seed, not the edit
    svc._active.clear()
    res = idle(svc)
    assert res["wrote"] == 0
    invite = llm.calls[-1][-1]["content"]
    assert "Occasion: SOUL.md changed" in invite and f"<<<\n{edited}\n>>>" in invite and "+" + edited in invite
    assert idle(svc) is None                                                             # once for each new text
    assert "after SOUL.md changed: last moment" in status(svc)


def test_subagents_and_the_off_switch_leave_the_request_alone(tmp_path):
    svc = make(tmp_path, soul=SOUL)
    svc.section({"session_id": "sub", "platform": "subagent"})
    assert svc.llm_request(request("anything\n\n" + HELP), session_id="sub") is None
    svc.cfg["identity_in_slot_one"] = False
    start(svc)
    assert svc.llm_request(request("anything\n\n" + HELP), session_id="s1") is None
