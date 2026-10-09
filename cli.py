"""`hermes persona status`: what her record holds and whether it checks out.  Reads only."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import backup
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
        if lr.get("problem"):
            line += f"; {lr['problem']}"
        out.append(line)
    out += _idle_lines(svc, state)
    return "\n".join(out)


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

    def run(args: Any) -> None:
        command = getattr(args, "persona_command", None)
        if command == "backup":
            print(do_backup(svc_factory(), args.dest))
        elif command == "restore":
            print(do_restore(svc_factory(), args.src, args.yes))
        else:
            print(status(svc_factory()))
    parser.set_defaults(func=run)
