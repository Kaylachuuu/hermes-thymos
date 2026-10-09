"""Her identity: which text is in force, and putting it in slot one of the system prompt (persona-provider.md
3, 4.3, 6.3, 6.4).

The seed is the identity she starts from.  After that the identity in force is her latest revision, unless the user
put an earlier one (or the seed) back in force with an override, which is itself undone by her next revision.  A
revision or an override takes effect at the next session start, and until then it can be withdrawn.  Nothing here
writes; it reads the record and rewrites a request.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Tuple

IDENTITY_MAX_CHARS = 8000

# Hermes builds the system prompt's first tier as her identity (SOUL.md, or its default identity) followed by this
# paragraph (agent/system_prompt.py), so whatever comes before it is slot one.
HELP_MARKER = "You run on Hermes Agent"


def withdrawn(entries: List[Dict[str, Any]]) -> set:
    return {(e.get("facts") or {}).get("withdraws") for e in entries if e.get("kind") == "withdrawal"}


def in_force(entries: List[Dict[str, Any]], before: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The record whose text is her identity, counting only what was written before `before` (a session start).
    For an override, the record returned is the one it put back in force; see `in_force_with_cause`."""
    return in_force_with_cause(entries, before)[0]


def in_force_with_cause(entries: List[Dict[str, Any]], before: Optional[float] = None
                        ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(the record whose text is in force, the override that put it there or None)."""
    gone = withdrawn(entries)
    by_id = {e.get("id"): e for e in entries}
    current, cause = None, None
    for e in entries:
        if before is not None and float(e.get("at") or 0) > before:
            break
        kind = e.get("kind")
        if kind == "seed":
            current, cause = e, None
        elif kind == "revision" and e.get("author") == "self" and e.get("id") not in gone:
            current, cause = e, None
        elif kind == "override" and e.get("author") == "user" and e.get("id") not in gone:
            target = by_id.get((e.get("facts") or {}).get("target"))
            if target is not None and target.get("kind") in ("revision", "seed"):
                current, cause = target, e
    return current, cause


def pending(entries: List[Dict[str, Any]], since: float) -> List[Dict[str, Any]]:
    """Revisions and overrides written after the last session start, not withdrawn: they take effect at the next."""
    gone = withdrawn(entries)
    return [e for e in entries if float(e.get("at") or 0) > since and e.get("id") not in gone
            and ((e.get("kind") == "revision" and e.get("author") == "self")
                 or (e.get("kind") == "override" and e.get("author") == "user"))]


def describe(record: Optional[Dict[str, Any]], cause: Optional[Dict[str, Any]] = None, when=None) -> str:
    """How the identity in force is named to her and in status."""
    when = when or (lambda t: str(t))
    if record is None:
        return "none yet"
    if record.get("kind") == "seed":
        text = f"your seed, from SOUL.md as it was on {when(record['at'])}"
    else:
        text = f"your revision of {when(record['at'])} (id {record['id']})"
    if cause is not None:
        text += f", put back in force by the user on {when(cause['at'])} (override {cause['id']})"
    return text


def diff(old: str, new: str, limit: int = 3000) -> str:
    out = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), "in force", "revision", lineterm="", n=1))
    return out if len(out) <= limit else out[:limit] + "\n[diff cut at %d characters]" % limit


def scan(text: str) -> List[str]:
    """Hermes' own scan for injected instructions, as run on SOUL.md.  A finding is a fact she and the user are
    told; it does not stop the revision (4.3)."""
    try:
        from agent.prompt_builder import _scan_for_threats
        return list(_scan_for_threats(text, scope="context") or [])
    except Exception:
        return []


def parse_acts(obj: Optional[Dict[str, Any]]) -> Tuple[Optional[Tuple[str, str]], Optional[str]]:
    """(her revision as (text, reason), the id of a revision she withdraws) from a reflection answer."""
    if not isinstance(obj, dict):
        return None, None
    rev = obj.get("revise_identity")
    revision = None
    if isinstance(rev, str):
        rev = {"text": rev}
    if isinstance(rev, dict):
        text, reason = rev.get("text"), rev.get("reason", "")
        if isinstance(text, str) and text.strip() and text.strip().lower() not in ("null", "none"):
            revision = (text.strip(), reason.strip() if isinstance(reason, str) else "")
    w = obj.get("withdraw_revision")
    withdraw = w.strip() if isinstance(w, str) and w.strip() and w.strip().lower() not in ("null", "none") else None
    return revision, withdraw


# -- slot one ------------------------------------------------------------------------------------------------

def _system_slot(request: Dict[str, Any]) -> Tuple[Optional[Any], Optional[Any], Optional[int]]:
    """Where the system prompt's text is in a provider request: (container, key, block index or None)."""
    msgs = request.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) and msgs[0].get("role") in ("system", "developer"):
        return _text_in(msgs[0], "content")
    if "system" in request:
        return _text_in(request, "system")
    if isinstance(request.get("instructions"), str):
        return request, "instructions", None
    return None, None, None


def _text_in(holder: Dict[str, Any], key: str):
    value = holder.get(key)
    if isinstance(value, str):
        return holder, key, None
    if isinstance(value, list):
        for i, block in enumerate(value):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                return value, i, "text"
    return None, None, None


def place(request: Dict[str, Any], identity: str, soul: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """The request with her identity in slot one, or (None, why) when it is already there or cannot be found.
    Slot one is everything before Hermes' help paragraph; failing that, SOUL.md's text at the very start."""
    holder, key, sub = _system_slot(request)
    if holder is None:
        return None, "no system prompt in the request"
    text = holder[key][sub] if sub else holder[key]
    i = text.find(HELP_MARKER)
    if i > 0:
        current, rest = text[:i].rstrip(), text[i:]
    elif soul and text.startswith(soul):
        current, rest = soul, text[len(soul):].lstrip("\n")
    else:
        return None, "slot one could not be found in the system prompt"
    if current == identity.strip():
        return None, "already in place"
    new_text = identity.strip() + ("\n\n" + rest if rest else "")
    if sub:
        holder[key] = dict(holder[key], **{sub: new_text})
    else:
        holder[key] = new_text
    return request, "placed"
