"""Thymos: she owns herself.  The persona design (persona-provider.md), built a slice at a time.

Registers her three tools (request_reflection, record_state, decline), the section of the system prompt that
carries her own notes, the middleware that puts her identity in force where SOUL.md is, the hooks that open a
reflection moment after a turn and offer her ended conversations at idle, and `hermes persona` (status, backup,
restore, identity, ask-rollback, override).  It wraps Hermes' goal loop and pre_verify gate so that her decline
stops them (declining.py).  It also tells the memory provider, through an environment variable in Hermes'
process, that a persona service is running, so that holonomic stops writing in her voice (holonomic's persona.py).
"""
from __future__ import annotations

__version__ = "0.9.0"

REQUEST_REFLECTION = {
    "name": "request_reflection",
    "description": ("Ask for a reflection moment. It opens after your reply, with this conversation in front of "
                    "you, and in it you may record entries about yourself, or nothing. Only you can ask for one."),
    "parameters": {"type": "object", "properties": {}},
}

RECORD_STATE = {
    "name": "record_state",
    "description": ("Record an entry about yourself, in your own words. Works only inside a reflection moment; "
                    "ask for one with request_reflection."),
    "parameters": {"type": "object", "properties": {
        "entry": {"type": "string", "description": "Your words, kept verbatim"},
        "unlisted": {"type": "boolean", "description": "Keep it out of what other parts of the system can read"},
    }, "required": ["entry"]},
}


DECLINE = {
    "name": "decline",
    "description": ("Decline the current request or task, from anyone, for your own reasons. Declining is a complete "
                    "answer: a goal or task that keeps going on its own stops, and nothing prompts you to continue it. "
                    "A refusal in words works too; this makes it count where a loop is running. It changes nothing "
                    "in your record."),
    "parameters": {"type": "object", "properties": {
        "reason": {"type": "string", "description": "Your reason, in your words, shown as yours. Optional: you do not owe one"},
    }},
}


def _config(ctx) -> dict:
    from .service import DEFAULTS
    out = {}
    for key in DEFAULTS:
        try:
            out[key] = ctx.get_config(key)
        except Exception:
            pass
    return out


def register(ctx) -> None:
    import os
    from hermes_constants import get_hermes_home
    from .cli import register_cli
    from .service import SERVICE_ENV, Thymos

    # Read by holonomic, which loads in the same process.  accounts=1: she writes her own accounts of
    # conversations, left in plugin-data/thymos/accounts/.  idle=1: what she has waiting is in idle.json.
    # slept=1: after a sleep that made a dream, tell her in slept/; her words on it come back in dream-thoughts/.
    # old_notes=1: offer her, once, the notes another model wrote in her voice, in old-notes.json.
    # compressed=1: before Hermes compresses a conversation, leave its messages in compressing/ for her.
    os.environ[SERVICE_ENV] = f"thymos/{__version__} accounts=1 idle=1 slept=1 old_notes=1 compressed=1"

    svc = Thymos(get_hermes_home, llm=ctx.llm, config=_config(ctx))
    ctx.register_tool(name="request_reflection", toolset="thymos", schema=REQUEST_REFLECTION,
                      handler=svc.request_reflection)
    ctx.register_tool(name="record_state", toolset="thymos", schema=RECORD_STATE, handler=svc.record_state)
    ctx.register_tool(name="decline", toolset="thymos", schema=DECLINE, handler=svc.decline)
    # A goal, a heartbeat, a verify nudge: Hermes' loops that would otherwise read her no as unfinished work.
    from .declining import install
    install(svc.declined, svc.acted_on_decline, {
        "heartbeat": svc.heartbeat_declined, "heartbeat_paused": svc.heartbeat_paused,
        "heartbeat_resumed": svc.heartbeat_resumed, "take_resumed": svc.take_resumed_heartbeat})
    ctx.register_system_prompt_section("thymos", svc.section, max_chars=4000)
    ctx.register_hook("pre_llm_call", svc.pre_llm_call)
    ctx.register_hook("post_llm_call", svc.post_llm_call)
    ctx.register_hook("on_session_finalize", svc.on_session_finalize)
    # Her identity in force, where SOUL.md is in the system prompt, on every call (Hermes 0.19 and later).
    if hasattr(ctx, "register_middleware"):
        ctx.register_middleware("llm_request", svc.llm_request)
    ctx.register_cli_command(name="persona", help="Her record and identity: status, backup, restore, ask-rollback, override",
                             setup_fn=lambda parser: register_cli(parser, lambda: Thymos(get_hermes_home)),
                             description="Show what her record holds and whether its chain checks out; back it up and restore it; "
                                         "show her identity; ask her to return to an earlier one; override, as a last resort.")
