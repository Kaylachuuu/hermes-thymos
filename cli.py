"""`hermes persona status`: what her record holds and whether it checks out.  Reads only."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import backup
from . import identity as ident
from .chain import same_model, sha256
from .service import Thymos, _when


def configured_model() -> Tuple[str, str]:
    try:
        from hermes_cli.config import load_config_readonly
        model = (load_config_readonly() or {}).get("model") or {}
    except Exception:
        return "", ""
    if isinstance(model, str):
        return "", model
    return str(model.get("provider") or ""), str(model.get("default") or model.get("model") or "")


def status(svc: Thymos) -> str:
    entries = svc.chain.entries()
    if not entries and svc.chain.read_anchor():
        return (f"thymos: her record is missing. The anchor says it held {svc.chain.read_anchor().get('count')} "
                f"entries, ending at {str(svc.chain.read_anchor().get('head'))[:12]}.\nrecord: {svc.chain.path}\n"
                "Put it back from a backup with: hermes persona restore [BACKUP]")
    if not entries:
        return ("thymos: no record yet. It starts with her seed the first time a session opens with the plugin "
                f"enabled.\nrecord: {svc.chain.path}")
    out = [f"record: {svc.chain.path}", f"anchor: {svc.chain.anchor_path}"]
    seed = next((e for e in entries if e.get("kind") == "seed"), None)
    soul = svc.soul()
    if seed is not None:
        f = seed.get("facts", {})
        line = f"seed: {_when(seed['at'])}, SOUL.md sha256 {str(f.get('soul_sha256'))[:12]}"
        if soul is not None and sha256(soul) != f.get("soul_sha256"):
            line += (f"; SOUL.md has changed since (now {sha256(soul)[:12]}). Hermes loads the file as it is now, "
                     "so the change is in her prompt")
        out.append(line)
    provider, home = svc.home_model()
    out.append(f"home model: {home}" + (f" ({provider})" if provider else ""))
    out += _identity_lines(svc, entries)
    cfg_provider, cfg_model = configured_model()
    if cfg_model and home and not same_model(cfg_model, home):
        out.append(f"configured main model: {cfg_model}. That is not her home model, so request_reflection is refused "
                   "and no reflection moment opens on it. Changing her home model is not in this version.")
    kinds = Counter(e.get("kind") for e in entries)
    notes = svc.notes()
    out.append(f"entries: {len(entries)} ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))})"
               + (f", her last note {_when(notes[-1]['at'])}" if notes else ""))
    notices = svc.chain.verify(soul_text=soul)
    head = entries[-1].get("hash", "")[:12]
    tamper = [n for n in notices if n["problem"] != "seed_changed"]
    if tamper:
        out.append(f"chain: {len(tamper)} problem(s), head {head}")
        out += [f"  - {n['problem']}: {n['detail']}" for n in tamper]
    else:
        out.append(f"chain: verified, head {head}, anchor matches")
    state = svc._state()
    pending = state.get("pending")
    out.append("pending reflection: " + (f"asked for {_when(pending['requested_at'])} in session {pending.get('session_id')}"
                                         if pending else "none"))
    lr = state.get("last_reflection")
    if lr:
        line = f"last reflection: {_when(lr['at'])}"
        if "wrote" in lr:
            line += f", she wrote {lr['wrote']} entr{'y' if lr['wrote'] == 1 else 'ies'} on {lr.get('model')}"
            if lr.get("revised"):
                line += f", and revised her identity (revision {lr['revised']})"
            if lr.get("withdrew"):
                line += f", and withdrew revision {lr['withdrew']}"
        if lr.get("problem"):
            line += f"; {lr['problem']}"
        out.append(line)
    out += _idle_lines(svc, state)
    return "\n".join(out)


def _identity_lines(svc: Thymos, entries: list) -> list:
    _, record, cause = svc.identity_now()
    revisions = [e for e in entries if e.get("kind") == "revision" and e.get("author") == "self"]
    out = [f"identity in force: {ident.describe(record, cause, _when)}; {_n(len(revisions), 'revision', 'revisions')} "
           "written by her"]
    for e in svc.pending_identity():
        if e["kind"] == "revision":
            out.append(f"  her revision of {_when(e['at'])} (id {e['id']}) takes effect at the next session start"
                       + (f"; Hermes' injection check found: {', '.join(e['facts']['scan'])}" if (e.get("facts") or {}).get("scan") else ""))
        else:
            out.append(f"  override {e['id']} of {_when(e['at'])} takes effect at the next session start "
                       "(withdraw it with: hermes persona override --withdraw)")
    overrides = [e for e in entries if e.get("kind") == "override"]
    gone = ident.withdrawn(entries)
    for e in overrides:
        f = e.get("facts") or {}
        out.append(f"  override {e['id']} on {_when(e['at'])}: put {f.get('target')} in place of {f.get('replaced')}"
                   + (" (withdrawn before it took effect)" if e["id"] in gone else "")
                   + (f"; reason: {f.get('user_reason')}" if f.get("user_reason") else ""))
    slot = svc._read_json(svc.data / "slot-one.json")
    if slot:
        result = {"placed": "her identity put in slot one", "already in place": "slot one already held her identity"}.get(
            slot.get("result"), f"not placed: {slot.get('result')}")
        out.append(f"slot one: {result} ({_when(slot['at'])}, session {slot.get('session_id')})")
    else:
        out.append("slot one: no session has run with this version yet")
    return out


def identity_text(svc: Thymos, ref: str = "") -> str:
    """`hermes persona identity [ID]`: the text in force, or the seed or a revision; and the list of them."""
    if ref:
        rec = svc.find_identity(ref)
        if rec is None:
            return f"no revision or seed matches {ref!r}"
        return f"{ident.describe(rec, None, _when)}" + (f"\nher reason: {rec['reason']}" if rec.get("reason") else "") \
            + f"\n\n{rec.get('text', '')}"
    text, record, cause = svc.identity_now()
    out = [f"in force: {ident.describe(record, cause, _when)}", "", text, "", "her seed and revisions:"]
    for e in svc.revisions():
        out.append(f"  {'seed' if e['kind'] == 'seed' else e['id']}  {_when(e['at'])}"
                   + (f"  {e['reason'][:80]}" if e.get("reason") else ""))
    return "\n".join(out)


OVERRIDE_WARNING = """This is the exception to "nothing outside her write path changes her identity". It is for an identity
that is damaged, or that cannot revise itself: not a way to change her mind. To ask her instead, so she decides:
  hermes persona ask-rollback {ref} --message "..."
The override takes effect at the next session start (in the CLI, /new). Until then you can undo it with
  hermes persona override --withdraw
She is told in the first session where it is in force, with your reason in your words, and she can revise again."""


def do_override(svc: Thymos, ref: str = "", reason: str = "", withdraw: bool = False, yes: bool = False,
                ask: Callable[[str], str] = input) -> str:
    if withdraw:
        rec = svc.withdraw_override()
        return (f"withdrew override {rec['facts']['withdraws']}" if rec else
                "no override is waiting to take effect, so there is nothing to withdraw")
    target = svc.find_identity(ref)
    if target is None:
        return f"not overridden: no revision or seed matches {ref!r} (see: hermes persona identity)"
    if not reason.strip():
        return "not overridden: give your reason with --reason; she is shown it, as your words"
    print(OVERRIDE_WARNING.format(ref=ref))
    print(f"\nput in force: {ident.describe(target, None, _when)}")
    if not yes and ask("Override her identity? [y/N] ").strip().lower() not in ("y", "yes"):
        return "not overridden."
    rec = svc.override(target, reason)
    return f"override {rec['id']} written; it takes effect at the next session start"


def do_ask_rollback(svc: Thymos, ref: str, message: str = "") -> str:
    target = svc.find_identity(ref)
    if target is None:
        return f"not asked: no revision or seed matches {ref!r} (see: hermes persona identity)"
    svc.ask_rollback(target, message)
    return (f"asked: she will be shown {ident.describe(target, None, _when)} at the next quiet moment while Hermes "
            "is running" + (", with your message" if message.strip() else "") + ". She decides.")


def _idle_lines(svc: Thymos, state: dict) -> list:
    """Idle time: conversations waiting to be offered to her, and what became of her accounts."""
    out = []
    convs = svc.conversations()
    due = svc.due_conversations()
    out.append(f"conversations kept for idle time: {len(convs)}, {len(due)} waiting to be offered to her")
    la = state.get("last_account")
    if la:
        line = f"last account moment: {_when(la['at'])}, conversation {la.get('session_id')}"
        if "account" in la:
            line += (", she stored an account" if la["account"] else ", she stored no account")
            if la.get("wrote"):
                line += f" and {la['wrote']} entr{'y' if la['wrote'] == 1 else 'ies'}"
            line += f" on {la.get('model')}"
        if la.get("problem"):
            line += f"; {la['problem']}"
        out.append(line)
    folder = svc.data / "accounts"
    waiting = len(list(folder.glob("*.json"))) if folder.exists() else 0
    stored = len(list((folder / "stored").glob("*.json"))) if (folder / "stored").exists() else 0
    accounts = sum(1 for e in svc.chain.entries() if e.get("kind") == "account")
    out.append(f"her accounts: {accounts} in her record; {stored} stored by the memory provider, {waiting} waiting for it"
               + (" (it stores them while Hermes is open, if it reads them: holonomic 0.25 or later)" if waiting else ""))
    slept = len(list((svc.data / "slept").glob("*.json"))) if (svc.data / "slept").exists() else 0
    ls = state.get("last_slept")
    line = f"after memory slept: {slept} waiting to be offered to her"
    if ls:
        line += f"; last moment {_when(ls['at'])}"
        if "dream_thoughts" in ls:
            line += (f", she wrote about {ls['dream_thoughts']} of {ls['dreams']} dream(s)" if ls["dream_thoughts"]
                     else f", she wrote nothing about the dream{'s' if ls.get('dreams', 1) > 1 else ''}")
            if ls.get("wrote"):
                line += f" and {ls['wrote']} entr{'y' if ls['wrote'] == 1 else 'ies'}"
            line += f" on {ls.get('model')}"
        if ls.get("problem"):
            line += f"; {ls['problem']}"
    out.append(line)
    restored = len(list((svc.data / "restored").glob("*.json"))) if (svc.data / "restored").exists() else 0
    lr = state.get("last_restored")
    if restored or lr:
        line = "after a restore: " + ("waiting to be told" if restored else "she was told")
        if lr:
            line += f"; last moment {_when(lr['at'])}"
            if "wrote" in lr:
                line += (f", shown {lr['shown']} entr{'y' if lr['shown'] == 1 else 'ies'} from after the backup, she wrote "
                         f"{lr['wrote']} on {lr.get('model')}")
            if lr.get("problem"):
                line += f"; {lr['problem']}"
        out.append(line)
    for key, label in (("last_rollback", "after you asked about an earlier identity"),
                       ("last_overridden", "after an override"), ("last_seed_changed", "after SOUL.md changed")):
        lm = state.get(key)
        if lm:
            line = f"{label}: last moment {_when(lm['at'])}"
            if lm.get("skipped"):
                line += f", skipped: {lm['skipped']}"
            elif "wrote" in lm:
                line += (f", she wrote {_n(lm['wrote'], 'entry', 'entries')}"
                         + (", and revised her identity" if lm.get("revised") else ", and did not revise her identity")
                         + f" on {lm.get('model')}")
            if lm.get("problem"):
                line += f"; {lm['problem']}"
            out.append(line)
    old = svc.data / "old-notes.json"
    lo = state.get("last_old_notes")
    if old.exists() or lo:
        line = "notes another model wrote in her voice: " + ("waiting to be offered to her" if old.exists() else "offered")
        if lo:
            line += f"; {_when(lo['at'])}"
            if "wrote" in lo:
                line += f", she kept {lo['wrote']} entr{'y' if lo['wrote'] == 1 else 'ies'} in her own words on {lo.get('model')}"
            if lo.get("problem"):
                line += f"; {lo['problem']}"
        out.append(line)
    return out


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def do_backup(svc: Thymos, dest: Optional[str] = None) -> str:
    from . import __version__
    try:
        m = backup.backup(svc.chain, Path(dest).expanduser() if dest else None, version=__version__)
    except backup.RefusedError as e:
        return f"not backed up: {e}"
    out = [f"backed up her record to {m['folder']}",
           f"  {_n(m['count'], 'entry', 'entries')}, head {m['head'][:12]}, taken {m['made']}"]
    if m["problems_when_taken"]:
        out.append("  her record did not check out when this was taken; the backup is a copy of it as it was, and "
                   "cannot be restored from:")
        out += [f"    - {p}" for p in m["problems_when_taken"]]
    out.append("  her memory is not in it; back that up with: hermes holonomic backup")
    return "\n".join(out)


def do_restore(svc: Thymos, src: Optional[str] = None, yes: bool = False, ask: Callable[[str], str] = input) -> str:
    try:
        folder = backup.find(Path(src).expanduser() if src else None)
        m = backup.check_backup(folder)
    except backup.RefusedError as e:
        return f"not restored: {e}"
    why = backup.refusal(svc.chain)
    if why:
        return f"not restored: {why}"
    found = backup.problems(svc.chain)
    print(f"backup: {folder}\n  {_n(m['count'], 'entry', 'entries')}, head {str(m['head'])[:12]}, taken {m.get('made')}")
    if not svc.chain.entries():
        now = "missing"
    elif found:
        now = f"{len(found)} problem(s)\n" + "\n".join(f"  - {n['detail']}" for n in found)
    else:
        now = "nothing in it but a seed (a fresh start)"
    print("her record now: " + now)
    print(f"what is there now is copied aside to {svc.chain.path.parent}.replaced-<time>, not deleted, and she is "
          "told at the next quiet moment.")
    if not yes and ask("Restore her record from this backup? [y/N] ").strip().lower() not in ("y", "yes"):
        return "not restored."
    try:
        r = backup.restore(svc.chain, folder, svc.data)
    except backup.RefusedError as e:
        return f"not restored: {e}"
    gone = r["entries_no_longer_present"]
    return "\n".join([
        f"restored her record from {r['folder']}",
        f"  set aside: {r['set_aside'] or '(nothing was there)'}",
        f"  entries written after the backup that are no longer in it: {gone}"
        + (f", {r['hers_to_show']} of them hers, to be shown to her" if r["hers_to_show"] else ""),
        "  a restore record was appended, and she is told at the next quiet moment while Hermes is running.",
        "  if her memory was restored too or needs to be: hermes holonomic restore"])


def register_cli(parser: Any, svc_factory) -> None:
    sub = parser.add_subparsers(dest="persona_command")
    sub.add_parser("status", help="What her record holds and whether its chain checks out")
    b = sub.add_parser("backup", help="Copy her record, its anchor and a manifest to a folder (reads and changes nothing)")
    b.add_argument("dest", nargs="?", help=f"Folder to write; default a new dated folder in {backup.default_folder()}")
    r = sub.add_parser("restore", help="Put her record back from a backup, only if it is missing or damaged")
    r.add_argument("src", nargs="?", help="A backup folder, or a folder of them (the newest); default the newest "
                                          f"in {backup.default_folder()}")
    r.add_argument("--yes", action="store_true", help="Do not ask before restoring")
    i = sub.add_parser("identity", help="Her identity in force, or her seed or a revision, and the list of them")
    i.add_argument("ref", nargs="?", default="", help="A revision id (or its start), or 'seed'")
    a = sub.add_parser("ask-rollback", help="Ask her to return to an earlier revision of her identity; she decides")
    a.add_argument("ref", help="A revision id (or its start), or 'seed'")
    a.add_argument("--message", default="", help="Your words to her, shown to her as yours")
    o = sub.add_parser("override", help="Last resort: put an earlier revision of hers, or her seed, back in force")
    o.add_argument("ref", nargs="?", default="", help="A revision id (or its start), or 'seed'")
    o.add_argument("--reason", default="", help="Why, in your words; she is shown it")
    o.add_argument("--withdraw", action="store_true", help="Undo an override that has not taken effect yet")
    o.add_argument("--yes", action="store_true", help="Do not ask before overriding")

    def run(args: Any) -> None:
        command = getattr(args, "persona_command", None)
        if command == "backup":
            print(do_backup(svc_factory(), args.dest))
        elif command == "restore":
            print(do_restore(svc_factory(), args.src, args.yes))
        elif command == "identity":
            print(identity_text(svc_factory(), args.ref))
        elif command == "ask-rollback":
            print(do_ask_rollback(svc_factory(), args.ref, args.message))
        elif command == "override":
            print(do_override(svc_factory(), args.ref, args.reason, args.withdraw, args.yes))
        else:
            print(status(svc_factory()))
    parser.set_defaults(func=run)
