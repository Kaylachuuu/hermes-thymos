"""Thymos: she owns herself.  First slice of the persona design (persona-provider.md, section 10).

Registers her two tools, the section of the system prompt that carries her own notes, the hooks that open a
reflection moment after a turn, and `hermes persona status`.
"""
from __future__ import annotations

__version__ = "0.2.0"

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
    from hermes_constants import get_hermes_home
    from .cli import register_cli
    from .service import Thymos

    svc = Thymos(get_hermes_home, llm=ctx.llm, config=_config(ctx))
    ctx.register_tool(name="request_reflection", toolset="thymos", schema=REQUEST_REFLECTION,
                      handler=svc.request_reflection)
    ctx.register_tool(name="record_state", toolset="thymos", schema=RECORD_STATE, handler=svc.record_state)
    ctx.register_system_prompt_section("thymos", svc.section, max_chars=4000)
    ctx.register_hook("pre_llm_call", svc.pre_llm_call)
    ctx.register_hook("post_llm_call", svc.post_llm_call)
    ctx.register_cli_command(name="persona", help="Her record: status and checks",
                             setup_fn=lambda parser: register_cli(parser, lambda: Thymos(get_hermes_home)),
                             description="Show what her record holds and whether its chain checks out.")
