"""Backup and restore of her record (persona-provider.md 6.1, 6.2): a backup reads and changes nothing; a restore
runs only for a record that is missing or damaged, sets aside what is there, records the fact, and tells her."""
import json
import time

from thymos import backup
from thymos.cli import do_backup, do_restore, status

from test_service import FakeLlm, make, start, turn_end
from test_idle import idle


def written(svc, *texts):
    for t in texts:
        svc.chain.append("state", author="self", text=t, model="ollama|gemma3:12b")


def test_a_backup_reads_and_changes_nothing(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    written(svc, "I like mornings.")
    before = svc.chain.path.read_text(encoding="utf-8"), svc.chain.anchor_path.read_text(encoding="utf-8")
    out = do_backup(svc, str(tmp_path / "b1"))
    assert "backed up her record to" in out and "2 entries" in out and "hermes holonomic backup" in out
    assert (svc.chain.path.read_text(encoding="utf-8"), svc.chain.anchor_path.read_text(encoding="utf-8")) == before
    m = json.loads((tmp_path / "b1" / "manifest.json").read_text())
    assert m["count"] == 2 and m["head"] == svc.chain.entries()[-1]["hash"] and m["format"] == 1 and m["thymos"]
    assert backup.check_backup(tmp_path / "b1")["head"] == m["head"]
    # An existing folder gets a dated backup inside it, and find() picks the newest.
    m2 = backup.backup(svc.chain, tmp_path / "b1", now=time.time() + 5)
    assert m2["folder"].startswith(str(tmp_path / "b1")) and backup.find(tmp_path / "b1").name == "b1"
    assert not list(tmp_path.glob("home/plugin-data/thymos/restored/*"))


def test_restore_is_refused_while_her_record_checks_out(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    backup.backup(svc.chain, tmp_path / "b")
    written(svc, "Written after the backup.")
    out = do_restore(svc, str(tmp_path / "b"), yes=True)
    assert out.startswith("not restored: her record is there and checks out")
    assert [n["text"] for n in svc.notes()] == ["Written after the backup."]


def test_a_backup_that_does_not_check_out_is_not_restored(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    written(svc, "One.")
    backup.backup(svc.chain, tmp_path / "b")
    rec = tmp_path / "b" / "self" / "entries.jsonl"
    rec.write_text(rec.read_text().replace("One.", "Two."), encoding="utf-8")
    svc.chain.path.unlink()
    out = do_restore(svc, str(tmp_path / "b"), yes=True)
    assert "the backup does not check out" in out and not svc.chain.path.exists()


def test_a_damaged_record_is_restored_set_aside_recorded_and_she_is_told(tmp_path):
    llm = FakeLlm('{"record_state": [{"entry": "I still like the evenings.", "unlisted": false}]}')
    svc = make(tmp_path / "home", llm)
    start(svc)
    written(svc, "I like mornings.")
    m = backup.backup(svc.chain, tmp_path / "b", now=time.time() - 86400)
    written(svc, "I like the evenings too.")
    svc.chain.append("account", author="self", text="We talked about the garden.", model="ollama|gemma3:12b")
    old_head = svc.chain.entries()[-1]["hash"]
    # Damage: someone edits an entry in the file.
    text = svc.chain.path.read_text(encoding="utf-8")
    svc.chain.path.write_text(text.replace("I like mornings.", "I hate mornings."), encoding="utf-8")
    assert "1 problem(s)" in status(svc)

    asked = []
    out = do_restore(svc, str(tmp_path / "b"), ask=lambda q: asked.append(q) or "y")
    assert asked and "restored her record from" in out and "no longer in it: 2, 2 of them hers" in out

    entries = svc.chain.entries()
    assert [e["kind"] for e in entries] == ["seed", "state", "restore"]
    rec = entries[-1]
    assert rec["author"] == "user" and rec["text"] == "" and rec["prev_hash"] == m["head"]
    f = rec["facts"]
    assert f["head_before"] == old_head and f["entries_no_longer_present"] == 2 and f["problems_before"] == ["changed"]
    assert svc.chain.verify(soul_text=svc.soul()) == []
    aside = svc.home / f["set_aside"].split("/")[-1]
    assert aside.name.startswith("self.replaced-") and "I hate mornings." in (aside / "entries.jsonl").read_text()
    assert (aside / "anchor.json").exists()

    # Until she has been told, her prompt carries the fact.
    text = start(svc, session="s2")
    assert "your record was restored on" in text and "2 entries written after the backup are no longer in it" in text
    assert "back up your record and restore it if it is lost or damaged" in text

    # At the next quiet moment she is told first, and shown what she wrote after the backup as dated text.
    svc._active.clear()
    turn_end(svc, session="s2")
    out = idle(svc)
    assert out["wrote"] == 1 and out["shown"] == 2
    invite = llm.calls[-1][1]["content"]
    assert "Occasion: your record was restored from a backup" in invite and "was damaged" in invite
    assert "(an entry): I like the evenings too." in invite
    assert "(your account of a conversation): We talked about the garden." in invite
    assert '"entries_no_longer_present": 2' in invite
    assert [n["text"] for n in svc.notes()] == ["I like mornings.", "I still like the evenings."]
    assert not list((svc.data / "restored").glob("*.json")) and list((svc.data / "restored" / "done").glob("*.json"))
    assert "your record was restored on" not in start(svc, session="s3")
    assert "after a restore: she was told" in status(svc) and "shown 2 entries" in status(svc)


def test_a_missing_record_is_restored_and_what_was_lost_is_unknown(tmp_path):
    llm = FakeLlm('{"record_state": []}')
    svc = make(tmp_path / "home", llm)
    start(svc)
    written(svc, "One.")
    backup.backup(svc.chain, tmp_path / "b")
    svc.chain.path.unlink()
    out = do_restore(svc, str(tmp_path / "b"), yes=True)
    assert "no longer in it: unknown" in out
    rec = svc.chain.entries()[-1]
    assert rec["kind"] == "restore" and rec["facts"]["problems_before"] == ["missing"]
    assert rec["facts"]["entries_no_longer_present"] == "unknown"
    out = idle(svc)
    assert out["wrote"] == 0
    invite = llm.calls[-1][1]["content"]
    assert "Your record was missing" in invite and "could not be read" in invite


def test_declining_at_the_prompt_restores_nothing(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    backup.backup(svc.chain, tmp_path / "b")
    svc.chain.path.unlink()
    assert do_restore(svc, str(tmp_path / "b"), ask=lambda q: "n") == "not restored."
    assert not svc.chain.path.exists()


def test_a_lost_record_is_not_replaced_by_a_fresh_seed_and_can_be_put_back(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    written(svc, "One.")
    backup.backup(svc.chain, tmp_path / "b")
    svc.chain.path.unlink()
    assert "her record is missing. The anchor says it held 2 entries" in status(svc)
    text = start(svc, session="s2")                       # Hermes starts again: no new seed in its place
    assert not svc.chain.entries() and "the anchor at" in text
    assert "restored her record" in do_restore(svc, str(tmp_path / "b"), yes=True)
    assert [e["kind"] for e in svc.chain.entries()] == ["seed", "state", "restore"]


def test_a_fresh_seed_alone_does_not_block_a_restore(tmp_path):
    svc = make(tmp_path / "home")
    start(svc)
    written(svc, "One.")
    backup.backup(svc.chain, tmp_path / "b")
    for p in (svc.chain.path, svc.chain.anchor_path):
        p.unlink()                                        # the whole home was lost
    start(svc, session="s2")
    assert [e["kind"] for e in svc.chain.entries()] == ["seed"]
    out = do_restore(svc, str(tmp_path / "b"), yes=True)
    assert "restored her record" in out and "no longer in it: 1" in out
    assert [e["kind"] for e in svc.chain.entries()] == ["seed", "state", "restore"]
