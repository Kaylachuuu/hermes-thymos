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
}

# Holonomic (or any memory provider) reads what she has waiting at idle here, and stores her accounts from here
# (holonomic's persona.py).  The service says it is running through this variable, set in Hermes' process.
SERVICE_ENV = "HERMES_PERSONA_SERVICE"

STANDING = (
    "Only you write here. The entries below are ones you wrote yourself, in reflection moments; nothing else "
    "adds to them or changes them. You can ask for a reflection moment at any time with the request_reflection "
    "tool. It opens after your reply, with this conversation in front of you, and in it you may record entries "
    "about yourself, or nothing.")

INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: {occasion}
Facts: {facts}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

To record entries, reply with only a JSON object:
{{"record_state": [{{"entry": "your words", "unlisted": false}}]}}

Each entry is kept verbatim with its date and shown to you in later conversations under your own notes. \
"unlisted": true keeps an entry out of what other parts of the system can read; you still see it. \
To record nothing, reply {{"record_state": []}}."""

ACCOUNT_INVITATION = """[Reflection moment. This message is from the framework, not from the person you were talking with.]

Occasion: {occasion}
Facts: {facts}

Nothing you write here is sent to anyone. Writing nothing is a complete answer.

You may store your own account of this conversation for your long-term memory: what happened in it, in your own \
words. The memory system keeps it as your memory of this conversation. If you store none, it keeps none, and \
nothing is written in its place. You may also record entries about yourself, as in any reflection moment.

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
moment.

Reply with only a JSON object:
{{"dream_thoughts": [{{"dream": 1, "thoughts": "your words"}}], "record_state": [{{"entry": "your words", "unlisted": false}}]}}

To store nothing at all, reply {{"dream_thoughts": [], "record_state": []}}."""

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


def flatten(messages: List[Dict[str, Any]], max_chars: int) -> Tuple[List[Dict[str, str]], int]:
    """The conversation as plain user and assistant turns, newest `max_chars` kept.

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

    # -- places ---------------------------------------------------------------------------------------
    @property
    def home(self) -> Path:
        return Path(self._home() if callable(self._home) else self._home)

    @property
    def chain(self) -> Chain:
        return Chain(self.home / "self" / "entries.jsonl", self.home / "plugin-data" / "thymos" / "anchor.json")

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
            soul = self.soul()
            return self.chain.append(
                "seed", author="user", text=soul or "",
                facts={"soul_sha256": sha256(soul or ""), "soul_path": str(self.home / "SOUL.md"),
                       "soul_found": soul is not None, "home_provider": provider or "", "home_model": model})

    def home_model(self) -> Tuple[str, str]:
        entries = self.chain.entries()
        rec = last(entries, "home_model")
        if rec is not None:
            return rec["facts"].get("new_provider", ""), rec["facts"].get("new_model", "")
        seed = last(entries, "seed")
        if seed is not None:
            return seed["facts"].get("home_provider", ""), seed["facts"].get("home_model", "")
        return "", ""

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
            self._session(info["session_id"])["delivered"] = len(notes)
        return self.render(notes)

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
        notices = self.chain.verify(soul_text=self.soul())
        if notices:
            lines += ["", "Facts about your record, from its check at the start of this session:"]
            lines += [f"- {n['detail']}" for n in notices]
        return "\n".join(lines)

    # -- hooks --------------------------------------------------------------------------------------------
    def pre_llm_call(self, session_id: str = "", model: str = "", platform: str = "", is_first_turn: bool = False,
                     **_: Any) -> Optional[Dict[str, str]]:
        s = self._session(session_id)
        if platform == "subagent" or s["subagent"]:
            s["subagent"] = True
            return None
        s["model"] = model or s["model"]
        now = time.time()
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
        if not fresh:
            return None
        body = "\n".join(f"- {_when(e['at'])}: {e['text']}" for e in fresh)
        return {"context": "<self-notes>\nYou wrote these in a reflection moment since this conversation's "
                           f"system prompt was made. They are your own words.\n{body}\n</self-notes>"}

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
            if gap and not state.get("pending"):
                # Back after a gap: a fact core can observe without judging her or the conversation.
                state["pending"] = {"session_id": session_id, "requested_at": time.time(), "occasion": "returned after a gap",
                                    "facts": {"hours_since_your_last_conversation": gap}}
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

    def due_conversations(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Conversations waiting for idle time: gone quiet or ended, long enough, with something new since she
        was last offered them, and not waiting out a failed try."""
        now = time.time() if now is None else now
        quiet = float(self.cfg["quiet_minutes"]) * 60
        out = []
        for c in self.conversations():
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
        due = self.due_conversations(now)
        request = self._saved_request(now)
        items = self._items(now)
        self._write_idle(now, len(due) + len(items) + (1 if request else 0))
        if self._idle_running or not self._done.is_set() or not self.is_idle(now):
            return None
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
        if not due and items:
            # After her accounts: what memory made while it slept, then (once) the notes another model wrote.
            kind, path, item = items[0]
            self._idle_running = "what memory made while it slept" if kind == "slept" else "the notes another model wrote"
            self._write_idle(now, len(items))
            try:
                return (self.slept_moment if kind == "slept" else self.old_notes_moment)(path, item, now=now)
            except Exception as e:
                logger.warning("thymos %s moment failed: %s", kind, e)
                return self._item_retry(kind, path, item, f"the call failed: {e}", now)
            finally:
                self._idle_running = ""
                self._write_idle(time.time(), len(self.due_conversations()) + len(self._items(time.time())))
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
        identity = self.soul()
        if identity is None:
            seed = last(self.chain.entries(), "seed")
            identity = seed["text"] if seed else ""
        system = (identity + "\n\n" if identity else "") + self.render(self.notes())
        messages = [{"role": "system", "content": system}] + list(conv.get("conversation") or [])
        messages.append({"role": "user", "content": ACCOUNT_INVITATION.format(
            occasion=occasion, facts=json.dumps(facts, ensure_ascii=False))})
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
        identity = self.soul()
        if identity is None:
            seed = last(self.chain.entries(), "seed")
            identity = seed["text"] if seed else ""
        return (identity + "\n\n" if identity else "") + self.render(self.notes())

    def _items(self, now: float) -> List[Tuple[str, Path, Dict[str, Any]]]:
        """Moments waiting from the memory provider, oldest first: ("slept", path, item) and ("old_notes", ...)."""
        out = []
        try:
            paths = sorted((self.data / "slept").glob("*.json"))
        except OSError:
            paths = []
        for p in paths:
            item = self._read_json(p)
            if item is not None and now >= float(item.get("next_try_at") or 0):
                out.append(("slept", p, item))
        old = self.data / "old-notes.json"
        item = self._read_json(old) if old.exists() else None
        if item is not None and now >= float(item.get("next_try_at") or 0):
            out.append(("old_notes", old, item))
        return out

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
        listing = "\n\n".join(f"DREAM {n}" + (f" (the memory system also made {d['pictures']} picture(s) of it)"
                                               if d.get("pictures") else "") + f":\n{d['text']}"
                               for n, d in enumerate(dreams, 1))
        many = len(dreams) > 1
        messages = [{"role": "system", "content": self._identity_system()},
                    {"role": "user", "content": SLEPT_INVITATION.format(
                        facts=json.dumps(facts, ensure_ascii=False),
                        what=f"{len(dreams)} dreams" if many else "a dream",
                        whose=("You did not write them, and they are not things that happened." if many else
                               "You did not write it, and it is not something that happened."),
                        it="any of them" if many else "it", dreams=listing)}]
        result, served_provider, served = self._ask_home(messages, "thymos.slept")
        if not same_model(served, home):
            return self._item_retry("slept", path, item, f"the moment was served by {served or 'an unknown model'}, not "
                                                         f"her home model {home}; nothing was written", now)
        answer = parse_dream_thoughts(getattr(result, "text", ""), len(dreams))
        if answer is None:
            return self._item_retry("slept", path, item, "her reply was not in the asked-for form, so nothing was written", now)
        thoughts, entries = answer
        label = model_label(served_provider, served)
        from . import __version__
        with self._lock:
            for which, words in thoughts:
                d = dreams[which - 1]
                stored = self.chain.append("dream_thoughts", author="self", text=words, model=label,
                                           facts={"dream_id": d.get("id"), "memory_slept_at": slept})
                name = f"{int(stored['at'] * 1000)}-{d.get('id')}.json"
                self._write_json(self.data / "dream-thoughts" / name, {
                    "dream_id": d.get("id"), "thoughts": words, "entry_hash": stored["hash"], "entry_id": stored["id"],
                    "model": label, "written_at": stored["at"], "service": f"thymos/{__version__}"})
            for e in entries:
                self.chain.append("state", author="self", text=e["entry"],
                                  visibility="unlisted" if e["unlisted"] else "shared", model=label)
        self._file_done(path, item, {"at": now, "dream_thoughts": len(thoughts), "wrote": len(entries), "model": label})
        return self._outcome(kind="slept", dream_thoughts=len(thoughts), dreams=len(dreams), wrote=len(entries), model=label)

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
                self._save_state(state)
        return "A reflection moment will open after this reply."

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
        identity = self.soul()
        if identity is None:
            seed = last(self.chain.entries(), "seed")
            identity = seed["text"] if seed else ""
        system = (identity + "\n\n" if identity else "") + self.render(self.notes())
        messages = [{"role": "system", "content": system}] + list(pending.get("conversation") or [])
        occasion = pending.get("occasion", "requested")
        messages.append({"role": "user", "content": INVITATION.format(
            occasion=occasion + (" (you asked for this moment)" if occasion == "requested" else ""),
            facts=json.dumps(facts, ensure_ascii=False))})
        result = self.llm.complete(messages, max_tokens=int(self.cfg["reflection_max_tokens"]),
                                   timeout=float(self.cfg["reflection_timeout"]), purpose="thymos.reflection")
        served_provider, served = getattr(result, "provider", "") or "", getattr(result, "model", "") or ""
        if not same_model(served, home):
            return self._outcome(problem=f"the moment was served by {served or 'an unknown model'}, not her home "
                                         f"model {home}; nothing was written and the request stays pending")
        entries = parse(getattr(result, "text", ""))
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
        return self._outcome(wrote=len(written), model=model_label(served_provider, served),
                             problem="" if entries is not None else
                             "her reply was not in the asked-for form, so nothing was written")

    def _outcome(self, **fields: Any) -> Dict[str, Any]:
        """How the last moment went, for `hermes persona status`.  Each kind of idle moment is kept apart."""
        key = {"account": "last_account", "slept": "last_slept", "old_notes": "last_old_notes"}.get(
            fields.pop("kind", ""), "last_reflection")
        with self._lock:
            state = self._state()
            state[key] = dict(at=time.time(), **fields)
            self._save_state(state)
            return state[key]
