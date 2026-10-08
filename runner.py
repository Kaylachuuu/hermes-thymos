"""Takes the private moment after a reply.

Hermes calls :meth:`Thymos.after_reply` from its ``post_llm_call`` hook, inside the turn.
That call returns at once: the question is put to the model on a separate thread, after
the person already has their answer, and the result is written to the store.

Nothing here may raise into Hermes.  Whatever goes wrong becomes a row in the store with
its reason, so a failure is something to read afterwards and never something that breaks a turn.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import config as _config
from . import moment as _moment
from .store import ERROR, FELT, SKIPPED, Store

logger = logging.getLogger(__name__)

PURPOSE = "thymos.moment"


def _thread(target: Callable[..., Any], *, name: str, kwargs: Dict[str, Any]) -> threading.Thread:
    """A background thread that carries Hermes' per-session context with it, when run inside Hermes."""
    try:
        from agent.memory_provider import spawn_context_thread
        return spawn_context_thread(target, name=name, kwargs=kwargs)
    except Exception:
        return threading.Thread(target=target, name=name, kwargs=kwargs, daemon=True)


def read_soul() -> str:
    """SOUL.md from the Hermes home folder: who she is, as Hermes itself tells her."""
    try:
        from hermes_constants import get_hermes_home
        path = Path(get_hermes_home()) / "SOUL.md"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except Exception:
        return ""


class Thymos:
    def __init__(self, *, llm: Any, get_config: Callable[[str, Any], Any], store: Optional[Store] = None,
                 store_factory: Optional[Callable[[], Store]] = None, soul: Callable[[], str] = read_soul,
                 background: bool = True) -> None:
        self._llm = llm
        self._get_config = get_config
        self._store = store
        self._store_factory = store_factory
        self._soul = soul
        self._background = background
        self._busy = threading.Lock()            # one moment at a time: they share her model with her replies
        self._store_lock = threading.Lock()
        self._warned_sealed = False
        self.last_thread: Optional[threading.Thread] = None

    # -- the store is opened on first use, so loading the plugin never touches the disk --

    def store(self) -> Store:
        with self._store_lock:
            if self._store is None:
                if self._store_factory is None:
                    from .store import default_path
                    self._store = Store(default_path())
                else:
                    self._store = self._store_factory()
            return self._store

    def config(self) -> Dict[str, Any]:
        return _config.load(self._get_config)

    # -- the hook --

    def after_reply(self, session_id: str = "", user_message: Any = None, assistant_response: Any = "",
                    conversation_history: Any = None, model: str = "", platform: str = "", **kwargs: Any) -> None:
        """``post_llm_call``: a turn finished with a reply.  Returns at once; never raises."""
        try:
            self._after_reply(session_id=session_id or "", reply=assistant_response, history=conversation_history,
                              platform=platform or "", turn_id=str(kwargs.get("turn_id") or ""))
        except Exception:
            logger.exception("thymos: the hook failed before a moment could start")

    def _after_reply(self, *, session_id: str, reply: Any, history: Any, platform: str, turn_id: str) -> None:
        cfg = self.config()
        if not cfg["enabled"]:
            return
        if cfg["visibility"] != _config.OPEN:
            # Sealed entries are not built yet.  Ask nothing and store nothing, not even a skipped row.
            if not self._warned_sealed:
                self._warned_sealed = True
                logger.warning("thymos: visibility is not 'open', and sealed entries are not built in this "
                               "version, so no moments are being taken")
            return
        where = dict(session_id=session_id, turn_id=turn_id, platform=platform, visibility=cfg["visibility"])
        if not isinstance(reply, str) or not reply.strip():
            return
        if platform.strip().lower() in cfg["skip_platforms"]:
            return
        if not self._busy.acquire(blocking=False):
            self.store().add(status=SKIPPED, note="the previous moment was still running", **where)
            return
        try:
            kwargs = dict(cfg=cfg, history=list(history) if isinstance(history, list) else [], reply=reply, where=where)
            if self._background:
                thread = _thread(self._moment_then_release, name="thymos-moment", kwargs=kwargs)
                self.last_thread = thread
                thread.start()
            else:
                self._moment_then_release(**kwargs)
        except Exception:
            self._busy.release()
            raise

    def _moment_then_release(self, **kwargs: Any) -> None:
        try:
            self.take_moment(**kwargs)
        except Exception:
            logger.exception("thymos: a moment failed outside the model call")
        finally:
            self._busy.release()

    # -- the moment itself --

    def take_moment(self, *, cfg: Dict[str, Any], history: Any, reply: str, where: Dict[str, str]) -> Optional[int]:
        """Ask, read the answer, store it.  Returns the row id, or None when there was nothing to ask about."""
        messages = _moment.build_messages(history, reply, self._soul(), cfg)
        if not messages:
            return None
        started = time.monotonic()
        try:
            result = self._llm.complete(messages, max_tokens=cfg["max_tokens"] or None, temperature=cfg["temperature"],
                                        timeout=cfg["timeout"] or None, purpose=PURPOSE)
        except Exception as exc:
            return self.store().add(status=ERROR, note=f"{type(exc).__name__}: {exc}"[:500],
                                    duration_ms=_ms(started), **where)
        took = _ms(started)
        raw = getattr(result, "text", "") or ""
        usage = getattr(result, "usage", None)
        about = dict(provider=getattr(result, "provider", "") or "", model=getattr(result, "model", "") or "",
                     tokens=int(getattr(usage, "total_tokens", 0) or 0), duration_ms=took, raw=raw)
        words, intensity = _moment.read_reply(raw)
        if not words:
            return self.store().add(status=ERROR, note="the reply had no words in it", **about, **where)
        note = "" if intensity is not None else "no intensity line"
        return self.store().add(status=FELT, words=words, intensity=intensity, note=note, **about, **where)


def _ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))
