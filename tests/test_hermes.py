"""The plugin loaded and driven by Hermes itself: its plugin manager, hook dispatch, prompt sections, CLI
wiring and ctx.llm.  Only the model call is replaced.  Needs Hermes' Python environment; elsewhere these
tests return without checking anything (see README, Testing)."""
import argparse
import contextlib
import io
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

try:
    import hermes_cli.plugins as _hp  # noqa: F401
    HAVE_HERMES = True
except Exception:
    HAVE_HERMES = False


def _install(tmp_path):
    home = tmp_path / "hermes-home"
    dest = home / "plugins" / "thymos"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "tests", "scripts", "__pycache__"))
    (home / "SOUL.md").write_text("You are Athena.", encoding="utf-8")
    (home / "config.yaml").write_text(
        "model:\n  provider: ollama\n  default: gemma3:12b\nplugins:\n  enabled: [thymos]\n", encoding="utf-8")
    return home


@contextlib.contextmanager
def _hermes(home, reply):
    """A fresh plugin manager for `home`, with the provider call answered by `reply`."""
    import agent.auxiliary_client as aux
    import hermes_cli.plugins as hp
    old_env, old_call = os.environ.get("HERMES_HOME"), aux.call_llm
    os.environ["HERMES_HOME"] = str(home)
    sent = []

    def fake_call_llm(**kw):
        sent.append(kw)
        msg = SimpleNamespace(content=reply, role="assistant", tool_calls=None)
        return SimpleNamespace(model="gemma3:12b", choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    aux.call_llm = fake_call_llm
    try:
        hp._reset_plugin_managers_for_tests()
        hp.discover_plugins(force=True)
        yield hp.get_plugin_manager(), sent
    finally:
        aux.call_llm = old_call
        hp._reset_plugin_managers_for_tests()
        if old_env is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_env


def test_hermes_loads_the_plugin_and_runs_a_reflection_moment(tmp_path):
    if not HAVE_HERMES:
        return
    from hermes_cli.lifecycle import invoke_hook
    from tools.registry import registry
    home = _install(tmp_path)
    with _hermes(home, '{"record_state": ["Written through Hermes."]}') as (mgr, sent):
        assert "thymos" in [p["name"] for p in mgr.list_plugins() if p.get("enabled", True)] or mgr._plugin_tool_names
        assert {"request_reflection", "record_state"} <= set(mgr._plugin_tool_names)
        info = {"session_id": "s1", "model": "gemma3:12b", "provider": "ollama", "platform": "cli",
                "profile_name": "default", "cwd": str(tmp_path)}
        sections = mgr.render_system_prompt_sections(info)
        assert [s.id for s in sections] == ["thymos"] and "Only you write here." in sections[0].content
        invoke_hook("pre_llm_call", session_id="s1", task_id="t", turn_id="1", user_message="hi",
                    conversation_history=[], is_first_turn=True, model="gemma3:12b", platform="cli",
                    parent_session_id="", sender_id="")
        out = registry.dispatch("request_reflection", {}, task_id="t", session_id="s1")
        assert "will open after this reply" in out
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."}]
        invoke_hook("post_llm_call", session_id="s1", task_id="t", turn_id="1", user_message="hi",
                    assistant_response="Hello.", conversation_history=history, model="gemma3:12b", platform="cli")
        got = invoke_hook("pre_llm_call", session_id="s1", task_id="t", turn_id="2", user_message="and now?",
                          conversation_history=history, is_first_turn=False, model="gemma3:12b", platform="cli",
                          parent_session_id="", sender_id="")
        assert sent and sent[0]["messages"][0]["content"].startswith("You are Athena.")
        assert any("Written through Hermes." in (r.get("context", "") if isinstance(r, dict) else str(r)) for r in got)
        entries = (home / "self" / "entries.jsonl").read_text(encoding="utf-8")
        assert "Written through Hermes." in entries and "ollama|gemma3:12b" in entries

        cmd = mgr._cli_commands["persona"]
        parser = argparse.ArgumentParser()
        cmd["setup_fn"](parser)
        if cmd.get("handler_fn") is not None:
            parser.set_defaults(func=cmd["handler_fn"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            args = parser.parse_args(["status"])
            args.func(args)
        text = buf.getvalue()
        assert "chain: verified" in text and "home model: gemma3:12b" in text and "configured main model" not in text
