"""Backup and restore of her record (persona-provider.md 6.1 and 6.2).

A backup is a folder: her record, the anchor, and a manifest saying what the head was when it was taken.  Taking
one reads and changes nothing.  A restore is for loss and damage only, so it is refused while her record is there
and checks out; what is there is set aside, never deleted, a `restore` record is appended (facts only, no text),
and she is told at the next quiet moment, with what she wrote after the backup shown to her as dated text.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chain import Chain, sha256

FORMAT = 1
MANIFEST = "manifest.json"
RECORD = Path("self") / "entries.jsonl"
ANCHOR = "anchor.json"
# Her own words, which she can be shown again after a restore.  Records written by the user or the framework
# (seed, restore, home model) are not hers to record again.
HERS = ("state", "account", "dream_thoughts", "revision")


class RefusedError(Exception):
    """A backup or restore that was not done, and why, in words for the person who asked."""


def default_folder() -> Path:
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "thymos-backups"


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S")


def _when(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def _hermes_version() -> str:
    try:
        from hermes_cli import __version__
        return str(__version__)
    except Exception:
        return ""


def problems(chain: Chain) -> List[Dict[str, str]]:
    """What verify() reports about the record itself.  A changed SOUL.md is not damage to the record."""
    return [n for n in chain.verify() if n["problem"] != "seed_changed"]


def refusal(chain: Chain) -> str:
    """Why restoring would be refused now, or "" if it may run: the record is missing or does not check out, or
    holds nothing but a seed (Hermes started on a fresh home and seeded before the restore), or does not start
    with one (what was written while the record was lost)."""
    entries = chain.entries()
    if (not entries or problems(chain) or entries[0].get("kind") != "seed"
            or [e.get("kind") for e in entries] == ["seed"]):
        return ""
    return ("her record is there and checks out, so it was not restored. Restore is for a record that is lost or "
            "damaged; going back to an earlier point while it is intact would discard what she has written since.")


def backup(chain: Chain, dest: Optional[Path] = None, *, version: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """Copy her record and the anchor to a new folder with a manifest.  Holds the record's lock while copying, so
    the copy is never half an append.  Writes nothing to her record."""
    now = time.time() if now is None else now
    if not chain.path.exists():
        raise RefusedError(f"there is no record to back up at {chain.path}")
    # A folder that does not exist yet becomes the backup; an existing one (the default included) gets a new
    # dated folder inside it.
    dest = Path(dest) if dest else default_folder()
    folder = dest / f"persona-backup-{_stamp(now)}" if dest.is_dir() or dest == default_folder() else dest
    if folder.exists():
        raise RefusedError(f"{folder} already exists and is not empty")
    (folder / RECORD).parent.mkdir(parents=True, exist_ok=True)
    with chain.lock():
        shutil.copy2(chain.path, folder / RECORD)
        if chain.anchor_path.exists():
            shutil.copy2(chain.anchor_path, folder / ANCHOR)
    copied = Chain(folder / RECORD, folder / ANCHOR)
    entries = copied.entries()
    manifest = {"format": FORMAT, "store": "thymos", "made_at": now, "made": _when(now),
                "head": entries[-1].get("hash", "") if entries else "", "count": len(entries),
                "record_sha256": sha256((folder / RECORD).read_text(encoding="utf-8")),
                "thymos": version, "hermes": _hermes_version(), "taken_from": str(chain.path),
                "problems_when_taken": [n["detail"] for n in problems(copied)]}
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return dict(manifest, folder=str(folder))


def find(src: Optional[Path]) -> Path:
    """The backup folder `src` names: a backup folder, its manifest, or a folder of backups (the newest is used).
    With nothing given, the newest in the default folder."""
    src = Path(src) if src else default_folder()
    if src.is_file() and src.name == MANIFEST:
        return src.parent
    if (src / MANIFEST).is_file():
        return src
    found = sorted((p.parent for p in src.glob(f"*/{MANIFEST}")), key=lambda p: p.name) if src.is_dir() else []
    if not found:
        raise RefusedError(f"no backup found at {src}")
    return found[-1]


def check_backup(folder: Path) -> Dict[str, Any]:
    """The backup's manifest, if its record checks out against it.  Raises RefusedError otherwise."""
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RefusedError(f"the manifest at {folder / MANIFEST} cannot be read: {e}")
    if manifest.get("store") != "thymos" or int(manifest.get("format") or 0) > FORMAT:
        raise RefusedError(f"{folder} is not a backup this version of thymos can restore")
    copied = Chain(folder / RECORD, folder / ANCHOR)
    entries = copied.entries()
    if not entries:
        raise RefusedError(f"the backup at {folder} has no record in it")
    found = problems(copied)
    if found:
        raise RefusedError("the backup does not check out, so it was not restored:\n"
                           + "\n".join(f"  - {n['detail']}" for n in found))
    if entries[-1].get("hash") != manifest.get("head") or len(entries) != int(manifest.get("count") or -1):
        raise RefusedError(f"the backup's record ends at {entries[-1].get('hash', '')[:12]} after {len(entries)} "
                           f"entries, but its manifest says {str(manifest.get('head'))[:12]} after "
                           f"{manifest.get('count')}, so it was not restored")
    return manifest


def restore(chain: Chain, src: Optional[Path], data: Path, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Put her record back from a backup, if it is missing or does not check out.  The record as it is now is
    copied aside to self.replaced-<time>/ first and never deleted.  Appends a `restore` record and leaves the
    moment that tells her in `data`/restored/."""
    now = time.time() if now is None else now
    folder = find(src)
    manifest = check_backup(folder)
    with chain.lock():
        before = chain.entries()
        found = problems(chain)
        why = refusal(chain)
        if why:
            raise RefusedError(why)
        aside = chain.path.parent.with_name(f"{chain.path.parent.name}.replaced-{_stamp(now)}")
        readable = chain.path.exists()
        if chain.path.parent.exists():
            shutil.copytree(chain.path.parent, aside, ignore=shutil.ignore_patterns("*.lock"))
        if chain.anchor_path.exists():
            aside.mkdir(parents=True, exist_ok=True)
            shutil.copy2(chain.anchor_path, aside / ANCHOR)
        anchor = chain.read_anchor() or {}
        tmp = chain.path.with_suffix(".restoring")
        chain.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / RECORD, tmp)
        os.replace(tmp, chain.path)
        chain._write_anchor(manifest["head"], int(manifest["count"]))
        kept = {e.get("hash") for e in chain.entries()}
        later = [e for e in before if e.get("hash") not in kept]
        facts: Dict[str, Any] = {
            "restored_at": now, "backup_made_at": manifest.get("made_at"), "backup_head": manifest["head"],
            "backup_entries": int(manifest["count"]),
            "head_before": (before[-1].get("hash", "") if before else "") or str(anchor.get("head") or ""),
            "entries_before": len(before) if readable else "unknown",
            "entries_no_longer_present": len(later) if readable else "unknown",
            "problems_before": sorted({n["problem"] for n in found}) if readable else ["missing"],
            "set_aside": str(aside) if aside.exists() else "",
        }
        record = chain.append_locked("restore", author="user", facts=facts)
    shown = [{"at": e.get("at"), "kind": e.get("kind"), "text": e["text"], "unlisted": e.get("visibility") == "unlisted"}
             for e in later if e.get("kind") in HERS and e.get("author") == "self" and isinstance(e.get("text"), str)
             and e["text"].strip()]
    moment = dict(facts, restore_entry=record["hash"], later_entries=shown, readable=readable)
    out = data / "restored" / f"{int(now * 1000)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(moment, ensure_ascii=False), encoding="utf-8")
    return dict(facts, folder=str(folder), restore_entry=record["hash"], hers_to_show=len(shown))
