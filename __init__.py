"""Thymos: personality and emotions for Hermes Agent.

This version does one thing.  After each reply, the agent is asked privately, on its own
model, how the exchange left it; it answers in its own words with one number for how
strongly, and that is stored.  Mood, memory, behaviour and growth are built on top of
this later.  See README.md.

Hermes loads this package from ``$HERMES_HOME/plugins/thymos/`` and calls ``register()``.
"""

__version__ = "0.1.0"
__all__ = ["register"]


def register(ctx) -> None:
    """Entry point used by Hermes."""
    from . import cli
    from .runner import Thymos

    thymos = Thymos(llm=ctx.llm, get_config=ctx.get_config)
    ctx.register_hook("post_llm_call", thymos.after_reply)
    ctx.register_cli_command(
        name="thymos", help="Thymos: how the agent says each exchange left it",
        setup_fn=cli.setup, handler_fn=cli.make_handler(thymos),
        description="Read the private moments the agent has written, and check that they are being taken.")
