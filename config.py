"""Settings for thymos.

Hermes keeps a plugin's settings in config.yaml under
``plugins.entries.thymos.settings``; ``ctx.get_config`` reads them.  They are read
again for every moment, so an edit takes effect on the next turn without a restart.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

OPEN = "open"
SEALED = "sealed"

DEFAULTS: Dict[str, Any] = {
    # The switch.  Off: the hook returns at once and nothing is asked or stored.
    "enabled": True,
    # Who may read what she writes.  "open": the person running Hermes can read every
    # entry, and she is told so in the question itself.  "sealed" (hers until she chooses
    # to share) is not built yet: while it is set, no moment is taken at all, so nothing
    # is ever stored under a promise this version cannot keep.
    "visibility": OPEN,
    # How the question names the person she talks with.  Empty: "the person you were talking with".
    "person_name": "",
    # How much of the conversation she is shown again: the last N user and assistant
    # messages, each cut to max_message_chars.  This bounds what a moment costs.
    "history_messages": 12,
    "max_message_chars": 2000,
    # Most of SOUL.md that is sent as the system message.  0 leaves it out.
    "soul_max_chars": 6000,
    # The reply.  temperature None leaves the model's own setting alone.
    "max_tokens": 300,
    "temperature": None,
    "timeout": 120,
    # Platforms on which no moment is taken (the `platform` Hermes reports for the turn).
    "skip_platforms": [],
}


def load(get_config: Callable[[str, Any], Any]) -> Dict[str, Any]:
    """Every setting, with the default wherever config.yaml says nothing or says nonsense."""
    cfg: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        try:
            value = get_config(key, default)
        except Exception:
            value = default
        cfg[key] = _coerce(key, value, default)
    return cfg


def _coerce(key: str, value: Any, default: Any) -> Any:
    if key == "temperature":
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if key == "visibility":
        text = str(value or "").strip().lower()
        # Anything that is not plainly "open" is treated as sealed: a typo must never open entries.
        return OPEN if text == OPEN else SEALED
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return default
    if isinstance(default, int):
        if isinstance(value, bool):
            return default
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return list(default)
        return [str(item).strip().lower() for item in value if str(item).strip()]
    if isinstance(default, str):
        return str(value or "").strip()
    return value
