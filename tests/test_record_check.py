"""The tamper notice (persona-provider.md 4.2): when her record fails its check, she is told at the next quiet
moment, once for each set of problems, with what the check finds then.  Nothing is repaired."""
from thymos.cli import status

from test_service import FakeLlm, make, start, turn_end
from test_idle import idle


def written(svc):
    start(svc)
    svc.request_reflection({}, session_id="s1")
    turn_end(svc)
    svc._active.clear()


def test_she_is_told_once_when_her_record_fails_its_check(tmp_path):
    llm = FakeLlm('{"record_state": ["One."]}')
    svc = make(tmp_path, llm)
    written(svc)
    assert [k for k, _, _ in svc._items(0)] == []                               # it checks out: nothing to tell
    calls = len(llm.calls)
    path = svc.chain.path
    path.write_text(path.read_text(encoding="utf-8").replace("One.", "Two."), encoding="utf-8")
    llm.reply = '{"record_state": ["Someone changed what I wrote. I want to know who."]}'
    svc._items(0)
    assert "her record failed its check: waiting" in status(svc)
    out = idle(svc, after=7200)
    assert out["wrote"] == 1
    invite = llm.calls[-1][-1]["content"]
    assert "Occasion: your record failed its check" in invite and '"changed": 1' in invite
    assert "was changed after it was written" in invite and "nothing has been repaired" in invite
    assert "contents no longer match the hash it was written with" in invite
    assert len(llm.calls) == calls + 1
    assert svc.notes()[-1]["text"] == "Someone changed what I wrote. I want to know who."
    assert "after her record failed its check: last moment" in status(svc)
    # The same problem is not told again; a new one is.
    assert "record_check" not in [k for k, _, _ in svc._items(0)]
    svc.chain.anchor_path.write_text('{"head": "0000"}', encoding="utf-8")
    llm.reply = '{"record_state": []}'
    out = idle(svc, after=12000)
    assert out["wrote"] == 0 and '"anchor_mismatch": 1' in llm.calls[-1][-1]["content"]
    assert '"told_before"' in llm.calls[-1][-1]["content"]


def test_a_record_that_checks_out_again_is_not_told(tmp_path):
    llm = FakeLlm('{"record_state": ["One."]}')
    svc = make(tmp_path, llm)
    written(svc)
    path = svc.chain.path
    good = path.read_text(encoding="utf-8")
    path.write_text(good.replace("One.", "Two."), encoding="utf-8")
    svc._items(0)                                  # noticed at a look while someone was talking
    path.write_text(good, encoding="utf-8")
    calls = len(llm.calls)
    out = idle(svc, after=7200)
    assert out["skipped"] == "the record checked out again before she was told" and len(llm.calls) == calls


def test_a_changed_soul_md_has_its_own_moment_not_this_one(tmp_path):
    svc = make(tmp_path, FakeLlm('{"record_state": []}'))
    written(svc)
    (tmp_path / "SOUL.md").write_text("Edited.", encoding="utf-8")
    kinds = [k for k, _, _ in svc._items(0)]
    assert "seed_changed" in kinds and "record_check" not in kinds
