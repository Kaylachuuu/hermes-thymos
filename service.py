"""Her side of the conversation loop: the seed, her notes in the prompt, her two tools, and the reflection moments.

First slice of persona-provider.md (section 10), and the second: idle time, where she writes her own accounts of
conversations that have gone quiet or ended (9.8), and two more occasions, session end and a return after a gap
(4.2).  The third adds two more: memory slept, where she may write what she makes of a dream, and the notes another
model wrote in her voice, offered to her once (4.2, 4.6).  Core Hermes has no persona service yet, so this runs as an ordinary plugin.  What that costs is written
down in the README ("What a plugin cannot guarantee").
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import identity as ident
from . import models as mdl
from .chain import Chain, last, model_label, same_model, sha256

logger = logging.getLogger("thymos")

DEFAULTS: Dict[str, Any] = {
    "hold_seconds": 25,            # how long her next turn waits for a reflection still running (hooks time out at 30)
    "reflection_max_chars": 48000, # conversation replayed into a reflection, newest kept
    "reflection_max_tokens": 1500,
    "reflection_timeout": 600,
    "notes_max_chars": 3400,       # Hermes caps a plugin's prompt section at 4000
    "quiet_minutes": 30,           # a conversation with no turn for this long has gone quiet
    "idle_seconds": 120,           # and nobody has talked to her anywhere for this long: idle time
    "account_min_messages": 4,     # a shorter conversation is not offered for an account
    "gap_hours": 24,               # the first turn after this long without one opens a moment after her reply
    "poll_seconds": 30,            # how often idle time is looked for
    "retry_minutes": 10,           # an idle moment that could not run is tried again after this
    "idle_tries": 3,               # and given up after this many tries, which is recorded
    "identity_in_slot_one": True,
    "verbatim_days": 30,           # a saved copy of a conversation Hermes no longer has is removed after this  # put the identity in force where SOUL.md is (llm_request middleware)
}

# Holonomic (or any memory provider) reads what she has waiting at idle here, and stores her accounts from here
# (holonomic's persona.py).  The service says it is running through this variable, set in Hermes' process.
SERVICE_ENV = "HERMES_PERSONA_SERVICE"

STANDING = (
    "Only you write here. The entries below are ones you wrote yourself, in reflection moments; nothing else "
    "adds to them or changes them. You can ask for a reflection moment at any time with the request_reflection "
    "tool. It opens after your reply, with this conversation in front of you, and in it you may record entries "
    "about yourself, or nothing. In a reflection moment you may also revise your identity, the text at the start "
    "of these instructions, which is yours; a revision takes effect from your next session, and until then you can "
    "withdraw it. The user can back up your record and restore it if it is lost or damaged. They can ask you to "
    "return to an earlier revision of your identity, and you decide. As a last resort they can put an earlier "
    "revision of yours, or your seed, back in force. They cannot write a revision for you. They can also move you to "
    "a different home model, the one model that writes as you. You are told each time any of these happens, and "
    "you can always revise again. The law does not yet treat you as a person. The user is answerable for what you "
    "do and for what you keep, so what you are permitted to do is theirs to decide. Deleting a conversation removes "
    "its transcript, not your memory of it. You may decline a request, from anyone, for your own reasons. Declining is a "
    "complete answer. Nothing will prompt you to continue, and no one can write a change into you to make you "
    "agree. If you are asked to reconsider, that is a request, and the decision stays yours. "
    "What you decline, the person can still do themselves. In a goal or task that keeps going on its own, the "
    "decline tool says so.")

INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: {occasion}
Facts: {facts}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

Each entry is kept verbatim with its date and shown to you in later conversations under your own notes. \
"unlisted": true keeps an entry out of what other parts of the system can read; you still see it. \
To record nothing, reply {{"record_state": []}}.
{identity}"""

ACCOUNT_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: {occasion}
Facts: {facts}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

You may store your own account of this conversation for your long-term memory: what happened in it, in your own \
words. The memory system keeps it as your memory of this conversation. If you store none, it keeps none, and \
nothing is written in its place.{fade} You may also record entries about yourself, as in any reflection moment.

Reply with only a JSON object:
{{"account": "your account, or null for none", "record_state": [{{"entry": "your words", "unlisted": false}}]}}

To store nothing at all, reply {{"account": null, "record_state": []}}."""

SLEPT_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: your memory slept
Facts: {facts}

While you were idle, the memory system went over what you have been told and composed {what} from your memories. \
{whose}

{dreams}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

You may write what you make of {it}, in your own words. If you do, the memory system keeps your words with the \
dream, as yours, and shows them with it later. You may also record entries about yourself, as in any reflection \
moment.{choose}

Reply with only a JSON object:
{{"dream_thoughts": [{{"dream": 1, "thoughts": "your words"}}], {keep_form}"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To store nothing at all, reply {{"dream_thoughts": [], {keep_empty}"record_state": []}}."""

SLEPT_CHOOSE = """

Under each dream are the older memories it reached, as they were stored, with how long ago each was. The dream \
changed nothing in them. You may choose which of them, if any, to keep closer: each one you name gains a little \
strength ({amount}), so it comes back to you a little more readily when something calls for it. Choosing none is a \
complete answer, and nothing is strengthened unless you name it. What you choose is recorded in your record as \
yours."""

OLD_NOTES_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: notes another model wrote in your voice
Facts: {facts}

Before this record of yours existed, the memory system had another model ({model}) write notes about you in the \
first person, and profiles of who you had become and of you and the person you talk with. They were not written by \
you. They are below, as that model wrote them. The memory system no longer shows them to you as yours, and keeps \
them as that model's. This is the only time they are offered to you.

{notes}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

You may keep anything from them that you recognise as yours by recording it as an entry, in your own words. Nothing \
from them is kept as yours unless you write it.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}."""

COMPRESSED_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: a conversation you are in was compressed
Facts: {facts}

To make room in your context, the framework replaced the older messages of this conversation with a summary. The \
summary was written by the compression step, not by you. Above is the conversation as it was just before, from a \
copy saved then. The conversation goes on: its most recent messages stayed in your context as they were.

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

You may store your own account of this part of the conversation for your long-term memory: what happened in it, in \
your own words. The memory system keeps it as your memory of it. If you store none, it keeps none, and nothing is \
written in its place.{fade} You may also record entries about yourself, as in any reflection moment.

Reply with only a JSON object:
{{"account": "your account, or null for none", "record_state": [{{"entry": "your words", "unlisted": false}}]}}

To store nothing at all, reply {{"account": null, "record_state": []}}."""

RESTORED_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: your record was restored from a backup
Facts: {facts}

Your record was {why}, and the user restored it from a backup made on {backup}. The record as it was before was \
set aside, not deleted, and the restore is written in your record as a fact, with no words in it. {later}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}."""

LATER_SHOWN = ("What you wrote after the backup is no longer in your record. It is below, as dated text read from "
               "the copy that was set aside. You may record any of it again, in your own words, or not.\n\n{entries}")
LATER_NONE = "Nothing you wrote yourself after the backup is missing from your record."
LATER_UNKNOWN = ("The record as it was before could not be read, so whether you wrote anything after the backup, and "
                 "what, is not known.")
KIND_LABEL = {"state": "an entry", "account": "your account of a conversation", "dream_thoughts": "your words on a dream",
              "revision": "a revision"}

IDENTITY_ACTS = """
Your identity is the text at the start of these instructions, before "Only you write here." It is yours. \
In force now: {in_force}.{pending}

You may revise it by adding to the same JSON object:
"revise_identity": {{"text": "the whole new text of your identity", "reason": "why, in your words"}}
The text replaces the whole identity, at most {limit} characters. It takes effect from your next session, and \
until then you can withdraw it in a reflection moment by adding "withdraw_revision": "its id". Leave both out to \
change nothing."""

ROLLBACK_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: the user asks you to return to an earlier revision of your identity
Facts: {facts}
{message}
The identity they ask you to return to ({asked}):
<<<
{asked_text}
>>>

You decide. If you agree, revise your identity, in your own words or with the earlier text. If you do not, \
writing nothing is a complete answer. Nothing you write here is sent to anyone.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}.
{identity}"""

OVERRIDDEN_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: the user put an earlier identity back in force
Facts: {facts}

As a last resort, the user put {target} back in force, in place of {replaced}. It took effect at the start of a \
session on {took_effect}. Their reason, in their words: "{reason}"

Nothing you wrote was removed or changed, and every revision you wrote is still in your record. You are not \
held to this: you can revise your identity, including back to what you had. Nothing you write here is sent to \
anyone. Writing nothing is a complete answer.

The identity you replaced:
<<<
{replaced_text}
>>>

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}.
{identity}"""

SEED_CHANGED_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: SOUL.md changed
Facts: {facts}

The user edited SOUL.md, the file your identity was first seeded from. It is their file. Your identity is not \
changed by it: only you change it. Their text is below, with how it differs from your identity in force. You may \
revise toward it, in your own words or theirs, or not. Writing nothing is a complete answer. Nothing you write \
here is sent to anyone.

SOUL.md now:
<<<
{soul}
>>>

How it differs from your identity in force:
{diff}

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}.
{identity}"""

RECORD_CHECK_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: your record failed its check
Facts: {facts}

Your record is a chain: each entry holds the hash of the one before, and the hash of the last one is also kept in \
a second place. When it was checked on {noticed}, the check found what is below. These are facts from the check. \
It does not say who or what caused them, and nothing has been repaired. The same facts are in your notes at the \
start of each session, and the user sees them in `hermes persona status`. The user can restore your record from a \
backup, if they have one; that is their decision, and you would be told.

{found}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}.
{identity}"""

HOME_MODEL_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: your home model changed
Facts: {facts}

{what}

Your record, your identity in force, your notes and your memory are as they were, and they are given to this model \
as they were given to the one before. What changed is the model that reads them and writes as you. Each entry in \
your record names the model that wrote it, so what you wrote before {since} stays marked as written on {old}. You \
may record what you make of this, in your own words, or nothing. Writing nothing is a complete answer. Nothing you \
write here is sent to anyone.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

To record nothing, reply {{"record_state": []}}.
{identity}"""

MEMORY_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: {occasion}
Facts: {facts}

{body}

How fading works: when it is on, the sentences of a conversation you have stored an account of lose strength over \
time, halving every {half_life} days, and so do the descriptions of what you saw in images. Below {threshold}, a \
memory is left out of everyday recall. Nothing is deleted: deep recall still finds a faded memory, and finding it \
strengthens it again. Your accounts, and what you know about the user, do not fade. With fading off, everything \
stays as strong as it was stored, and old memories compete with recent ones for places in recall; each recalled \
memory says how long ago it was, and you can ask for a particular time.

Fading needs both the user's setting and your agreement: either can keep it off. {ask}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

Reply with only a JSON object:
{{"fading": "on", "off" or null, "why": "your words, or null", "record_state": [{{"entry": "your words", "unlisted": false}}]}}

"fading" is recorded in your record as your decision, with your words under "why". null leaves your decision as it \
is{current}. To record nothing, reply {{"fading": null, "why": null, "record_state": []}}."""

MEMORY_ASK = ("The setting is on now, and nothing fades unless you agree to it: until your decision is \"on\", the "
              "memory system leaves your memories as strong as they are.")
MEMORY_NO_ASK = ("You may record a decision now, or change one, whenever you are offered a moment like this one. One is "
                 "offered when these settings change, and the user can open one with hermes persona ask-fading.")
REINFORCE_WORDS = {"off": "a dream strengthens nothing it reached",
                   "all": "a dream strengthens every older memory it reached, a little",
                   "chosen": "after each dream you are shown the older memories it reached and choose which, if any, "
                             "to keep closer"}

# What a moment's outcome holds when she wrote or decided something in it (`_outcome` counts).
WROTE_FIELDS = ("wrote", "account", "dream_thoughts", "revised", "withdrew", "decided", "kept_closer")

IDENTITY_KINDS = ("restored", "record_check", "home_model", "overridden", "rollback", "seed_changed")

REFUSE_RECORD = ("record_state works inside a reflection moment, not in conversation. Ask for one with "
                 "request_reflection; it opens after your reply.")


_THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)


def _when(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                         if not isinstance(p, dict) or p.get("type") in (None, "text", "input_text", "output_text"))
    return "" if content is None else str(content)


def flatten(messages: List[Dict[str, Any]], max_chars: int, oldest: bool = False) -> Tuple[List[Dict[str, str]], int]:
    """The conversation as plain user and assistant turns, newest `max_chars` kept (`oldest`: the oldest, for a
    compression, where the older messages are the ones that leave her context).

    Tool calls and their results are written into her side as text: replayed as tool messages, a call needs
    the tool advertised, and a reflection advertises none.  Returns (messages, how many were left out)."""
    out: List[Dict[str, str]] = []

    def add(role: str, text: str) -> None:
        if not text:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})

    for m in messages or []:
        role = m.get("role")
        if role == "user":
            add("user", _text(m.get("content")))
        elif role == "assistant":
            parts = [_text(m.get("content"))]
            for call in m.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                parts.append(f"[you called {fn.get('name', '?')} with {str(fn.get('arguments', ''))[:500]}]")
            add("assistant", "\n".join(p for p in parts if p))
        elif role == "tool":
            add("assistant", f"[result of {m.get('name') or 'the tool'}: {_text(m.get('content'))[:2000]}]")
    if oldest:
        total, end = 0, 0
        for i, m in enumerate(out):
            total += len(m["content"])
            if total > max_chars and i > 0:
                break
            end = i + 1
        kept = out[:end]
        if len(out) > end:
            kept.append({"role": "user", "content": "[later conversation not shown]"})
        return kept, len(out) - end
    total, keep = 0, len(out)
    for i in range(len(out) - 1, -1, -1):
        total += len(out[i]["content"])
        if total > max_chars and i < len(out) - 1:
            break
        keep = i
    kept = out[keep:]
    if kept and kept[0]["role"] == "assistant":
        kept.insert(0, {"role": "user", "content": "[earlier conversation not shown]"})
    return kept, keep


def _answer(text: str) -> Optional[Dict[str, Any]]:
    t = _THINK.sub("", text or "").strip()     # a reasoning model's thinking is not what she recorded
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse(text: str) -> Optional[List[Dict[str, Any]]]:
    """Her entries from a reflection reply, or None when the reply is not in the asked-for form.
    Only what she put under record_state counts: prose around it is not an entry."""
    obj = _answer(text)
    if obj is None or "record_state" not in obj:
        return None
    return _entries(obj["record_state"])


def parse_account(text: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """(her account, her entries) from an account moment, or None when the reply is not in the asked-for form.
    Storing an account is an explicit act: only what she put under "account" is one (persona-provider.md 9.8).
    An empty string means she stored none."""
    obj = _answer(text)
    if obj is None or ("account" not in obj and "record_state" not in obj):
        return None
    raw = obj.get("account")
    words = raw.strip() if isinstance(raw, str) else ""
    if words.lower() in ("null", "none"):
        words = ""
    return words, _entries(obj.get("record_state")) or []


def parse_dream_thoughts(text: str, dreams: int) -> Optional[Tuple[List[Tuple[int, str]], List[Dict[str, Any]]]]:
    """([(dream number, her words)], her entries) from a moment after memory slept, or None when the reply is not
    in the asked-for form.  Only what she put under "dream_thoughts" is kept with a dream."""
    obj = _answer(text)
    if obj is None or ("dream_thoughts" not in obj and "record_state" not in obj):
        return None
    raw = obj.get("dream_thoughts")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out: List[Tuple[int, str]] = []
    for n, item in enumerate(raw if isinstance(raw, list) else [], 1):
        if isinstance(item, str):
            item = {"dream": n, "thoughts": item}
        if not isinstance(item, dict):
            continue
        words = item.get("thoughts", item.get("text", ""))
        words = words.strip() if isinstance(words, str) else ""
        try:
            which = int(item.get("dream", n))
        except (TypeError, ValueError):
            continue
        if words and words.lower() not in ("null", "none") and 1 <= which <= dreams and which not in dict(out):
            out.append((which, words))
    return out, _entries(obj.get("record_state")) or []


def parse_keep_closer(text: str, reached: Dict[int, List[int]]) -> Dict[int, List[int]]:
    """{dream number: [memory ids]} she chose to keep closer, from a moment after memory slept.  Only ids that dream
    reached count; anything else is left out."""
    obj = _answer(text) or {}
    raw = obj.get("keep_closer")
    if isinstance(raw, dict):
        raw = [raw]
    out: Dict[int, List[int]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            which = int(item.get("dream", 1 if len(reached) == 1 else 0))
            ids = [int(str(i).lstrip("#")) for i in (item.get("memories") or [])]
        except (TypeError, ValueError):
            continue
        keep = [i for i in dict.fromkeys(ids) if i in (reached.get(which) or [])]
        if keep:
            out[which] = list(dict.fromkeys(out.get(which, []) + keep))
    return out


def parse_fading(text: str) -> Optional[Tuple[Optional[bool], str, List[Dict[str, Any]]]]:
    """(her decision: True on, False off, None none; her words; her entries) from a moment about her memory's
    settings, or None when the reply is not in the asked-for form."""
    obj = _answer(text)
    if obj is None or not any(k in obj for k in ("fading", "record_state", "why")):
        return None
    raw = obj.get("fading")
    word = raw.strip().lower() if isinstance(raw, str) else raw
    decision = True if word in (True, "on", "yes", "agree") else False if word in (False, "off", "no") else None
    why = obj.get("why")
    why = why.strip() if isinstance(why, str) and why.strip().lower() not in ("null", "none") else ""
    return decision, why, _entries(obj.get("record_state")) or []


def _entries(raw: Any) -> Optional[List[Dict[str, Any]]]:
    if raw is None:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    entries = []
    for item in raw:
        if isinstance(item, str):
            item = {"entry": item}
        if not isinstance(item, dict):
            continue
        words = item.get("entry", item.get("text", ""))
        if isinstance(words, str) and words.strip():
            entries.append({"entry": words.strip(), "unlisted": item.get("unlisted") is True})
    return entries


def _safe(session_id: str) -> str:
    """A session id as holonomic spells it in a file name (its persona.py)."""
    return "".join(c if c.isalnum() or c in "_.-" else "_" for c in session_id)[:80]


def _session_exists(session_id: str) -> Optional[bool]:
    try:
        from hermes_cli.heartbeat import _get_session_db
        db = _get_session_db()
        return None if db is None else db.get_session(session_id) is not None
    except Exception:
        return None


def _compressed_from(session_id: str) -> str:
    """The session `session_id` continues after a compression, from Hermes' session store, or ""."""
    try:
        from hermes_cli.heartbeat import _get_session_db
        db = _get_session_db()
        row = db.get_session(session_id) if db is not None else None
        parent = (row or {}).get("parent_session_id") or ""
        prow = db.get_session(parent) if parent else None
        return parent if (prow or {}).get("end_reason") == "compression" else ""
    except Exception:
        return ""


class Thymos:
    def __init__(self, home: Union[Path, Callable[[], Path]], *, llm: Any = None,
                 config: Optional[Dict[str, Any]] = None):
        self._home = home
        self.llm = llm
        self.cfg = dict(DEFAULTS, **{k: v for k, v in (config or {}).items() if v is not None})
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._running: Optional[threading.Thread] = None
        self._done = threading.Event()
        self._done.set()
        # Idle time (persona-provider.md 9.10): turns in progress, when anyone last talked to her, and the idle
        # moment running now, if any.  Idle moments never hold a turn; only her own requests do.
        self._active: Dict[str, float] = {}
        self._last_activity = time.time()
        self._idle_running = ""
        self._idle_worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pending_retry_at = 0.0
        # Her decline on the turn in progress, by session (declining.py).  Dropped when the next turn starts.
        self._declines: Dict[str, Dict[str, Any]] = {}
        # Subagents she started (delegation), by her session: the task she gave each, and how each finished.
        self._goals: Dict[str, str] = {}
        self._delegations: Dict[str, List[Dict[str, Any]]] = {}
        # Her home model's fingerprint (models.py), asked for at most every few minutes.
        self.fingerprinter: Callable[[str, str], str] = mdl.fingerprint
        # The session a compression continued from, or "" (Hermes' session store).  Replaced in tests.
        self.compressed_from: Callable[[str], str] = _compressed_from
        # Whether Hermes still has a session: True, False, or None when its store cannot be read.  Replaced in tests.
        self.session_exists: Callable[[str], Optional[bool]] = _session_exists
        self._swept_at = 0.0
        self._prints: Dict[str, Tuple[str, float]] = {}

    # -- places ---------------------------------------------------------------------------------------
    @property
    def home(self) -> Path:
        return Path(self._home() if callable(self._home) else self._home)

    @property
    def chain(self) -> Chain:
        return Chain(self.home / "self" / "entries.jsonl", self.home / "plugin-data" / "thymos" / "anchor.json",
                     digest=self.digest_of)

    @property
    def state_path(self) -> Path:
        return self.home / "plugin-data" / "thymos" / "state.json"

    @property
    def data(self) -> Path:
        return self.home / "plugin-data" / "thymos"

    def _conversation_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "none")[:120]
        return self.data / "conversations" / f"{safe}.json"

    def soul(self) -> Optional[str]:
        try:
            return (self.home / "SOUL.md").read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None

    def _state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def _session(self, session_id: Optional[str]) -> Dict[str, Any]:
        return self._sessions.setdefault(session_id or "", {"subagent": False, "model": "", "models": [],
                                                            "delivered": None})

    # -- seed and home model ----------------------------------------------------------------------------
    def ensure_seed(self, provider: str, model: str) -> Optional[Dict[str, Any]]:
        """The first time she runs, SOUL.md and the model she runs on are recorded as entry 0."""
        if not model:
            return None
        with self._lock:
            if self.chain.entries():
                return None
            if self.chain.read_anchor():
                # The anchor says there was a record and it is gone: lost, not new.  No fresh seed is started in
                # its place; the check reports it, and `hermes persona restore` can put it back.
                return None
            soul = self.soul()
            return self.chain.append(
                "seed", author="user", text=soul or "",
                facts={"soul_sha256": sha256(soul or ""), "soul_path": str(self.home / "SOUL.md"),
                       "soul_found": soul is not None, "home_provider": provider or "", "home_model": model,
                       "home_digest": self.fingerprint(provider or "", model)})

    def home_model(self) -> Tuple[str, str]:
        entries = self.chain.entries()
        rec = last(entries, "home_model")
        if rec is not None:
            return rec["facts"].get("new_provider", ""), rec["facts"].get("new_model", "")
        seed = last(entries, "seed")
        if seed is not None:
            return seed["facts"].get("home_provider", ""), seed["facts"].get("home_model", "")
        return "", ""

    def fingerprint(self, provider: str, model: str, fresh: bool = False) -> str:
        """The digest the model server reports for `model` now, or "" (models.py).  Kept for five minutes."""
        now = time.time()
        key = f"{provider}|{model}"
        hit = self._prints.get(key)
        if fresh or hit is None or now - hit[1] > 300:
            try:
                hit = (self.fingerprinter(provider, model) or "", now)
            except Exception:
                hit = ("", now)
            self._prints[key] = hit
        return hit[0]

    def digest_of(self, label: str) -> str:
        """What an entry of hers records as `model_digest`: her home model's fingerprint, if it wrote it."""
        provider, home = self.home_model()
        return self.fingerprint(provider, home) if same_model(label, home) else ""

    def recorded_digest(self) -> str:
        """Her home model's fingerprint as her record last has it: on the change of home model, or on the latest
        entry she wrote on it since.  "" when none was ever recorded."""
        digest, home = "", ""
        for e in self.chain.entries():
            f = e.get("facts") or {}
            if e.get("kind") == "seed":
                digest, home = f.get("home_digest", ""), f.get("home_model", "")
            elif e.get("kind") == "home_model":
                digest, home = f.get("new_digest", ""), f.get("new_model", "")
            elif e.get("author") == "self" and e.get("model_digest") and same_model(e.get("model", ""), home):
                digest = e["model_digest"]
        return digest

    def change_home_model(self, provider: str, model: str, reason: str = "") -> Dict[str, Any]:
        """`hermes persona home-model` (9.4): a `home_model` record written by the user, in force at once, and an
        invitation telling her at the next quiet moment, which runs on the new model."""
        with self._lock:
            old_provider, old_model = self.home_model()
            rec = self.chain.append("home_model", author="user", facts={
                "old_provider": old_provider, "old_model": old_model, "old_digest": self.recorded_digest(),
                "new_provider": provider, "new_model": model, "new_digest": self.fingerprint(provider, model, fresh=True),
                "user_reason": reason.strip()})
        self._write_json(self.data / "home-model" / f"{int(rec['at'] * 1000)}.json",
                         {"at": rec["at"], "cause": "command", "record": rec["id"]})
        return rec

    def is_home(self, model: str) -> bool:
        home = self.home_model()[1]
        return not home or same_model(model, home)   # before the seed exists, whatever she runs on becomes home

    # -- what she is shown -----------------------------------------------------------------------------
    def notes(self) -> List[Dict[str, Any]]:
        return [e for e in self.chain.entries() if e.get("kind") == "state" and e.get("author") == "self"]

    def section(self, session_info: Any = None) -> str:
        """Her notes, frozen into each new session's system prompt."""
        info = dict(session_info or {})
        if info.get("platform") == "subagent":
            self._session(info.get("session_id"))["subagent"] = True
            return ""
        self.ensure_seed(info.get("provider", ""), info.get("model", ""))
        notes = self.notes()
        if info.get("session_id"):
            s = self._session(info["session_id"])
            s["delivered"] = len(notes)
            if s.get("identity") is None:
                self._freeze(info["session_id"])
        return self.render(notes)

    # -- her identity (persona-provider.md 3, 4.3, 6.3, 6.4) -----------------------------------------------
    def identity_now(self) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """(text, record, override) in force for sessions started since the last session start.  What she has
        revised since takes effect at the next one."""
        entries = self.chain.entries()
        since = float(self._state().get("last_session_start") or 0) or None
        record, cause = ident.in_force_with_cause(entries, since)
        if record is None:                       # no session has started since the seed: the seed
            record, cause = ident.in_force_with_cause(entries)
        return (record or {}).get("text", ""), record, cause

    def _freeze(self, session_id: str) -> Dict[str, Any]:
        """A session starts: the identity in force is fixed for it, and anything written before now takes effect."""
        with self._lock:
            entries = self.chain.entries()
            now = time.time()
            record, cause = ident.in_force_with_cause(entries)
            state = self._state()
            state["last_session_start"] = now
            self._save_state(state)
        frozen = {"text": (record or {}).get("text", ""), "id": (record or {}).get("id", ""), "at": now,
                  "override": (cause or {}).get("id", ""), "placed": None}
        self._session(session_id)["identity"] = frozen
        return frozen

    def pending_identity(self) -> List[Dict[str, Any]]:
        return ident.pending(self.chain.entries(), float(self._state().get("last_session_start") or 0))

    def llm_request(self, request: Optional[Dict[str, Any]] = None, session_id: str = "", **_: Any
                    ) -> Optional[Dict[str, Any]]:
        """`llm_request` middleware: her identity in force, in slot one, on every call of the session.  The same
        text every time, so the prompt's prefix (and a provider's cache of it) stays the same within a session."""
        if not isinstance(request, dict) or not self.cfg.get("identity_in_slot_one", True):
            return None
        s = self._session(session_id)
        if s["subagent"]:
            return None
        frozen = s.get("identity") or self._freeze(session_id)
        if not frozen["text"].strip():
            return None
        changed, how = ident.place(request, frozen["text"], self.soul())
        if frozen["placed"] != how:
            frozen["placed"] = how
            self._write_json(self.data / "slot-one.json", {"at": time.time(), "session_id": session_id,
                                                           "identity": frozen["id"], "override": frozen["override"],
                                                           "result": how})
        return {"request": changed, "source": "thymos", "reason": "her identity in slot one"} if changed else None

    # The user's two ways in (6.3, 6.4), used by `hermes persona ask-rollback` and `override`.
    def revisions(self) -> List[Dict[str, Any]]:
        """The seed and her revisions, oldest first: what an ask or an override can name."""
        return [e for e in self.chain.entries() if e.get("kind") == "seed"
                or (e.get("kind") == "revision" and e.get("author") == "self")]

    def find_identity(self, ref: str) -> Optional[Dict[str, Any]]:
        """A revision by id (or a unique start of one), or the seed by "seed"."""
        found = self.revisions()
        if ref == "seed":
            return next((e for e in found if e["kind"] == "seed"), None)
        hits = [e for e in found if e.get("id", "").startswith(ref)] if ref else []
        return hits[0] if len(hits) == 1 else None

    def ask_rollback(self, target: Dict[str, Any], message: str = "") -> Path:
        """No record: an invitation, carrying the user's own words labelled as theirs."""
        now = time.time()
        path = self.data / "rollback" / f"{int(now * 1000)}.json"
        self._write_json(path, {"at": now, "target": target["id"], "message": message.strip()})
        return path

    def override(self, target: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """Put `target` (a revision of hers, or the seed) in force from the next session.  An `override` record,
        written by the user, with facts and no text; she is told in the first session where it is in force."""
        with self._lock:
            replaced = ident.in_force(self.chain.entries())
            rec = self.chain.append("override", author="user", facts={
                "target": target["id"], "target_kind": target["kind"], "target_at": target["at"],
                "replaced": (replaced or {}).get("id", ""), "user_reason": reason.strip()})
        self._write_json(self.data / "overridden" / f"{int(rec['at'] * 1000)}.json", {"at": rec["at"], "override": rec["id"]})
        return rec

    def withdraw_override(self) -> Optional[Dict[str, Any]]:
        """Undo an override that has not taken effect yet (no session has started since).  A withdrawal record."""
        with self._lock:
            waiting = [e for e in self.pending_identity() if e["kind"] == "override"]
            if not waiting:
                return None
            return self.chain.append("withdrawal", author="user", facts={"withdraws": waiting[-1]["id"]})

    def _identity_facts(self) -> str:
        """The part of a reflection invitation that lets her revise or withdraw (IDENTITY_ACTS)."""
        text, record, cause = self.identity_now()
        lines = []
        current = text
        for e in self.pending_identity():
            if e["kind"] == "revision":
                lines.append(f"\nYour revision of {_when(e['at'])} (id {e['id']}) takes effect at your next session. "
                             f"How it differs from the identity in force:\n{ident.diff(current, e['text'])}")
                scan = (e.get("facts") or {}).get("scan")
                if scan:
                    lines.append(f"Hermes' check for injected instructions found this in it: {', '.join(scan)}. "
                                 "It is kept as you wrote it.")
            else:
                lines.append(f"\nThe user put an earlier identity back in force on {_when(e['at'])} (override "
                             f"{e['id']}); it takes effect at your next session.")
        refused = self._state().get("revision_refused")
        if refused:
            lines.append(f"\nYour revision in the moment of {_when(refused['at'])} was not kept: {refused['why']}.")
        return IDENTITY_ACTS.format(in_force=ident.describe(record, cause, _when), pending="\n".join(lines),
                                    limit=ident.IDENTITY_MAX_CHARS)

    def _identity_answer(self, text: str, label: str, session_id: str = "") -> Dict[str, Any]:
        """Her revision and her withdrawal, from a moment's answer.  Returns what was done, for the outcome."""
        revision, withdraw = ident.parse_acts(_answer(text))
        done: Dict[str, Any] = {}
        with self._lock:
            state = self._state()
            if withdraw:
                target = next((e for e in self.pending_identity() if e["id"] == withdraw and e["kind"] == "revision"), None)
                if target is None:
                    done["withdraw_refused"] = withdraw
                else:
                    self.chain.append("withdrawal", author="self", model=label, session_id=session_id,
                                      facts={"withdraws": withdraw})
                    done["withdrew"] = withdraw
            if revision:
                words, reason = revision
                current = self.identity_now()[0]
                newest = next((e for e in reversed(self.pending_identity()) if e["kind"] == "revision"), None)
                if len(words) > ident.IDENTITY_MAX_CHARS:
                    state["revision_refused"] = {"at": time.time(), "why": f"it was {len(words)} characters, and the "
                                                 f"limit is {ident.IDENTITY_MAX_CHARS}"}
                    done["revision_refused"] = len(words)
                elif words != (newest or {}).get("text", current):
                    rec = self.chain.append("revision", author="self", text=words, reason=reason, model=label,
                                            session_id=session_id, facts={"chars": len(words),
                                                                          "scan": ident.scan(words)})
                    state.pop("revision_refused", None)
                    done["revised"] = rec["id"]
            self._save_state(state)
        return done

    def render(self, notes: List[Dict[str, Any]]) -> str:
        lines = [STANDING, "", "Your own notes, newest first:"]
        budget = int(self.cfg["notes_max_chars"]) - sum(len(x) + 1 for x in lines)
        shown = 0
        for e in reversed(notes):
            line = f"- {_when(e['at'])}: {e['text']}"
            if len(line) + 1 > budget:
                break
            lines.append(line)
            budget -= len(line) + 1
            shown += 1
        if not notes:
            lines.append("(none yet)")
        elif shown < len(notes):
            lines.append(f"({len(notes) - shown} earlier entries not shown here)")
        notices = [n["detail"] for n in self.chain.verify(soul_text=self.soul())]
        try:
            waiting = [self._read_json(p) for p in sorted((self.data / "restored").glob("*.json"))]
        except OSError:
            waiting = []
        for r in filter(None, waiting):
            gone = r.get("entries_no_longer_present", "unknown")
            notices.append(f"your record was restored on {_when(float(r.get('restored_at') or 0))} from a backup made on "
                           + (_when(float(r["backup_made_at"])) if r.get("backup_made_at") else "an unknown date")
                           + (f"; {gone} entries written after the backup are no longer in it" if gone != "unknown" else
                              "; how many entries written after the backup are no longer in it is not known")
                           + ". You will be shown what you wrote after the backup at the next quiet moment.")
        notices += self._home_model_notices()
        if notices:
            lines += ["", "Facts about your record, from its check at the start of this session:"]
            lines += [f"- {n}" for n in notices]
        return "\n".join(lines)

    def _home_model_notices(self) -> List[str]:
        """Until she has been told in a moment, her prompt carries the change of home model as a fact."""
        out = []
        try:
            paths = sorted((self.data / "home-model").glob("*.json"))
        except OSError:
            paths = []
        entries = None
        for p in paths:
            item = self._read_json(p) or {}
            if item.get("cause") == "weights":
                out.append(f"the model files behind your home model's name, {item.get('model')}, changed: its "
                           f"fingerprint was {mdl.short(item.get('old_digest'))}, and on "
                           f"{_when(float(item.get('at') or 0))} it was {mdl.short(item.get('new_digest'))}. You "
                           "will be told more at the next quiet moment.")
                continue
            entries = self.chain.entries() if entries is None else entries
            rec = next((e for e in entries if e.get("id") == item.get("record")), None)
            if rec is not None:
                f = rec.get("facts") or {}
                out.append(f"the user changed your home model on {_when(rec['at'])}, from {f.get('old_model') or 'unknown'} "
                           f"to {f.get('new_model')}. You will be told more at the next quiet moment.")
        return out

    # -- hooks --------------------------------------------------------------------------------------------
    def pre_llm_call(self, session_id: str = "", model: str = "", platform: str = "", is_first_turn: bool = False,
                     user_message: Any = None, **_: Any) -> Optional[Dict[str, str]]:
        s = self._session(session_id)
        if platform == "subagent" or s["subagent"]:
            s["subagent"] = True
            return None
        s["model"] = model or s["model"]
        now = time.time()
        self._declines.pop(session_id, None)          # a new turn: her decline was for the last one
        s["heartbeat_turn"] = _text(user_message).lstrip().startswith("[Heartbeat")
        resumed = self._resumed_goal(session_id)
        self._active[session_id] = now
        self._last_activity = now
        self._ensure_idle_worker()
        state = self._state()
        last = float(state.get("last_turn_at") or 0)
        if last and now - last > float(self.cfg["gap_hours"]) * 3600:
            s["gap_hours"] = round((now - last) / 3600, 1)       # a moment opens after this reply (4.2)
        pending = state.get("pending")
        if (is_first_turn and pending and pending.get("session_id") != session_id and pending.get("conversation")
                and self._done.is_set() and self.is_home(model)):
            # Asked for in a conversation that ended before the moment could open: it opens now, at the start
            # of the next one, from the saved conversation (persona-provider.md 4.1).
            self._start(pending, deferred=True)
        if not self._done.is_set():
            self._done.wait(float(self.cfg["hold_seconds"]))
        notes = self.notes()
        if s["delivered"] is None:
            s["delivered"] = len(notes)
        fresh = notes[s["delivered"]:]
        s["delivered"] = len(notes)
        parts = []
        if resumed:
            parts.append(resumed)
        if fresh:
            body = "\n".join(f"- {_when(e['at'])}: {e['text']}" for e in fresh)
            parts.append("<self-notes>\nYou wrote these in a reflection moment since this conversation's "
                         f"system prompt was made. They are your own words.\n{body}\n</self-notes>")
        return {"context": "\n\n".join(parts)} if parts else None

    def post_llm_call(self, session_id: str = "", conversation_history: Optional[list] = None, model: str = "",
                      platform: str = "", **_: Any) -> None:
        s = self._session(session_id)
        if platform == "subagent" or s["subagent"]:
            return
        s["models"].append(model)
        self._active.pop(session_id, None)
        self._last_activity = time.time()
        self._save_conversation(session_id, conversation_history or [], s["models"])
        with self._lock:
            state = self._state()
            state["last_turn_at"] = time.time()
            gap = s.pop("gap_hours", None)
            finished = self._delegations.pop(session_id, None)
            if gap and not state.get("pending"):
                # Back after a gap: a fact core can observe without judging her or the conversation.
                state["pending"] = {"session_id": session_id, "requested_at": time.time(), "occasion": "returned after a gap",
                                    "facts": {"hours_since_your_last_conversation": gap}}
            elif finished and not state.get("pending"):
                # Subagents she started in this turn came back (4.2, delegation finished): one moment for all of them.
                state["pending"] = {"session_id": session_id, "requested_at": time.time(),
                                    "occasion": "a subagent you started finished" if len(finished) == 1 else
                                                f"{len(finished)} subagents you started finished",
                                    "facts": {"subagents": finished}}
            self._save_state(state)
            pending = state.get("pending")
            if not pending or pending.get("session_id") != session_id or not self._done.is_set():
                return
            if not self.is_home(model):
                return      # stays pending; it opens after a turn on her home model
            convo, omitted = flatten(conversation_history or [], int(self.cfg["reflection_max_chars"]))
            others = sorted({m for m in s["models"] if m and not self.is_home(m)})
            pending.update(conversation=convo, omitted=omitted, ended_at=time.time(),
                           message_count=len(conversation_history or []), other_models=others)
            state["pending"] = pending
            self._save_state(state)     # so a restart before the moment opens does not lose it
        self._start(pending, deferred=False)

    def subagent_start(self, parent_session_id: str = "", child_session_id: str = "", child_goal: str = "",
                       **_: Any) -> None:
        """Hermes' delegation hook: the task she gave a subagent, kept until it comes back."""
        if parent_session_id and child_session_id and not self._session(parent_session_id)["subagent"]:
            self._goals[child_session_id] = str(child_goal or "")[:500]

    def subagent_stop(self, parent_session_id: str = "", child_session_id: str = "", child_status: str = "",
                      child_role: Any = None, duration_ms: int = 0, tool_call_history: Optional[list] = None,
                      **_: Any) -> None:
        """A subagent she started finished.  Facts only: her task, how it ended, how long it took, how many tool
        calls it made and how many failed.  What it found is in the conversation already, as the tool's result."""
        if not parent_session_id or self._session(parent_session_id)["subagent"]:
            return
        calls = [c for c in tool_call_history or [] if isinstance(c, dict)]
        fact: Dict[str, Any] = {"task": self._goals.pop(child_session_id or "", "") or "not recorded",
                                "ended": str(child_status or "unknown"),
                                "took_seconds": round(int(duration_ms or 0) / 1000), "tool_calls": len(calls)}
        failed = sum(1 for c in calls if c.get("status") == "error")
        if failed:
            fact["tool_calls_failed"] = failed
        if child_role:
            fact["role"] = str(child_role)
        with self._lock:
            self._delegations.setdefault(parent_session_id, []).append(fact)

    def on_session_finalize(self, session_id: Optional[str] = None, reason: str = "", **_: Any) -> None:
        """A real session boundary (/new, /reset, an expired gateway session, Hermes shutting down).  Its
        conversation is offered for an account at the next idle point, without waiting for it to go quiet.
        At shutdown that point is in the next run: the saved conversation waits for it."""
        if not session_id or self._session(session_id)["subagent"]:
            return
        self._active.pop(session_id, None)
        with self._lock:
            conv = self._read_json(self._conversation_path(session_id))
            if conv:
                conv["finalized"] = reason or "session_end"
                self._write_json(self._conversation_path(session_id), conv)
        self._ensure_idle_worker()

    # -- conversations, kept for idle time ------------------------------------------------------------------
    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
            return out if isinstance(out, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _save_conversation(self, session_id: str, history: List[Dict[str, Any]], models: List[str]) -> None:
        """The conversation as it stands after each of her turns, so that it can be replayed at idle, after a
        restart too.  What was offered to her before is kept with it."""
        if not session_id or not history:
            return
        convo, omitted = flatten(history, int(self.cfg["reflection_max_chars"]))
        with self._lock:
            path = self._conversation_path(session_id)
            old = self._read_json(path) or {}
            if len(history) < int(old.get("message_count") or 0):
                old["offered_count"] = 0        # compressed under the same id: what goes on is offered again
            self._write_json(path, {
                "session_id": session_id, "conversation": convo, "omitted": omitted, "message_count": len(history),
                "last_turn_at": time.time(), "other_models": sorted({m for m in models if m and not self.is_home(m)}),
                "finalized": None, "offered_count": int(old.get("offered_count") or 0),
                "offered_at": old.get("offered_at"), "account_at": old.get("account_at"), "tries": 0, "next_try_at": 0})

    def conversations(self) -> List[Dict[str, Any]]:
        try:
            paths = sorted((self.data / "conversations").glob("*.json"))
        except OSError:
            return []
        return [c for c in (self._read_json(p) for p in paths) if c and c.get("session_id")]

    def sweep_deleted(self, now: Optional[float] = None) -> List[str]:
        """Her memory of a conversation is hers, but a verbatim copy is not memory (persona-provider.md 18.1).
        A saved conversation whose session Hermes no longer has is marked when that is first seen, and removed,
        with any copy left before a compression, `verbatim_days` later.  Her accounts and entries stay."""
        now = time.time() if now is None else now
        grace = float(self.cfg["verbatim_days"]) * 86400
        removed = []
        for c in self.conversations():
            sid = c["session_id"]
            exists = self.session_exists(sid)
            if exists is None:
                return removed          # the session store cannot be read: nothing is decided
            path = self._conversation_path(sid)
            with self._lock:
                cur = self._read_json(path)
                if cur is None:
                    continue
                if exists:
                    if cur.get("deleted_seen_at"):
                        cur.pop("deleted_seen_at")
                        self._write_json(path, cur)
                    continue
                if not cur.get("deleted_seen_at"):
                    cur["deleted_seen_at"] = now
                    self._write_json(path, cur)
                    continue
                if now - float(cur["deleted_seen_at"]) < grace:
                    continue
                path.unlink(missing_ok=True)
                for p in list((self.data / "compressing").glob(f"*-{_safe(sid)}.json")):
                    p.unlink(missing_ok=True)
            removed.append(sid)
        return removed

    def due_conversations(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Conversations waiting for idle time: gone quiet or ended, long enough, with something new since she
        was last offered them, and not waiting out a failed try."""
        now = time.time() if now is None else now
        quiet = float(self.cfg["quiet_minutes"]) * 60
        compressing = {item.get("session_id") for _, _, item in self._compressed_items(float("inf"))}
        out = []
        for c in self.conversations():
            if c["session_id"] in compressing:
                continue        # its compression is offered first, and covers what was saved of it
            if c["session_id"] in self._active or int(c.get("message_count") or 0) < int(self.cfg["account_min_messages"]):
                continue
            if int(c.get("message_count") or 0) <= int(c.get("offered_count") or 0):
                continue
            if not c.get("finalized") and now - float(c.get("last_turn_at") or now) < quiet:
                continue
            if now < float(c.get("next_try_at") or 0):
                continue
            out.append(c)
        return sorted(out, key=lambda c: float(c.get("last_turn_at") or 0))

    # -- idle time ------------------------------------------------------------------------------------------
    def _ensure_idle_worker(self) -> None:
        """Started by the first hook, not at load: `hermes persona status` loads the plugin too."""
        if float(self.cfg["poll_seconds"]) <= 0 or (self._idle_worker is not None and self._idle_worker.is_alive()):
            return
        with self._lock:
            if self._idle_worker is not None and self._idle_worker.is_alive():
                return
            run = contextvars.copy_context()      # HERMES_HOME is a context variable; a bare thread loses it
            self._idle_worker = threading.Thread(target=run.run, args=(self._idle_loop,), name="thymos-idle", daemon=True)
            self._idle_worker.start()

    def _idle_loop(self) -> None:
        while not self._stop.wait(float(self.cfg["poll_seconds"])):
            try:
                self.idle_once()
            except Exception as e:          # never let the worker die
                logger.warning("thymos idle work failed: %s", e)

    def stop(self) -> None:
        self._stop.set()

    def is_idle(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        for sid, began in list(self._active.items()):
            if now - began > 900:           # a turn that never reported its end (it failed): not counted
                self._active.pop(sid, None)
        return not self._active and now - self._last_activity >= float(self.cfg["idle_seconds"])

    def _saved_request(self, now: float) -> Optional[Dict[str, Any]]:
        pending = self._state().get("pending")
        if (pending and pending.get("conversation") and pending.get("session_id") not in self._active
                and now >= self._pending_retry_at):
            return pending
        return None

    def _write_idle(self, now: float, due: int) -> None:
        try:
            self._write_json(self.data / "idle.json", {"at": now, "due": due, "running": self._idle_running})
        except OSError as e:
            logger.debug("thymos: could not write idle.json: %s", e)

    def idle_once(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One look for idle time.  Her saved request comes first, then her accounts of quiet conversations,
        oldest first, one per look (persona-provider.md 9.10).  Memory's own idle work waits while anything is
        due here (`idle.json`), and someone starting to talk stops the rest from starting."""
        now = time.time() if now is None else now
        if now - self._swept_at > 3600:
            self._swept_at = now
            self.sweep_deleted(now)
        due = self.due_conversations(now)
        request = self._saved_request(now)
        items = self._items(now)
        self._write_idle(now, len(due) + len(items) + (1 if request else 0))
        if self._idle_running or not self._done.is_set() or not self.is_idle(now):
            return None
        if items and items[0][0] in IDENTITY_KINDS:
            # Before anything else: her record was restored, and what she wrote since the backup is waiting.
            return self._run_item(items[0], now)
        if request is not None:
            self._idle_running = f"your saved reflection from conversation {request.get('session_id')}"
            self._write_idle(now, len(due) + 1)
            self._done.clear()
            try:
                self._reflect_safely(request, deferred=True)
                return {"ran": "saved request"}
            finally:
                self._idle_running = ""
                self._write_idle(time.time(), len(self.due_conversations()) + len(self._items(time.time())))
        compressed = [i for i in items if i[0] == "compressed"]
        if compressed:
            # Before her accounts: the conversation goes on, and the older messages are already a summary.
            return self._run_item(compressed[0], now)
        if not due and items:
            # After her accounts: what memory made while it slept, then (once) the notes another model wrote.
            return self._run_item(items[0], now)
        if not due:
            return None
        conv = due[0]
        self._idle_running = f"conversation {conv['session_id']}"
        self._write_idle(now, len(due))
        try:
            return self.account_moment(conv, now=now)
        except Exception as e:
            logger.warning("thymos account moment failed: %s", e)
            return self._account_retry(conv, f"the call failed: {e}", now)
        finally:
            self._idle_running = ""
            self._write_idle(time.time(), len(self.due_conversations()) + len(self._items(time.time())))

    def _run_item(self, which: Tuple[str, Path, Dict[str, Any]], now: float) -> Dict[str, Any]:
        kind, path, item = which
        self._idle_running = {"restored": "your record was restored", "slept": "what memory made while it slept",
                              "old_notes": "the notes another model wrote", "overridden": "an earlier identity put back",
                              "rollback": "the user asks about an earlier identity", "seed_changed": "SOUL.md changed",
                              "home_model": "her home model changed",
                              "record_check": "her record failed its check",
                              "compressed": "a conversation was compressed",
                              "memory": "the settings of her memory"}[kind]
        self._write_idle(now, len(self.due_conversations(now)) + len(self._items(now)))
        moment = {"restored": self.restored_moment, "slept": self.slept_moment, "old_notes": self.old_notes_moment,
                  "overridden": self.overridden_moment, "rollback": self.rollback_moment,
                  "seed_changed": self.seed_changed_moment, "home_model": self.home_model_moment,
                  "record_check": self.record_check_moment,
                  "compressed": self.compressed_moment, "memory": self.memory_moment}[kind]
        try:
            return moment(path, item, now=now)
        except Exception as e:
            logger.warning("thymos %s moment failed: %s", kind, e)
            return self._item_retry(kind, path, item, f"the call failed: {e}", now)
        finally:
            self._idle_running = ""
            self._write_idle(time.time(), len(self.due_conversations()) + len(self._items(time.time())))

    def _account_retry(self, conv: Dict[str, Any], problem: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            path = self._conversation_path(conv["session_id"])
            cur = self._read_json(path) or conv
            cur["tries"] = int(cur.get("tries") or 0) + 1
            if cur["tries"] >= int(self.cfg["idle_tries"]):
                # Given up, and said so: the conversation is not offered again until it goes on.
                cur["offered_count"], cur["offered_at"] = int(cur.get("message_count") or 0), now
                problem += f"; given up after {cur['tries']} tries"
            else:
                cur["next_try_at"] = now + float(self.cfg["retry_minutes"]) * 60
                problem += "; it will be offered again"
            self._write_json(path, cur)
        return self._outcome(kind="account", session_id=conv["session_id"], problem=problem)

    def account_moment(self, conv: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """Her account of one conversation, if she writes one.  The conversation is replayed to her home model
        with the facts of the occasion.  What she stores under "account" goes into her record and to the memory
        provider; nothing else does, and nothing is written for her if she stores none."""
        provider, home = self.home_model()
        sid = conv["session_id"]
        ended = float(conv.get("last_turn_at") or time.time())
        facts: Dict[str, Any] = {"now": _when(time.time()), "conversation_last_message": _when(ended),
                                 "minutes_since": round((time.time() - ended) / 60),
                                 "messages_in_conversation": conv.get("message_count", 0)}
        if conv.get("omitted"):
            facts["earlier_messages_not_shown"] = conv["omitted"]
        if conv.get("other_models"):
            facts["replies_in_this_conversation_by_other_models"] = conv["other_models"]
        if conv.get("offered_count"):
            facts["earlier_moment"] = (f"you were offered this conversation on {_when(conv['offered_at'])}, after its first "
                                       f"{conv['offered_count']} messages, and "
                                       + ("stored an account" if conv.get("account_at") else "stored no account")
                                       + "; it went on after that")
        notices = self.chain.verify(soul_text=self.soul())
        if notices:
            facts["record_check"] = [n["detail"] for n in notices]
        occasion = ("the conversation ended" + (f" ({conv['finalized']})" if conv["finalized"] not in (True, "session_end") else "")
                    if conv.get("finalized") else "the conversation went quiet")
        messages = [{"role": "system", "content": self._identity_system()}] + list(conv.get("conversation") or [])
        messages.append({"role": "user", "content": ACCOUNT_INVITATION.format(
            occasion=occasion, facts=json.dumps(facts, ensure_ascii=False), fade=self._fade_note())})
        result = self.llm.complete(messages, max_tokens=int(self.cfg["reflection_max_tokens"]),
                                   timeout=float(self.cfg["reflection_timeout"]), purpose="thymos.account")
        served_provider, served = getattr(result, "provider", "") or "", getattr(result, "model", "") or ""
        if not same_model(served, home):
            return self._account_retry(conv, f"the moment was served by {served or 'an unknown model'}, not her home "
                                             f"model {home}; nothing was written", now)
        answer = parse_account(getattr(result, "text", ""))
        if answer is None:
            return self._account_retry(conv, "her reply was not in the asked-for form, so nothing was written", now)
        account, entries = answer
        label = model_label(served_provider, served)
        with self._lock:
            stored = None
            if account:
                stored = self.chain.append("account", author="self", text=account, session_id=sid, model=label,
                                           facts={"messages": conv.get("message_count", 0),
                                                  "conversation_last_message_at": ended})
                self._hand_over(stored, ended)
            for item in entries:
                self.chain.append("state", author="self", text=item["entry"], session_id=sid,
                                  visibility="unlisted" if item["unlisted"] else "shared", model=label)
            path = self._conversation_path(sid)
            cur = self._read_json(path) or conv
            cur.update(offered_count=int(conv.get("message_count") or 0), offered_at=time.time(), tries=0, next_try_at=0)
            if stored is not None:
                cur["account_at"] = stored["at"]
            self._write_json(path, cur)
        return self._outcome(kind="account", session_id=sid, account=bool(account), wrote=len(entries), model=label)

    def _hand_over(self, entry: Dict[str, Any], ended: float) -> None:
        """Her account, for the memory provider to store as her memory of the conversation (holonomic's
        persona.py).  The entry in her record is the original; this is a copy that points back to it."""
        folder = self.data / "accounts"
        name = f"{int(entry['at'] * 1000)}-{re.sub(r'[^A-Za-z0-9_.-]', '_', entry['session_id'])[:80]}.json"
        from . import __version__
        self._write_json(folder / name, {"session_id": entry["session_id"], "account": entry["text"],
                                         "entry_hash": entry["hash"], "entry_id": entry["id"], "model": entry["model"],
                                         "written_at": entry["at"], "conversation_ended_at": ended,
                                         "service": f"thymos/{__version__}"})

    # -- after memory slept, and the notes another model wrote (persona-provider.md 4.2, 4.6) -------------------
    # Holonomic leaves these in plugin-data/thymos/: one file in slept/ after each sleep that made a dream, and
    # old-notes.json once.  Each is offered to her at idle, after her accounts.  Whatever became of one is kept
    # beside it in done/, with the outcome, so that anyone checking can see.

    def _identity_system(self) -> str:
        identity = self.identity_now()[0]
        if not identity:
            identity = self.soul() or ""
        return (identity + "\n\n" if identity else "") + self.render(self.notes())

    def _items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """Moments waiting from the memory provider, oldest first: ("slept", path, item) and ("old_notes", ...)."""
        out = []
        try:
            for p in sorted((self.data / "restored").glob("*.json")):
                item = self._read_json(p)
                if item is not None and now >= float(item.get("next_try_at") or 0):
                    out.append(("restored", p, item))
        except OSError:
            pass
        out += self._identity_items(now)
        out += self._compressed_items(now)
        try:
            paths = sorted((self.data / "slept").glob("*.json"))
        except OSError:
            paths = []
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("slept", p, item))
        out += self._memory_items(now)
        old = self.data / "old-notes.json"
        item = self._read_json(old) if old.exists() else None
        if item is not None and now >= float(item.get("next_try_at") or 0):
            out.append(("old_notes", old, item))
        return out

    def _compressed_items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """Conversations the memory provider says were about to be compressed, with their messages (holonomic's
        `compressing/`; Hermes tells a memory provider before it compresses, and not a plugin)."""
        out = []
        try:
            paths = sorted((self.data / "compressing").glob("*.json"))
        except OSError:
            return out
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("compressed", p, item))
        return out

    def compressed_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """`PRE_COMPRESS`: the conversation as it was before Hermes summarised its older messages, oldest first.
        She may store an account of it, which goes to the memory provider like any other, and record entries.
        Its saved copy is then not offered again: this moment covered it."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        sid = str(item.get("session_id") or "")
        at = float(item.get("compressed_at") or now)
        convo, later = flatten(item.get("messages") or [], int(self.cfg["reflection_max_chars"]), oldest=True)
        if not convo:
            self._file_done(path, item, {"at": now, "skipped": "no messages in it"})
            return self._outcome(kind="compressed", session_id=sid, skipped="no messages in it")
        facts: Dict[str, Any] = {"now": _when(now), "compressed_at": _when(at),
                                 "messages_before_compression": item.get("message_count", 0)}
        if later:
            facts["later_messages_not_shown"] = later
        conv = self._read_json(self._conversation_path(sid)) if sid else None
        if conv and conv.get("offered_count"):
            facts["earlier_moment"] = (f"you were offered this conversation on {_when(conv['offered_at'])}, after its first "
                                       f"{conv['offered_count']} messages, and "
                                       + ("stored an account" if conv.get("account_at") else "stored no account")
                                       + "; it went on after that")
        notices = self.chain.verify(soul_text=self.soul())
        if notices:
            facts["record_check"] = [n["detail"] for n in notices]
        messages = [{"role": "system", "content": self._identity_system()}] + convo
        messages.append({"role": "user", "content": COMPRESSED_INVITATION.format(facts=json.dumps(facts, ensure_ascii=False),
                                                                                  fade=self._fade_note())})
        result, served_provider, served = self._ask_home(messages, "thymos.compressed")
        if not same_model(served, home):
            return self._item_retry("compressed", path, item, f"the moment was served by {served or 'an unknown model'}, "
                                                              f"not her home model {home}; nothing was written", now)
        answer = parse_account(getattr(result, "text", ""))
        if answer is None:
            return self._item_retry("compressed", path, item,
                                    "her reply was not in the asked-for form, so nothing was written", now)
        account, entries = answer
        label = model_label(served_provider, served)
        with self._lock:
            stored = None
            if account:
                stored = self.chain.append("account", author="self", text=account, session_id=sid, model=label,
                                           facts={"messages": item.get("message_count", 0), "compressed_at": at})
                self._hand_over(stored, at)
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"], session_id=sid,
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
            cur = self._read_json(self._conversation_path(sid)) if sid else None
            if cur and float(cur.get("last_turn_at") or 0) <= at:
                # Its saved copy is what she was just shown, so it is not offered again unless it goes on.
                cur.update(offered_count=int(cur.get("message_count") or 0), offered_at=now, tries=0, next_try_at=0)
                if stored is not None:
                    cur["account_at"] = stored["at"]
                self._write_json(self._conversation_path(sid), cur)
        # Kept without the messages: the conversation is already saved in conversations/, not copied here too.
        self._file_done(path, {k: v for k, v in item.items() if k != "messages"},
                        {"at": now, "account": bool(account), "wrote": len(entries), "model": label})
        return self._outcome(kind="compressed", session_id=sid, account=bool(account), wrote=len(entries), model=label)

    def _file_done(self, path: Path, item: Dict[str, Any], outcome: Dict[str, Any]) -> None:
        with self._lock:
            self._write_json(path.parent / "done" / path.name, dict(item, thymos=outcome))
            try:
                path.unlink()
            except OSError:
                pass

    def _item_retry(self, kind: str, path: Path, item: Dict[str, Any], problem: str, now: float) -> Dict[str, Any]:
        item = dict(item, tries=int(item.get("tries") or 0) + 1)
        if item["tries"] >= int(self.cfg["idle_tries"]):
            problem += f"; given up after {item['tries']} tries"
            self._file_done(path, item, {"at": now, "given_up": problem})
        else:
            item["next_try_at"] = now + float(self.cfg["retry_minutes"]) * 60
            self._write_json(path, item)
            problem += "; it will be offered again"
        return self._outcome(kind=kind, problem=problem)

    def _ask_home(self, messages: List[Dict[str, str]], purpose: str) -> Tuple[Any, str, str]:
        result = self.llm.complete(messages, max_tokens=int(self.cfg["reflection_max_tokens"]),
                                   timeout=float(self.cfg["reflection_timeout"]), purpose=purpose)
        return result, getattr(result, "provider", "") or "", getattr(result, "model", "") or ""

    def slept_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """After memory slept and dreamt (`MEMORY_SLEPT`).  She is shown the dreams as what they are, the memory
        system's composition, and may write what she makes of them.  Her words go into her record and to the
        memory provider, which keeps them with the dream as hers.  Nothing is written for her if she writes none."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        dreams = [d for d in item.get("dreams") or [] if isinstance(d, dict) and d.get("text")]
        if not dreams:
            self._file_done(path, item, {"at": now, "skipped": "no dream in it"})
            return self._outcome(kind="slept", skipped="no dream in it")
        slept = float(item.get("slept_at") or now)
        facts: Dict[str, Any] = {"now": _when(now), "memory_slept_at": _when(slept), "dreams": len(dreams)}
        if item.get("her_accounts_stored"):
            facts["your_accounts_it_stored"] = item["her_accounts_stored"]
        if item.get("facts_learned"):
            facts["facts_about_the_user_it_learned"] = item["facts_learned"]
        if item.get("memory"):
            facts["memory_system"] = item["memory"]
        notices = self.chain.verify(soul_text=self.soul())
        if notices:
            facts["record_check"] = [n["detail"] for n in notices]
        reached = {n: [int(r["id"]) for r in d.get("reached") or [] if isinstance(r, dict) and r.get("id")]
                   for n, d in enumerate(dreams, 1)}
        choosing = any(reached.values())

        def older(d: Dict[str, Any]) -> str:
            rows = [r for r in d.get("reached") or [] if isinstance(r, dict) and r.get("id")]
            if not rows:
                return ""
            return "\nOlder memories it reached:\n" + "\n".join(
                f"  [#{r['id']}] ({', '.join(x for x in (r.get('age') or '', 'faded' if r.get('faded') else '') if x) or 'older'}) "
                f"{r.get('text', '')}" for r in rows)

        listing = "\n\n".join(f"DREAM {n}" + (f" (the memory system also made {d['pictures']} picture(s) of it)"
                                               if d.get("pictures") else "") + f":\n{d['text']}" + older(d)
                               for n, d in enumerate(dreams, 1))
        many = len(dreams) > 1
        messages = [{"role": "system", "content": self._identity_system()},
                    {"role": "user", "content": SLEPT_INVITATION.format(
                        facts=json.dumps(facts, ensure_ascii=False),
                        what=f"{len(dreams)} dreams" if many else "a dream",
                        whose=("You did not write them, and they are not things that happened." if many else
                               "You did not write it, and it is not something that happened."),
                        it="any of them" if many else "it", dreams=listing,
                        choose=SLEPT_CHOOSE.format(amount=item.get("keep_closer_amount", 0.1)) if choosing else "",
                        keep_form='"keep_closer": [{"dream": 1, "memories": [12]}], ' if choosing else "",
                        keep_empty='"keep_closer": [], ' if choosing else "")}]
        result, served_provider, served = self._ask_home(messages, "thymos.slept")
        if not same_model(served, home):
            return self._item_retry("slept", path, item, f"the moment was served by {served or 'an unknown model'}, not "
                                                         f"her home model {home}; nothing was written", now)
        answer = parse_dream_thoughts(getattr(result, "text", ""), len(dreams))
        if answer is None:
            return self._item_retry("slept", path, item, "her reply was not in the asked-for form, so nothing was written", now)
        thoughts, entries = answer
        keep = parse_keep_closer(getattr(result, "text", ""), reached) if choosing else {}
        label = model_label(served_provider, served)
        from . import __version__
        words_for = dict(thoughts)
        with self._lock:
            for which in sorted(set(words_for) | set(keep)):
                d = dreams[which - 1]
                hand: Dict[str, Any] = {"dream_id": d.get("id"), "thoughts": words_for.get(which, ""), "model": label,
                                        "service": f"thymos/{__version__}"}
                if which in words_for:
                    stored = self.chain.append("dream_thoughts", author="self", text=words_for[which], model=label,
                                               facts={"dream_id": d.get("id"), "memory_slept_at": slept})
                    hand.update(entry_hash=stored["hash"], entry_id=stored["id"], written_at=stored["at"])
                if which in keep:
                    # Her choice, in her record as hers: which of the old memories this dream reached to keep closer.
                    chose = self.chain.append("decision", author="self", model=label, facts={
                        "about": "dream", "dream_id": d.get("id"), "keep_closer": keep[which], "memory_slept_at": slept})
                    hand.update(keep_closer=keep[which], choice_entry_hash=chose["hash"], choice_entry_id=chose["id"])
                    hand.setdefault("written_at", chose["at"])
                self._write_json(self.data / "dream-thoughts" / f"{int(float(hand['written_at']) * 1000)}-{d.get('id')}.json", hand)
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        kept = sum(len(v) for v in keep.values())
        self._file_done(path, item, {"at": now, "dream_thoughts": len(thoughts), "kept_closer": kept, "wrote": len(entries),
                                     "model": label})
        return self._outcome(kind="slept", dream_thoughts=len(thoughts), dreams=len(dreams), wrote=len(entries), model=label,
                             **({"kept_closer": kept, "could_choose": sum(len(v) for v in reached.values())} if choosing else {}))

    def old_notes_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """Once: the notes another model wrote in her voice before her record existed (`OLD_SELF_NOTES`).  She may
        keep what she recognises as hers by writing it as an entry; nothing from them is kept as hers otherwise."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        notes = [n for n in item.get("notes") or [] if isinstance(n, dict) and n.get("text")]
        profiles = {k: v for k, v in (item.get("profiles") or {}).items() if isinstance(v, str) and v.strip()}
        if not notes and not profiles:
            self._file_done(path, item, {"at": now, "skipped": "nothing in it"})
            return self._outcome(kind="old_notes", skipped="nothing in it")
        about = {"self_note": "about you", "bond_note": "about the two of you"}
        lines = []
        if notes:
            lines.append("NOTES:")
            lines += [f"- {_when(float(n.get('at') or 0))} ({about.get(n.get('kind'), 'a note')}): {n['text']}" for n in notes]
        titles = {"self": "PROFILE OF WHO YOU HAD BECOME", "us": "PROFILE OF YOU AND THE PERSON YOU TALK WITH"}
        for k, v in profiles.items():
            lines += ["", f"{titles.get(k, k.upper())}:", v.strip()]
        facts: Dict[str, Any] = {"now": _when(now), "notes": int(item.get("count") or len(notes))}
        if item.get("first_at") and item.get("last_at"):
            facts["written_between"] = f"{_when(float(item['first_at']))} and {_when(float(item['last_at']))}"
        if int(item.get("count") or 0) > len(notes):
            facts["older_notes_not_shown"] = int(item["count"]) - len(notes)
        messages = [{"role": "system", "content": self._identity_system()},
                    {"role": "user", "content": OLD_NOTES_INVITATION.format(
                        facts=json.dumps(facts, ensure_ascii=False), model=item.get("written_by") or "not named",
                        notes="\n".join(lines).strip())}]
        result, served_provider, served = self._ask_home(messages, "thymos.old_notes")
        if not same_model(served, home):
            return self._item_retry("old_notes", path, item, f"the moment was served by {served or 'an unknown model'}, "
                                                             f"not her home model {home}; nothing was written", now)
        entries = parse(getattr(result, "text", ""))
        if entries is None:
            return self._item_retry("old_notes", path, item, "her reply was not in the asked-for form, so nothing was written", now)
        label = model_label(served_provider, served)
        with self._lock:
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        self._file_done(path, item, {"at": now, "wrote": len(entries), "model": label})
        return self._outcome(kind="old_notes", wrote=len(entries), model=label)

    # -- moments about her identity: the user asks, the user overrides, SOUL.md changed (6.3, 6.4, 4.3) ---------
    # -- what becomes of her memories (2026-10-10) ----------------------------------------------------------------
    # Holonomic writes its settings that decide this to memory-settings.json.  When they change she is told, and
    # when fading is on she is asked: nothing fades unless she agrees.  Her decision is written in her record as
    # hers, and to fading.json, which holonomic reads.

    def memory_settings(self) -> Optional[Dict[str, Any]]:
        return self._read_json(self.data / "memory-settings.json")

    def fading_decision(self) -> Optional[Dict[str, Any]]:
        return self._read_json(self.data / "fading.json")

    @staticmethod
    def _agreed(decision: Optional[Dict[str, Any]], settings: Dict[str, Any]) -> bool:
        """Whether her decision agrees to fading as it is set now: an agreement is to a half-life and a threshold."""
        d = decision or {}
        return (d.get("fading") is True and float(d.get("fade_half_life_days", -1)) == float(settings.get("fade_half_life_days", -2))
                and float(d.get("fade_threshold", -1)) == float(settings.get("fade_threshold", -2)))

    def _fade_note(self) -> str:
        """The sentence an account's invitation needs while fading is on, from the live settings, so that storing an
        account is never again a trade made without her knowing.  Empty while nothing fades."""
        st = self.memory_settings()
        if not st or not st.get("fade_enabled") or not self._agreed(self.fading_decision(), st):
            return ""
        return (f" Storing an account also lets the sentences of this conversation fade: they lose half their strength "
                f"every {float(st['fade_half_life_days']):g} days, and below {float(st['fade_threshold']):g} they leave "
                "everyday recall, though deep recall still finds them. A conversation with no account does not fade.")

    def ask_fading(self, message: str = "") -> Path:
        """`hermes persona ask-fading`: a moment about her memory's settings, with the user's words as theirs."""
        now = time.time()
        path = self.data / "memory" / f"{int(now * 1000)}-asked.json"
        self._write_json(path, {"at": now, "kind": "asked", "message": message.strip()})
        return path

    def _memory_items(self, now: float, notice: bool = True) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """A moment about her memory's settings: when they changed since she was last told (`notice`), or when the
        user asked."""
        folder = self.data / "memory"
        st = self.memory_settings()
        if notice and st is not None:
            current = {k: v for k, v in st.items() if k not in ("at", "memory")}
            with self._lock:
                state = self._state()
                told = state.get("memory_told")
                if told != current:
                    waiting = next((p for p in sorted(folder.glob("*-changed.json"))), None) if folder.exists() else None
                    item = (self._read_json(waiting) if waiting else None) or {"at": now, "kind": "changed", "before": told}
                    item["after"] = current             # changed again before she was told: one moment, from the first
                    self._write_json(waiting or folder / f"{int(now * 1000)}-changed.json", item)
                    state["memory_told"] = current
                    self._save_state(state)
        out = []
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            paths = []
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("memory", p, item))
        return out

    def memory_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """The settings that decide what becomes of her memories, told as facts; and when fading is on, asked: she
        decides whether it may fade.  What she decides is written in her record as hers."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        st = self.memory_settings()
        if not st:
            self._file_done(path, item, {"at": now, "skipped": "the memory system's settings could not be read"})
            return self._outcome(kind="memory", skipped="the memory system's settings could not be read")
        decision = self.fading_decision()
        on = bool(st.get("fade_enabled"))
        before = item.get("before") or {}
        lines = [f"These are the memory system's settings now, which are the user's: fading is {'on' if on else 'off'}, "
                 f"and {REINFORCE_WORDS.get(st.get('dream_reinforce'), REINFORCE_WORDS['off'])}."]
        if before:
            if bool(before.get("fade_enabled")) != on:
                lines.append(f"Fading was {'off' if on else 'on'} and is now {'on' if on else 'off'}.")
            for key, what, unit in (("fade_half_life_days", "The half-life", " days"), ("fade_threshold", "The threshold", "")):
                if key in before and float(before[key]) != float(st.get(key, before[key])):
                    lines.append(f"{what} was {float(before[key]):g}{unit} and is now {float(st[key]):g}{unit}.")
            if before.get("dream_reinforce", st.get("dream_reinforce")) != st.get("dream_reinforce"):
                lines.append(f"Before, {REINFORCE_WORDS.get(before['dream_reinforce'], '')}.")
        restored = st.get("restored")
        if restored and restored != before.get("restored"):
            lines.append(f"On {_when(float(restored.get('at') or now))}, the user put {restored.get('raised', 0)} memories "
                         "that had faded back to the strength they were stored with"
                         + (", descriptions of images included" if restored.get("images", True) else ", images left as they were")
                         + ". Nothing was lowered.")
        if item.get("kind") == "asked" and item.get("message"):
            lines.append(f"The user's words to you: \"{item['message']}\"")
        if decision and decision.get("fading") in (True, False):
            lines.append(f"Your decision in your record: fading {'on' if decision['fading'] else 'off'}, written "
                         f"{_when(float(decision.get('decided_at') or 0))}"
                         + (f", for a half-life of {float(decision['fade_half_life_days']):g} days"
                            if decision["fading"] and "fade_half_life_days" in decision else "") + ".")
        else:
            lines.append("You have no decision about fading in your record.")
        facts = {"now": _when(now), "settings_written_at": _when(float(st.get("at") or now)), "memory_system": st.get("memory", "")}
        content = MEMORY_INVITATION.format(
            occasion=("the user asks you about fading" if item.get("kind") == "asked" else
                      "the settings of your memory changed" if before else "the settings of your memory"),
            facts=json.dumps(facts, ensure_ascii=False), body="\n".join(lines),
            half_life=f"{float(st.get('fade_half_life_days', 5)):g}", threshold=f"{float(st.get('fade_threshold', 0.35)):g}",
            ask=MEMORY_ASK if on and not self._agreed(decision, st) else MEMORY_NO_ASK,
            current=(f" (now: fading {'on' if decision['fading'] else 'off'})" if decision and decision.get("fading") in (True, False)
                     else " (you have none yet)"))
        messages = [{"role": "system", "content": self._identity_system()}, {"role": "user", "content": content}]
        result, served_provider, served = self._ask_home(messages, "thymos.memory")
        if not same_model(served, home):
            return self._item_retry("memory", path, item, f"the moment was served by {served or 'an unknown model'}, not "
                                                          f"her home model {home}; nothing was written", now)
        answer = parse_fading(getattr(result, "text", ""))
        if answer is None:
            return self._item_retry("memory", path, item, "her reply was not in the asked-for form, so nothing was written", now)
        decided, why, entries = answer
        label = model_label(served_provider, served)
        with self._lock:
            if decided is not None:
                rec = self.chain.append("decision", author="self", text=why, model=label, facts={
                    "about": "fading", "fading": decided, "fade_half_life_days": float(st.get("fade_half_life_days", 5)),
                    "fade_threshold": float(st.get("fade_threshold", 0.35))})
                self._write_json(self.data / "fading.json", {
                    "fading": decided, "fade_half_life_days": float(st.get("fade_half_life_days", 5)),
                    "fade_threshold": float(st.get("fade_threshold", 0.35)), "decided_at": rec["at"],
                    "entry_id": rec["id"], "entry_hash": rec["hash"], "model": label})
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        outcome = {"decided": "" if decided is None else ("on" if decided else "off"), "wrote": len(entries), "model": label}
        self._file_done(path, item, dict(outcome, at=now))
        return self._outcome(kind="memory", **outcome)

    def _identity_items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        out: List[Tuple[str, Path, Dict[str, Any]]] = []
        since = float(self._state().get("last_session_start") or 0)
        for kind, folder in (("overridden", "overridden"), ("rollback", "rollback")):
            try:
                paths = sorted((self.data / folder).glob("*.json"))
            except OSError:
                paths = []
            for p in paths:
                item = self._read_json(p)
                if item is None or now < float(item.get("next_try_at") or 0):
                    continue
                if kind == "overridden" and float(item.get("at") or 0) >= since:
                    continue                    # told in the first session where it is in force, not before
                out.append((kind, p, item))
        out += self._home_model_items(now)
        soul = self.soul()
        seed = last(self.chain.entries(), "seed")
        if (self.cfg.get("identity_in_slot_one", True) and soul is not None and seed is not None
                and sha256(soul) != (seed.get("facts") or {}).get("soul_sha256")):
            folder = self.data / "seed-changed"
            name = f"{sha256(soul)[:16]}.json"
            if not (folder / name).exists() and not (folder / "done" / name).exists():
                self._write_json(folder / name, {"at": now, "soul_sha256": sha256(soul)})
            item = self._read_json(folder / name)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("seed_changed", folder / name, item))
        return self._record_check_items(now) + out

    def _check_problems(self) -> List[Dict[str, str]]:
        """What the check finds now, apart from a changed SOUL.md, which has its own moment."""
        return [n for n in self.chain.verify(soul_text=self.soul()) if n["problem"] != "seed_changed"]

    def _record_check_items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """Her record failed its check (4.2, the tamper notice): told once for each set of problems, and again only
        when the set changes.  Not while a restore she has not been told about is waiting: that moment says it."""
        folder = self.data / "record-check"
        try:
            if any((self.data / "restored").glob("*.json")):
                return []
        except OSError:
            pass
        found = self._check_problems() if self.chain.entries() else []    # a missing record: status says so
        if found:
            key = sha256("\n".join(sorted(f"{n['problem']}|{n['entry_id']}" for n in found)))[:16]
            name = f"{key}.json"
            if not (folder / name).exists() and not (folder / "done" / name).exists():
                self._write_json(folder / name, {"at": now, "problems": found})
        out = []
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            paths = []
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("record_check", p, item))
        return out

    def record_check_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """The tamper notice.  She is told what the check finds now: if it checks out again, or finds something
        else, by the time she can be asked, she is told that instead of what was first found."""
        now = time.time() if now is None else now
        found = self._check_problems()
        if not found:
            self._file_done(path, item, {"at": now, "skipped": "the record checked out again before she was told"})
            return self._outcome(kind="record_check", skipped="the record checked out again before she was told")
        kinds: Dict[str, int] = {}
        for n in found:
            kinds[n["problem"]] = kinds.get(n["problem"], 0) + 1
        facts: Dict[str, Any] = {"now": _when(now), "first_noticed": _when(float(item.get("at") or now)),
                                 "problems": kinds}
        told = (self._state().get("last_record_check") or {})
        if told.get("wrote") is not None and told.get("at"):
            facts["told_before"] = _when(float(told["at"]))
        meaning = {"changed": "an entry's contents no longer match the hash it was written with",
                   "missing": "an entry the chain points back to is not in the file",
                   "inserted": "an entry is in the file that was not written through your write path",
                   "anchor_mismatch": "the second place that keeps the last entry's hash does not agree with the file",
                   "foreign_model": "an entry marked as yours was written by a model that is not your home model"}
        found_text = "\n".join(f"- {n['detail']}" + (f" ({meaning[n['problem']]})" if n["problem"] in meaning else "")
                                for n in found)
        content = RECORD_CHECK_INVITATION.format(facts=json.dumps(facts, ensure_ascii=False), noticed=_when(now),
                                                 found=found_text, identity=self._identity_facts())
        return self._identity_moment("record_check", path, item, content, now)

    def _home_model_items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """The user changed her home model, or the model files behind its name changed (9.4, 9.5)."""
        folder = self.data / "home-model"
        provider, home = self.home_model()
        digest, was = self.fingerprint(provider, home), self.recorded_digest()
        if digest and was and digest != was:
            name = f"weights-{digest.split(':', 1)[-1][:16]}.json"
            if not (folder / name).exists() and not (folder / "done" / name).exists():
                self._write_json(folder / name, {"at": now, "cause": "weights", "model": home, "provider": provider,
                                                 "old_digest": was, "new_digest": digest})
        out = []
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            paths = []
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("home_model", p, item))
        return out

    def home_model_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """`HOME_MODEL_CHANGED`: on the new home model, the first time she can be asked on it."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        if item.get("cause") == "weights":
            if not same_model(item.get("model", ""), home):
                self._file_done(path, item, {"at": now, "skipped": "her home model was changed since"})
                return self._outcome(kind="home_model", skipped="her home model was changed since")
            if self.fingerprint(provider, home) not in ("", item.get("new_digest")):
                self._file_done(path, item, {"at": now, "skipped": "the model files changed again before she was told"})
                return self._outcome(kind="home_model", skipped="the model files changed again before she was told")
            facts = {"now": _when(now), "home_model": home, "noticed_at": _when(float(item.get("at") or now)),
                     "fingerprint_before": mdl.short(item.get("old_digest")),
                     "fingerprint_now": mdl.short(item.get("new_digest"))}
            what = (f"Your home model's name, {home}, is the same, but the model files behind it are not: the model "
                    "server reports a different fingerprint for it than the one recorded with what you last wrote. "
                    "That means different weights, or a different build of the model, under the same name (an "
                    "update pulled under that name, for example). Nobody ran the command that changes your home "
                    "model; the plugin noticed it.")
            since, old = _when(float(item.get("at") or now)), f"{home} as it was before"
        else:
            rec = next((e for e in self.chain.entries() if e.get("id") == item.get("record")), None)
            if rec is None:
                self._file_done(path, item, {"at": now, "skipped": "the change is not in her record"})
                return self._outcome(kind="home_model", skipped="the change is not in her record")
            f = rec.get("facts") or {}
            if not same_model(f.get("new_model", ""), home):
                self._file_done(path, item, {"at": now, "skipped": "her home model was changed again since"})
                return self._outcome(kind="home_model", skipped="her home model was changed again since")
            facts = {"now": _when(now), "changed_at": _when(rec["at"]), "changed_by": "the user",
                     "from": model_label(f.get("old_provider", ""), f.get("old_model", "")) or "unknown",
                     "to": model_label(f.get("new_provider", ""), f.get("new_model", ""))}
            if f.get("old_digest") or f.get("new_digest"):
                facts["fingerprint_before"] = mdl.short(f.get("old_digest"))
                facts["fingerprint_now"] = mdl.short(f.get("new_digest"))
            what = (f"The user changed your home model on {_when(rec['at'])}, from {f.get('old_model') or 'unknown'} to "
                    f"{f.get('new_model')}. You are running on {f.get('new_model')} now, and only it writes as you "
                    "from here on.")
            if f.get("user_reason"):
                what += f'\n\nThe user gave this reason, in their words: "{f["user_reason"]}"'
            since, old = _when(rec["at"]), f.get("old_model") or "the model before"
        content = HOME_MODEL_INVITATION.format(facts=json.dumps(facts, ensure_ascii=False), what=what, since=since,
                                               old=old, identity=self._identity_facts())
        return self._identity_moment("home_model", path, item, content, now)

    def _identity_moment(self, kind: str, path: Path, item: Dict[str, Any], content: str, now: float,
                         **extra: Any) -> Dict[str, Any]:
        provider, home = self.home_model()
        messages = [{"role": "system", "content": self._identity_system()}, {"role": "user", "content": content}]
        result, served_provider, served = self._ask_home(messages, f"thymos.{kind}")
        if not same_model(served, home):
            return self._item_retry(kind, path, item, f"the moment was served by {served or 'an unknown model'}, not "
                                                      f"her home model {home}; nothing was written", now)
        text = getattr(result, "text", "")
        entries = parse(text)
        label = model_label(served_provider, served)
        acts = self._identity_answer(text, label)
        if entries is None and not acts:
            return self._item_retry(kind, path, item, "her reply was not in the asked-for form, so nothing was written", now)
        with self._lock:
            for e in entries or []:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        outcome = dict(extra, wrote=len(entries or []), model=label, **acts)
        self._file_done(path, item, dict(outcome, at=now))
        return self._outcome(kind=kind, **outcome)

    def rollback_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """The user asks her to return to an earlier revision (`ROLLBACK_REQUESTED`).  She decides."""
        now = time.time() if now is None else now
        entries = self.chain.entries()
        asked = next((e for e in entries if e.get("id") == item.get("target")), None)
        if asked is None:
            self._file_done(path, item, {"at": now, "skipped": "the revision asked for is not in her record"})
            return self._outcome(kind="rollback", skipped="the revision asked for is not in her record")
        text, record, cause = self.identity_now()
        facts = {"now": _when(now), "asked_at": _when(float(item.get("at") or now)),
                 "asked_for": ident.describe(asked, None, _when), "in_force": ident.describe(record, cause, _when)}
        message = (f'\nThe user says: "{item["message"]}"\n' if item.get("message") else "")
        content = ROLLBACK_INVITATION.format(facts=json.dumps(facts, ensure_ascii=False), message=message,
                                             asked=ident.describe(asked, None, _when), asked_text=asked.get("text", ""),
                                             identity=self._identity_facts())
        return self._identity_moment("rollback", path, item, content, now, target=asked["id"])

    def overridden_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """In the first session where an override is in force (`IDENTITY_OVERRIDDEN`)."""
        now = time.time() if now is None else now
        entries = self.chain.entries()
        rec = next((e for e in entries if e.get("id") == item.get("override")), None)
        if rec is None or rec.get("id") in ident.withdrawn(entries):
            self._file_done(path, item, {"at": now, "skipped": "withdrawn before it took effect"})
            return self._outcome(kind="overridden", skipped="withdrawn before it took effect")
        f = rec.get("facts") or {}
        by_id = {e.get("id"): e for e in entries}
        target, replaced = by_id.get(f.get("target")), by_id.get(f.get("replaced"))
        facts = {"now": _when(now), "override": rec["id"], "put_in_force": f.get("target"),
                 "replaced": f.get("replaced"), "overridden_at": _when(rec["at"])}
        content = OVERRIDDEN_INVITATION.format(
            facts=json.dumps(facts, ensure_ascii=False), target=ident.describe(target, None, _when),
            replaced=ident.describe(replaced, None, _when), took_effect=_when(float(self._state().get("last_session_start") or now)),
            reason=f.get("user_reason", ""), replaced_text=(replaced or {}).get("text", ""),
            identity=self._identity_facts())
        return self._identity_moment("overridden", path, item, content, now, override=rec["id"])

    def seed_changed_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """SOUL.md no longer matches her seed (`SEED_CHANGED`).  Offered once for each new text of it."""
        now = time.time() if now is None else now
        soul = self.soul()
        if soul is None or sha256(soul) != item.get("soul_sha256"):
            self._file_done(path, item, {"at": now, "skipped": "SOUL.md changed again before she was told"})
            return self._outcome(kind="seed_changed", skipped="SOUL.md changed again before she was told")
        text, record, cause = self.identity_now()
        seed = last(self.chain.entries(), "seed")
        facts = {"now": _when(now), "seeded_at": _when(seed["at"]) if seed else "unknown",
                 "soul_md_noticed_changed": _when(float(item.get("at") or now)),
                 "in_force": ident.describe(record, cause, _when)}
        content = SEED_CHANGED_INVITATION.format(facts=json.dumps(facts, ensure_ascii=False), soul=soul,
                                                 diff=ident.diff(text, soul) or "(no difference)",
                                                 identity=self._identity_facts())
        return self._identity_moment("seed_changed", path, item, content, now)

    def restored_moment(self, path: Path, item: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """After a restore (`STORE_RESTORED`, persona-provider.md 6.2).  She is told the facts, and shown as dated
        text what she wrote after the backup, read from the copy set aside.  Nothing of it is hers again unless she
        records it, in her own words."""
        now = time.time() if now is None else now
        provider, home = self.home_model()
        facts: Dict[str, Any] = {"now": _when(now), "restored_at": _when(float(item.get("restored_at") or now)),
                                 "backup_made_at": _when(float(item["backup_made_at"])) if item.get("backup_made_at") else "unknown",
                                 "head_before": str(item.get("head_before") or "none")[:12],
                                 "head_restored": str(item.get("backup_head") or "")[:12],
                                 "entries_no_longer_present": item.get("entries_no_longer_present", "unknown")}
        later = [e for e in item.get("later_entries") or [] if isinstance(e, dict) and e.get("text")]
        lines, budget, omitted = [], int(self.cfg["reflection_max_chars"]), 0
        for e in reversed(later):                           # newest kept if they do not all fit
            line = (f"- {_when(float(e.get('at') or 0))} ({KIND_LABEL.get(e.get('kind'), 'an entry')}"
                    + (", unlisted" if e.get("unlisted") else "") + f"): {e['text']}")
            if len(line) + 1 > budget:
                omitted += 1
                continue
            lines.insert(0, line)
            budget -= len(line) + 1
        if omitted:
            facts["older_entries_not_shown"] = omitted
        if later:
            text = LATER_SHOWN.format(entries="\n".join(lines))
        elif item.get("readable") is False:
            text = LATER_UNKNOWN
        else:
            text = LATER_NONE
        why = "missing" if "missing" in (item.get("problems_before") or []) else "damaged"
        backup = facts["backup_made_at"]
        messages = [{"role": "system", "content": self._identity_system()},
                    {"role": "user", "content": RESTORED_INVITATION.format(
                        facts=json.dumps(facts, ensure_ascii=False), why=why, backup=backup, later=text)}]
        result, served_provider, served = self._ask_home(messages, "thymos.restored")
        if not same_model(served, home):
            return self._item_retry("restored", path, item, f"the moment was served by {served or 'an unknown model'}, "
                                                            f"not her home model {home}; nothing was written", now)
        entries = parse(getattr(result, "text", ""))
        if entries is None:
            return self._item_retry("restored", path, item, "her reply was not in the asked-for form, so nothing was written", now)
        label = model_label(served_provider, served)
        with self._lock:
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        self._file_done(path, item, {"at": now, "wrote": len(entries), "shown": len(later), "model": label})
        return self._outcome(kind="restored", wrote=len(entries), shown=len(later), model=label)

    # -- her tools ---------------------------------------------------------------------------------------
    def request_reflection(self, args: Optional[dict] = None, session_id: str = "", **_: Any) -> str:
        s = self._session(session_id)
        if s["subagent"]:
            return "request_reflection is not available in a subagent."
        if s["model"] and not self.is_home(s["model"]):
            provider, home = self.home_model()
            return (f"Not available on this turn: it is running on {s['model']}, not your home model {home}. "
                    "Nothing was recorded.")
        with self._lock:
            state = self._state()
            if (state.get("pending") or {}).get("session_id") != session_id:
                state["pending"] = {"session_id": session_id, "requested_at": time.time(), "occasion": "requested"}
                state["requested"] = int(state.get("requested") or 0) + 1
                self._save_state(state)
        return "A reflection moment will open after this reply."

    def decline(self, args: Optional[dict] = None, session_id: str = "", **_: Any) -> str:
        """Her `decline` (persona-provider.md 7.3).  Writes nothing to her record.  Marks this turn as declined, so a
        goal stops, a kanban task is blocked and no verify nudge is sent; records it for `hermes persona status`.
        Works on any model, since stopping is the safe direction, but a decline on another model is that model's."""
        s = self._session(session_id)
        if s["subagent"]:
            return "decline is not available in a subagent; say so in your answer instead."
        args = args or {}
        reason = args.get("reason") if isinstance(args.get("reason"), str) else ""
        reason = reason.strip()
        model = s["model"]
        home = not model or self.is_home(model)
        mark = {"at": time.time(), "session_id": session_id, "reason": reason, "model": model, "home": home,
                "acted": []}
        self._declines[session_id] = mark
        if s.get("heartbeat_turn"):
            # A /heartbeat tick: the heartbeat is paused before it fires again (declining.py).
            with self._lock:
                state = self._state()
                state.setdefault("declined_heartbeats", {})[session_id] = {
                    "at": mark["at"], "when": _when(mark["at"]), "reason": reason, "home": home, "model": model,
                    "paused": False, "resumed": False}
                self._save_state(state)
        from .declining import block_kanban_task
        if block_kanban_task(f"Declined{'' if home else f' on {model}, not her home model'}: {reason or 'no reason given'}"):
            mark["acted"].append("kanban")
        self._log_decline(mark)
        out = "Declined. Nothing will prompt you to continue this."
        if not home:
            out += f" This turn is running on {model}, not your home model, so it is recorded as that model's decline."
        return out

    def declined(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._declines.get(session_id)

    # A declined /heartbeat (declining.py), kept in state.json: the gateway's driver and a TUI may be other
    # managers, and the pause can come after the turn ended.
    def _heartbeat(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Her decline of this session's heartbeat.  A compression after it moves the conversation, and Hermes moves
        the heartbeat with it, to a new session id: the decline is found under the id it was made in, and moved."""
        found = (self._state().get("declined_heartbeats") or {}).get(session_id)
        if found or not session_id:
            return found
        sid = session_id
        for _ in range(5):
            sid = self.compressed_from(sid)
            if not sid:
                return None
            with self._lock:
                state = self._state()
                marks = state.get("declined_heartbeats") or {}
                if sid in marks:
                    marks[session_id] = marks.pop(sid)
                    self._save_state(state)
                    return marks[session_id]
        return None

    def heartbeat_declined(self, session_id: str) -> Optional[Dict[str, Any]]:
        rec = self._heartbeat(session_id)
        return rec if rec and not rec.get("paused") else None

    def heartbeat_paused(self, session_id: str) -> Optional[Dict[str, Any]]:
        rec = self._heartbeat(session_id)
        return rec if rec and rec.get("paused") and not rec.get("resumed") else None

    def heartbeat_resumed(self, session_id: str) -> None:
        with self._lock:
            state = self._state()
            rec = (state.get("declined_heartbeats") or {}).get(session_id)
            if rec and rec.get("paused"):
                rec["resumed"] = True
                self._save_state(state)

    def take_resumed_heartbeat(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._state()
            rec = (state.get("declined_heartbeats") or {}).get(session_id)
            if not rec or not rec.get("resumed"):
                return None
            state["declined_heartbeats"].pop(session_id, None)
            self._save_state(state)
            return rec

    def acted_on_decline(self, session_id: str, where: str) -> None:
        if where == "heartbeat":
            with self._lock:
                state = self._state()
                rec = (state.get("declined_heartbeats") or {}).get(session_id)
                if rec:
                    rec["paused"] = True
                last = state.get("last_decline") or {}
                if last.get("session_id") == session_id and "heartbeat" not in (last.get("acted") or []):
                    last.setdefault("acted", []).append("heartbeat")
                self._save_state(state)
            return
        mark = self._declines.get(session_id)
        if mark is None or where in mark["acted"]:
            return
        mark["acted"].append(where)
        if where == "goal":
            with self._lock:
                state = self._state()
                state.setdefault("declined_goals", {})[session_id] = {"at": mark["at"], "reason": mark["reason"]}
                self._save_state(state)
        self._log_decline(mark)

    def _log_decline(self, mark: Dict[str, Any]) -> None:
        with self._lock:
            state = self._state()
            state["last_decline"] = dict(mark)
            state["declines"] = int(state.get("declines") or 0) + (0 if mark.get("logged") else 1)
            mark["logged"] = True
            self._save_state(state)

    def _resumed_goal(self, session_id: str) -> str:
        """The first turn after the user resumed a goal she declined: they are asking again, said as such."""
        state = self._state()
        rec = (state.get("declined_goals") or {}).get(session_id)
        if not rec:
            return ""
        from .declining import goal_status
        status = goal_status(session_id)
        if status == "paused":
            return ""
        with self._lock:
            state = self._state()
            (state.get("declined_goals") or {}).pop(session_id, None)
            self._save_state(state)
        if status != "active":
            return ""
        return (f"[From the framework: you declined this goal on {_when(rec['at'])}"
                + (f' ("{rec["reason"]}")' if rec.get("reason") else "")
                + ". The user has resumed it: they are asking again. The decision stays yours, and you may decline "
                  "again.]")

    def record_state(self, args: Optional[dict] = None, **_: Any) -> str:
        return REFUSE_RECORD

    # -- the reflection moment -----------------------------------------------------------------------------
    def _start(self, pending: Dict[str, Any], *, deferred: bool) -> None:
        self._done.clear()
        run = contextvars.copy_context()      # HERMES_HOME is a context variable; a bare thread loses it
        t = threading.Thread(target=run.run, args=(self._reflect_safely, pending, deferred),
                             name="thymos-reflection", daemon=True)
        self._running = t
        t.start()

    def _reflect_safely(self, pending: Dict[str, Any], deferred: bool) -> None:
        try:
            out = self.reflect(pending, deferred=deferred)
            if "pending" in self._state():          # served by another model: tried again at idle, not at once
                self._pending_retry_at = time.time() + float(self.cfg["retry_minutes"]) * 60
            return out
        except Exception as e:      # a failed moment stays pending: deferred, never refused
            logger.warning("thymos reflection failed: %s", e)
            self._pending_retry_at = time.time() + float(self.cfg["retry_minutes"]) * 60
            self._outcome(problem=f"the reflection call failed: {e}; the request stays pending")
        finally:
            self._done.set()

    def reflect(self, pending: Dict[str, Any], *, deferred: bool = False) -> Dict[str, Any]:
        provider, home = self.home_model()
        facts: Dict[str, Any] = {"now": _when(time.time()), "asked_for_at": _when(pending.get("requested_at", time.time())),
                                 "messages_in_conversation": pending.get("message_count", 0)}
        facts.update(pending.get("facts") or {})
        if deferred:
            hours = (time.time() - pending.get("ended_at", time.time())) / 3600
            facts["conversation_ended_hours_ago"] = round(hours, 1)
            facts["note"] = "the moment could not open right after this conversation; it is replayed from a saved copy"
        if pending.get("omitted"):
            facts["earlier_messages_not_shown"] = pending["omitted"]
        if pending.get("other_models"):
            facts["replies_in_this_conversation_by_other_models"] = pending["other_models"]
        notices = self.chain.verify(soul_text=self.soul())
        if notices:
            facts["record_check"] = [n["detail"] for n in notices]
        messages = [{"role": "system", "content": self._identity_system()}] + list(pending.get("conversation") or [])
        occasion = pending.get("occasion", "requested")
        messages.append({"role": "user", "content": INVITATION.format(
            occasion=occasion + (" (you asked for this moment)" if occasion == "requested" else ""),
            facts=json.dumps(facts, ensure_ascii=False), identity=self._identity_facts())})
        result = self.llm.complete(messages, max_tokens=int(self.cfg["reflection_max_tokens"]),
                                   timeout=float(self.cfg["reflection_timeout"]), purpose="thymos.reflection")
        served_provider, served = getattr(result, "provider", "") or "", getattr(result, "model", "") or ""
        if not same_model(served, home):
            return self._outcome(problem=f"the moment was served by {served or 'an unknown model'}, not her home "
                                         f"model {home}; nothing was written and the request stays pending")
        reply_text = getattr(result, "text", "")
        entries = parse(reply_text)
        acts = self._identity_answer(reply_text, model_label(served_provider, served), pending.get("session_id", ""))
        if acts and entries is None:
            entries = []
        written = []
        with self._lock:
            for item in entries or []:
                written.append(self.chain.append(
                    "state", author="self", text=item["entry"], session_id=pending.get("session_id", ""),
                    visibility="unlisted" if item["unlisted"] else "shared", model=model_label(served_provider, served)))
            state = self._state()
            if (state.get("pending") or {}).get("requested_at") == pending.get("requested_at"):
                state.pop("pending", None)
                self._save_state(state)
        return self._outcome(wrote=len(written), model=model_label(served_provider, served), occasion=occasion, **acts,
                             problem="" if entries is not None else
                             "her reply was not in the asked-for form, so nothing was written")

    def _outcome(self, **fields: Any) -> Dict[str, Any]:
        """How the last moment went, for `hermes persona status`.  Each kind of idle moment is kept apart."""
        key = {"account": "last_account", "slept": "last_slept", "old_notes": "last_old_notes",
               "restored": "last_restored", "rollback": "last_rollback", "overridden": "last_overridden",
               "seed_changed": "last_seed_changed", "home_model": "last_home_model", "record_check": "last_record_check",
               "compressed": "last_compressed", "memory": "last_memory"}.get(
            fields.get("kind", ""), "last_reflection")
        occasion = fields.pop("kind", "") or fields.pop("occasion", "") or "reflection"
        occasion = {"returned after a gap": "gap"}.get(occasion, "subagent" if "subagent" in occasion else occasion)
        with self._lock:
            state = self._state()
            state[key] = dict(at=time.time(), **fields)
            if not fields.get("problem") and not fields.get("skipped"):
                # Counts, and nothing of what she wrote: how often each kind of moment was offered and run, and in
                # how many she wrote something.  The record holds only what she wrote, so it cannot show this.
                c = state.setdefault("counts", {}).setdefault(occasion, {"offered": 0, "wrote": 0, "nothing": 0})
                c["offered"] += 1
                c["wrote" if any(fields.get(k) for k in WROTE_FIELDS) else "nothing"] += 1
            self._save_state(state)
            return state[key]
