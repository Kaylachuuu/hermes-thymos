"""Her record: an append-only file of entries, each holding the hash of the one before it.

The chain head is also kept in a second place (the anchor), so editing the file and recomputing every
hash still leaves a mismatch.  Nothing here decides anything: it appends, reads and checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Every field a record's hash covers (persona-provider.md, section 5).  The multi-user fields are written
# empty for now; they are here so the record format does not change when those features arrive.
HASHED = ("id", "kind", "author", "text", "facts", "reason", "at", "session_id", "visibility", "scope",
          "shown_with", "person_id", "audience_id", "about", "model", "model_digest", "prev_hash")
KINDS = ("state", "revision", "withdrawal", "seed", "restore", "override", "home_model", "account", "dream_thoughts")
DEFAULTS: Dict[str, Any] = {"author": "self", "text": "", "facts": {}, "reason": "", "session_id": "",
                            "visibility": "shared", "scope": "everywhere", "shown_with": [], "person_id": "",
                            "audience_id": "", "about": "", "model": "", "model_digest": ""}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry_hash(entry: Dict[str, Any]) -> str:
    body = {k: entry.get(k, DEFAULTS.get(k, "")) for k in HASHED}
    if entry.get("salt"):
        # Random, inside the hash, on every entry written since 0.10.0: if an entry's words are ever erased
        # (persona-provider.md 18.3), the hash it keeps cannot be used to check a guess at what they were.
        # Entries written before have none, and their hashes are as they were.
        body["salt"] = entry["salt"]
    return sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


class _Lock:
    """A lock file, so a gateway and a CLI writing at once cannot both append after the same head.
    os.O_EXCL works the same on Windows and Linux; a lock older than `stale` seconds is from a dead process."""

    def __init__(self, path: Path, timeout: float = 10.0, stale: float = 30.0):
        self.path, self.timeout, self.stale = path, timeout, stale

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"could not lock {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class Chain:
    def __init__(self, path: Path, anchor_path: Path, digest: Optional[Callable[[str], str]] = None):
        self.path, self.anchor_path = Path(path), Path(anchor_path)
        # Her home model's fingerprint for an entry she writes (persona-provider.md 9.5), given its model label.
        self.digest = digest

    # -- reading ------------------------------------------------------------------------------------
    def _lines(self) -> List[str]:
        try:
            return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except FileNotFoundError:
            return []

    def entries(self) -> List[Dict[str, Any]]:
        """Every record that parses, in order.  verify() reports the ones that do not."""
        out = []
        for ln in self._lines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def read_anchor(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.anchor_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    # -- writing ------------------------------------------------------------------------------------
    def append(self, kind: str, **fields: Any) -> Dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock():
            return self.append_locked(kind, **fields)

    def lock(self) -> _Lock:
        """Held while anything writes the record.  Backup holds it too, so its copy is never half an append."""
        return _Lock(self.path.with_suffix(".lock"))

    def append_locked(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """append(), for a caller already holding lock() (restore)."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.entries()
        entry = dict(DEFAULTS)
        entry.update(fields)
        entry.update(id=fields.get("id") or uuid.uuid4().hex[:12], kind=kind,
                     at=float(fields.get("at") or time.time()),
                     prev_hash=existing[-1].get("hash", "") if existing else "", salt=secrets.token_hex(16))
        entry["shown_with"] = list(entry.get("shown_with") or [])
        if self.digest is not None and entry["author"] == "self" and entry["model"] and not entry["model_digest"]:
            try:
                entry["model_digest"] = self.digest(entry["model"]) or ""
            except Exception:
                pass
        entry["hash"] = entry_hash(entry)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._write_anchor(entry["hash"], len(existing) + 1)
        return entry

    def _write_anchor(self, head: str, count: int) -> None:
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.anchor_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"head": head, "count": count, "at": time.time()}), encoding="utf-8")
        os.replace(tmp, self.anchor_path)

    # -- checking -----------------------------------------------------------------------------------
    def verify(self, *, soul_text: Optional[str] = None) -> List[Dict[str, str]]:
        """Facts about anything not written through her write path.  Never repairs anything."""
        notices: List[Dict[str, str]] = []
        seen: set = set()
        prev = ""
        home: Tuple[str, str] = ("", "")
        lines = self._lines()
        for n, ln in enumerate(lines, 1):
            try:
                rec = json.loads(ln)
                assert isinstance(rec, dict)
            except Exception:
                notices.append(dict(entry_id=f"line {n}", problem="changed", detail=f"line {n} of {self.path} is not a record"))
                prev = ""
                continue
            eid = str(rec.get("id", f"line {n}"))
            if entry_hash(rec) != rec.get("hash"):
                notices.append(dict(entry_id=eid, problem="changed",
                                    detail=f"entry {eid} (line {n}) was changed after it was written: its hash is "
                                           f"{rec.get('hash', '')[:12]}, its contents now hash to {entry_hash(rec)[:12]}"))
            if rec.get("prev_hash", "") != prev:
                if rec.get("prev_hash") and rec.get("prev_hash") not in seen:
                    notices.append(dict(entry_id=eid, problem="missing",
                                        detail=f"the entry before {eid} (line {n}) is not in the file"))
                else:
                    notices.append(dict(entry_id=eid, problem="inserted",
                                        detail=f"an entry before {eid} (line {n}) was not written through her write path"))
            if rec.get("kind") == "seed":
                home = (rec.get("facts", {}).get("home_provider", ""), rec.get("facts", {}).get("home_model", ""))
            elif rec.get("kind") == "home_model":
                home = (rec.get("facts", {}).get("new_provider", ""), rec.get("facts", {}).get("new_model", ""))
            elif rec.get("author") == "self" and home[1] and not same_model(rec.get("model", ""), home[1]):
                notices.append(dict(entry_id=eid, problem="foreign_model",
                                    detail=f"entry {eid} was written by {rec.get('model') or 'an unrecorded model'}, "
                                           f"not her home model {home[1]}"))
            seen.add(rec.get("hash"))
            prev = rec.get("hash", "")
        anchor = self.read_anchor()
        if lines and anchor is None:
            notices.append(dict(entry_id="", problem="anchor_mismatch", detail=f"no anchor at {self.anchor_path}"))
        elif anchor is not None and anchor.get("head") != prev:
            notices.append(dict(entry_id="", problem="anchor_mismatch",
                                detail=f"the last entry's hash is {prev[:12] or 'none'}, the anchor at "
                                       f"{self.anchor_path} says {str(anchor.get('head'))[:12]}"))
        seed = next((e for e in self.entries() if e.get("kind") == "seed"), None)
        if seed is not None and soul_text is not None and sha256(soul_text) != seed.get("facts", {}).get("soul_sha256"):
            notices.append(dict(entry_id=seed.get("id", ""), problem="seed_changed",
                                detail=f"SOUL.md now hashes to {sha256(soul_text)[:12]}; her seed was "
                                       f"{str(seed.get('facts', {}).get('soul_sha256'))[:12]}"))
        return notices


def model_key(model: str) -> str:
    """Compare model names the way providers spell them: case, an "ollama/"-style prefix and ":latest"
    do not make a different model."""
    m = (model or "").strip().lower()
    if ":" in m and m.split(":", 1)[0] in {"ollama", "ollama_chat", "local", "custom"}:
        m = m.split(":", 1)[1]
    if "/" in m and m.split("/", 1)[0] in {"ollama", "ollama_chat", "local", "custom"}:
        m = m.split("/", 1)[1]
    return m[:-len(":latest")] if m.endswith(":latest") else m


def same_model(a: str, b: str) -> bool:
    """`a` may be "provider:model" as recorded on an entry; compare the model part."""
    def bare(x: str) -> str:
        x = x or ""
        return x.split("|", 1)[1] if "|" in x else x
    return bool(bare(a)) and model_key(bare(a)) == model_key(bare(b))


def model_label(provider: str, model: str) -> str:
    """How an entry records its model.  "|" separates them, because model names contain ":" and "/"."""
    return f"{provider}|{model}" if provider else model


def last(entries: Iterable[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    found = None
    for e in entries:
        if e.get("kind") == kind:
            found = e
    return found
