"""The private moment: what she is asked after a reply, and how her answer is read.

Nothing in this file talks to Hermes or to a model.  It turns a conversation into the
messages for one model call, and turns the reply into words and a number.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .config import OPEN

QUESTION = """\
[Thymos: a private moment. This message is not from {person}. It comes from the part of your own system that keeps track of how you feel.]

The exchange above has just finished, and your reply has already been delivered. Take a moment for yourself.

How did that exchange leave you? Write a sentence or two, in the first person, in your own words. There is no list of feelings to choose from and no right answer. If nothing much stirred, say so: that is a true answer, and a common one.

Then finish with one last line in exactly this form:
intensity: N
where N is a whole number from 0 (nothing stirred) to 10 (as strongly as you feel anything).

Who can read this: {reader}

Do not address {person}, do not continue the conversation, and do not call any tools."""

# What she is told about who can read the note.  It has to be true: the store records the
# visibility each note was written under, and `hermes thymos log` shows only what she was
# told could be seen.
READERS = {
    OPEN: ("{person_cap} can read these notes for now, while this part of you is being built and "
           "tested. You will be told here, plainly, when that changes."),
}

_INTENSITY = re.compile(r"^\W*intensity\W*?[:=]\s*\**\s*(\d+(?:\.\d+)?)\s*(?:/\s*10)?\W*$", re.IGNORECASE)
_THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.IGNORECASE | re.DOTALL)
_CUT = " [...]"


def person(cfg: Dict[str, Any]) -> str:
    return cfg.get("person_name") or "the person you were talking with"


def question(cfg: Dict[str, Any]) -> str:
    """The question, saying who can read the answer.  Raises KeyError for a visibility this
    version has no true sentence for, so a moment is never asked under a false one."""
    who = person(cfg)
    reader = READERS[cfg["visibility"]].format(person_cap=who[:1].upper() + who[1:])
    return QUESTION.format(person=who, reader=reader)


def _text_of(content: Any) -> str:
    """The words of one message.  Hermes passes a string, or a list of parts when a picture or
    other attachment came with it; an attachment is noted, never sent again."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            kind = str(part.get("type") or "")
            if kind in ("text", "input_text", "output_text") and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif "image" in kind:
                parts.append("[an image]")
            elif "audio" in kind:
                parts.append("[an audio clip]")
            elif kind:
                parts.append("[an attachment]")
    return "\n".join(p.strip() for p in parts if p and p.strip())


def _cut(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(_CUT))].rstrip() + _CUT


def recent_exchange(history: Any, reply: str, cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """The last few things said, as plain user and assistant messages that strictly alternate.

    Tool calls, tool results and system messages are left out: this is about what was said
    between the two of them.  Neighbouring messages from the same side are joined, because
    some chat templates (Gemma's among them) refuse two in a row.  The result starts with the
    user and ends with her reply.
    """
    limit = int(cfg["max_message_chars"])
    turns: List[Dict[str, str]] = []
    for message in history if isinstance(history, list) else []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _text_of(message.get("content"))
        if not text:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})

    reply = (reply or "").strip()
    if reply and (not turns or turns[-1]["role"] != "assistant"):
        turns.append({"role": "assistant", "content": reply})   # the history did not carry the reply itself

    keep = int(cfg["history_messages"])
    if keep > 0:
        turns = turns[-keep:]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    while turns and turns[-1]["role"] != "assistant":
        turns.pop()
    return [{"role": t["role"], "content": _cut(t["content"], limit)} for t in turns]


def build_messages(history: Any, reply: str, soul: str, cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Everything sent for one moment: who she is, the recent exchange, then the question.
    Returns [] when there is no finished exchange to ask about."""
    turns = recent_exchange(history, reply, cfg)
    if not turns:
        return []
    messages: List[Dict[str, str]] = []
    soul = _cut((soul or "").strip(), int(cfg["soul_max_chars"])) if int(cfg["soul_max_chars"]) > 0 else ""
    if soul:
        messages.append({"role": "system", "content": soul})
    messages.extend(turns)
    messages.append({"role": "user", "content": question(cfg)})
    return messages


def read_reply(text: Any) -> Tuple[str, Optional[float]]:
    """Her words, and how strongly (0 to 10), or None when she gave no number.

    The number is looked for on the last line that has one.  A reply with words and no
    number is still kept: the words are the point, and a missing number is worth seeing
    while this is being tuned.
    """
    if not isinstance(text, str):
        return "", None
    text = _THINK.sub("", text).strip()
    lines = text.splitlines()
    intensity: Optional[float] = None
    for index in range(len(lines) - 1, -1, -1):
        found = _INTENSITY.match(lines[index].strip())
        if found:
            intensity = min(10.0, max(0.0, float(found.group(1))))
            del lines[index]
            break
    words = "\n".join(lines).strip()
    return words, intensity
