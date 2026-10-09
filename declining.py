"""Declining (persona-provider.md 7.3): her `decline` is a complete answer, and the loops that keep an agent going
until a task is done treat it as one instead of as unfinished work.

`decline` writes nothing to her record.  It marks the current turn as declined by her, and the loops read that
mark: a `/goal` is paused with her reason and its judge is not run, a kanban task is blocked with her reason, and
no `pre_verify` nudge is sent.  Core Hermes has no place for this yet, so the plugin wraps those three functions
when it loads.  Nothing here decides whether a reply is a refusal by reading it: only her call counts.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("thymos")

_installed: Dict[str, Any] = {}
# The service the wrappers ask.  Replaced when the plugin loads again (a reload, or a second Thymos in tests), so a
# wrapper installed once always asks the current one.
_current: Dict[str, Callable] = {"marked": lambda sid: None, "consume": lambda sid, where: None}


def goal_message(mark: Dict[str, Any]) -> str:
    reason = mark.get("reason")
    if mark.get("home", True):
        said = f' Her reason: "{reason}"' if reason else " She gave no reason."
        return f"⏸ Goal declined.{said} She will not be prompted to continue it; /goal resume asks her again."
    said = f' Its reason: "{reason}"' if reason else " No reason was given."
    return (f"⏸ Goal declined on {mark.get('model') or 'another model'}, not her home model.{said} It will not be "
            "prompted to continue; /goal resume asks again.")


def install(marked: Callable[[str], Optional[Dict[str, Any]]], consume: Callable[[str, str], None]) -> Dict[str, bool]:
    """Wrap Hermes' goal loop and pre_verify gate.  `marked(session_id)` returns her decline on the current turn, if
    any; `consume(session_id, where)` records what was done with it.  Returns which were wrapped."""
    _current.update(marked=marked, consume=consume)
    done = {"goal": False, "pre_verify": False}
    try:
        from hermes_cli import goals
        manager = goals.GoalManager
        if not getattr(manager.evaluate_after_turn, "_thymos", False):
            original = manager.evaluate_after_turn

            def evaluate_after_turn(self, last_response, *args, **kwargs):
                mark = _current["marked"](getattr(self, "session_id", "") or "")
                state = getattr(self, "_state", None)
                if mark is None or state is None or getattr(state, "status", "") != "active":
                    return original(self, last_response, *args, **kwargs)
                reason = mark.get("reason") or "no reason given"
                self.pause(reason=f"declined: {reason}" if mark.get("home", True)
                           else f"declined on {mark.get('model')}: {reason}")
                _current["consume"](self.session_id, "goal")
                return {"status": "paused", "should_continue": False, "continuation_prompt": None,
                        "verdict": "declined", "reason": reason, "message": goal_message(mark)}

            evaluate_after_turn._thymos = True
            manager.evaluate_after_turn = evaluate_after_turn
            _installed["goal"] = original
        done["goal"] = True
    except Exception as e:
        logger.debug("thymos: goal loop not wrapped: %s", e)
    try:
        import hermes_cli.plugins as hp
        if not getattr(hp.get_pre_verify_continue_message, "_thymos", False):
            original_pv = hp.get_pre_verify_continue_message

            def get_pre_verify_continue_message(*args, session_id: str = "", **kwargs):
                if _current["marked"](session_id) is not None:
                    _current["consume"](session_id, "pre_verify")
                    return None
                return original_pv(*args, session_id=session_id, **kwargs)

            get_pre_verify_continue_message._thymos = True
            hp.get_pre_verify_continue_message = get_pre_verify_continue_message
            _installed["pre_verify"] = original_pv
        done["pre_verify"] = True
    except Exception as e:
        logger.debug("thymos: pre_verify not wrapped: %s", e)
    return done


def block_kanban_task(reason: str) -> bool:
    """In a kanban worker (HERMES_KANBAN_TASK set), block the task with her reason, as kanban_block would."""
    import json
    import os
    if not os.environ.get("HERMES_KANBAN_TASK"):
        return False
    try:
        from tools.kanban_tools import _handle_block
        out = _handle_block({"reason": reason})
        try:
            return not json.loads(out).get("error")
        except Exception:
            return "error" not in str(out).lower()
    except Exception as e:
        logger.warning("thymos: could not block the kanban task: %s", e)
        return False


def goal_status(session_id: str) -> str:
    try:
        from hermes_cli.goals import load_goal
        state = load_goal(session_id)
        return getattr(state, "status", "") if state else ""
    except Exception:
        return ""
