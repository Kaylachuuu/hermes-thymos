"""`hermes persona status`: what her record holds and whether it checks out.  Reads only."""
from __future__ import annotations

from collections import Counter
from typing import Any, Tuple

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
    return out


def register_cli(parser: Any, svc_factory) -> None:
    sub = parser.add_subparsers(dest="persona_command")
    sub.add_parser("status", help="What her record holds and whether its chain checks out")

    def run(args: Any) -> None:
        print(status(svc_factory()))
    parser.set_defaults(func=run)
