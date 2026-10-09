"""Thymos: she owns herself.  The persona design (persona-provider.md), built a slice at a time.

Registers her two tools, the section of the system prompt that carries her own notes, the hooks that open a
reflection moment after a turn and offer her ended conversations at idle, and `hermes persona status`.  It also
tells the memory provider, through an environment variable in Hermes' process, that a persona service is running,
so that holonomic stops writing in her voice (holonomic's persona.py).
"""
from __future__ import annotations

__version__ = "0.3.0"

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
    os.environ[SERVICE_ENV] = f"thymos/{__version__} accounts=1 idle=1"

    svc = Thymos(get_hermes_home, llm=ctx.llm, config=_config(ctx))
    ctx.register_tool(name="request_reflection", toolset="thymos", schema=REQUEST_REFLECTION,
                      handler=svc.request_reflection)
    ctx.register_tool(name="record_state", toolset="thymos", schema=RECORD_STATE, handler=svc.record_state)
    ctx.register_system_prompt_section("thymos", svc.section, max_chars=4000)
    ctx.register_hook("pre_llm_call", svc.pre_llm_call)
    ctx.register_hook("post_llm_call", svc.post_llm_call)
    ctx.register_hook("on_session_finalize", svc.on_session_finalize)
    ctx.register_cli_command(name="persona", help="Her record: status and checks",
                             setup_fn=lambda parser: register_cli(parser, lambda: Thymos(get_hermes_home)),
                             description="Show what her record holds and whether its chain checks out.")
