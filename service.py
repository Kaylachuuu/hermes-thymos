"""Her side of the conversation loop: the seed, her notes in the prompt, her two tools, and the reflection moment.

First slice of persona-provider.md (section 10).  Core Hermes has no persona service yet, so this runs as an
ordinary plugin.  What that costs is written down in the README ("What a plugin cannot guarantee").
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
}

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


def parse(text: str) -> Optional[List[Dict[str, Any]]]:
    """Her entries from a reflection reply, or None when the reply is not in the asked-for form.
    Only what she put under record_state counts: prose around it is not an entry."""
    t = _THINK.sub("", text or "").strip()     # a reasoning model's thinking is not what she recorded
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict) or "record_state" not in obj:
        return None
    raw = obj["record_state"]
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
        pending = self._state().get("pending")
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
        with self._lock:
            state = self._state()
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
            self.reflect(pending, deferred=deferred)
        except Exception as e:      # a failed moment stays pending: deferred, never refused
            logger.warning("thymos reflection failed: %s", e)
            self._outcome(problem=f"the reflection call failed: {e}; the request stays pending")
        finally:
            self._done.set()

    def reflect(self, pending: Dict[str, Any], *, deferred: bool = False) -> Dict[str, Any]:
        provider, home = self.home_model()
        facts: Dict[str, Any] = {"now": _when(time.time()), "asked_for_at": _when(pending.get("requested_at", time.time())),
                                 "messages_in_conversation": pending.get("message_count", 0)}
        if deferred:
            hours = (time.time() - pending.get("ended_at", time.time())) / 3600
            facts["conversation_ended_hours_ago"] = round(hours, 1)
            facts["note"] = "this conversation ended before the moment could open; it is replayed from a saved copy"
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
        with self._lock:
            state = self._state()
            state["last_reflection"] = dict(at=time.time(), **fields)
            self._save_state(state)
            return state["last_reflection"]
