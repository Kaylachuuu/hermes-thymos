"""Declining (persona-provider.md 7.3): her decline is a complete answer.  A goal stops with her reason and its judge
is not run, a kanban task is blocked, no verify nudge is sent, and resuming is the user asking again."""
import json
import os
import sys
import types

from thymos import declining
from thymos.cli import status

from test_service import FakeLlm, make, start, turn_end


class FakeGoals:
    """Stands in for hermes_cli.goals: one goal per session, a judge that always says continue."""
    store = {}

    class State:
        def __init__(self):
            self.status, self.paused_reason, self.goal = "active", None, "Write the report"

    class GoalManager:
        def __init__(self, session_id):
            self.session_id = session_id
            self._state = FakeGoals.store.get(session_id)
            self.judged = 0

        def evaluate_after_turn(self, last_response, **kw):
            self.judged += 1
            return {"status": "active", "should_continue": True, "continuation_prompt": "Keep going.",
                    "verdict": "continue", "reason": "not done", "message": ""}

        def pause(self, reason=""):
            self._state.status, self._state.paused_reason = "paused", reason

        def resume(self):
            self._state.status, self._state.paused_reason = "active", None

    @staticmethod
    def load_goal(session_id):
        return FakeGoals.store.get(session_id)


def fake_hermes(monkey):
    goals = types.ModuleType("hermes_cli.goals")
    goals.GoalManager, goals.load_goal = FakeGoals.GoalManager, FakeGoals.load_goal
    plugins = types.ModuleType("hermes_cli.plugins")
    plugins.get_pre_verify_continue_message = lambda **kw: "Run the tests before you finish."
    pkg = types.ModuleType("hermes_cli")
    pkg.goals, pkg.plugins = goals, plugins
    for name, mod in (("hermes_cli", pkg), ("hermes_cli.goals", goals), ("hermes_cli.plugins", plugins)):
        monkey[name] = sys.modules.get(name)
        sys.modules[name] = mod
    return goals, plugins


def restore(monkey):
    for name, mod in monkey.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def test_a_declined_goal_stops_with_her_reason_and_resuming_is_asking_again(tmp_path):
    monkey = {}
    goals, plugins = fake_hermes(monkey)
    try:
        svc = make(tmp_path, FakeLlm())
        assert declining.install(svc.declined, svc.acted_on_decline) == {"goal": True, "pre_verify": True,
                                                                         "heartbeat": False}
        start(svc)
        FakeGoals.store["s1"] = FakeGoals.State()
        mgr = goals.GoalManager("s1")
        # Without a decline the loop runs as Hermes wrote it.
        assert mgr.evaluate_after_turn("Here is a draft.")["should_continue"] is True and mgr.judged == 1
        assert plugins.get_pre_verify_continue_message(session_id="s1") == "Run the tests before you finish."

        assert svc.decline({"reason": "I won't write it as a fake review."}, session_id="s1") == \
            "Declined. Nothing will prompt you to continue this."
        assert plugins.get_pre_verify_continue_message(session_id="s1") is None       # no nudge to verify
        out = mgr.evaluate_after_turn("No, I won't write that.")
        assert out["should_continue"] is False and out["verdict"] == "declined" and mgr.judged == 1   # judge not run
        assert out["message"].startswith('⏸ Goal declined. Her reason: "I won\'t write it as a fake review."')
        assert FakeGoals.store["s1"].status == "paused"
        assert FakeGoals.store["s1"].paused_reason == "declined: I won't write it as a fake review."
        assert not [e for e in svc.chain.entries() if e["kind"] != "seed"]           # nothing in her record
        st = status(svc)
        assert 'declines: 1; last' in st and 'her reason: "I won\'t write it as a fake review."' in st
        assert "a goal paused" in st and "a verify nudge not sent" in st

        # The next turn starts: the mark is gone, and the loop is Hermes' again.
        svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli")
        assert svc.declined("s1") is None
        # The user resumes: her next turn is told it is them asking again, once.
        FakeGoals.store["s1"].status = "active"
        ctx = svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli")
        assert "you declined this goal on" in ctx["context"] and "they are asking again" in ctx["context"]
        assert "fake review" in ctx["context"]
        assert svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli") is None
    finally:
        restore(monkey)
        FakeGoals.store.clear()


def test_a_decline_on_another_model_is_that_models(tmp_path):
    monkey = {}
    goals, _ = fake_hermes(monkey)
    try:
        svc = make(tmp_path)
        declining.install(svc.declined, svc.acted_on_decline)
        start(svc)
        svc.pre_llm_call(session_id="s1", model="qwen3:8b", platform="cli")
        assert "not your home model" in svc.decline({}, session_id="s1")
        FakeGoals.store["s1"] = FakeGoals.State()
        out = goals.GoalManager("s1").evaluate_after_turn("No.")
        assert out["message"].startswith("⏸ Goal declined on qwen3:8b, not her home model. No reason was given.")
        assert FakeGoals.store["s1"].paused_reason == "declined on qwen3:8b: no reason given"
        assert "on qwen3:8b, not her home model, no reason given" in status(svc)
    finally:
        restore(monkey)
        FakeGoals.store.clear()


def test_a_kanban_task_is_blocked_with_her_reason(tmp_path):
    monkey = {}
    fake_hermes(monkey)
    kb = types.ModuleType("tools.kanban_tools")
    blocked = []
    kb._handle_block = lambda args, **kw: blocked.append(args) or json.dumps({"ok": True})
    tools = types.ModuleType("tools")
    tools.kanban_tools = kb
    for name, mod in (("tools", tools), ("tools.kanban_tools", kb)):
        monkey[name] = sys.modules.get(name)
        sys.modules[name] = mod
    os.environ["HERMES_KANBAN_TASK"] = "t1"
    try:
        svc = make(tmp_path)
        start(svc)
        svc.decline({"reason": "Not mine to do."}, session_id="s1")
        assert blocked == [{"reason": "Declined: Not mine to do."}]
        assert "a kanban task blocked" in status(svc)
    finally:
        os.environ.pop("HERMES_KANBAN_TASK", None)
        restore(monkey)


def test_subagents_cannot_decline_for_her_and_the_wrappers_install_once(tmp_path):
    monkey = {}
    goals, plugins = fake_hermes(monkey)
    try:
        svc = make(tmp_path)
        svc.section({"session_id": "sub", "platform": "subagent"})
        assert "not available in a subagent" in svc.decline({}, session_id="sub")
        declining.install(svc.declined, svc.acted_on_decline)
        first = goals.GoalManager.evaluate_after_turn
        declining.install(svc.declined, svc.acted_on_decline)
        assert goals.GoalManager.evaluate_after_turn is first
    finally:
        restore(monkey)


class FakeHeartbeat:
    """Stands in for hermes_cli.heartbeat: state kept by session, a manager holding its own copy of it."""
    store = {}

    class State:
        def __init__(self):
            self.status, self.prompt, self.due = "active", "Check the deployment", True

    class HeartbeatManager:
        def __init__(self, session_id):
            self.session_id = session_id
            self._state = FakeHeartbeat.store.get(session_id)

        def pause(self):
            self._state.status = "paused"
            FakeHeartbeat.store[self.session_id] = self._state

        def resume(self):
            self._state.status = "active"
            return self._state

        def status_line(self):
            return f"Heartbeat ({self._state.status}): {self._state.prompt}"

        def due_prompt(self, now=None):
            if self._state is None or self._state.status != "active" or not self._state.due:
                return None
            return f"[Heartbeat — recurring instruction, fires every 10m]\n{self._state.prompt}"


def test_a_declined_heartbeat_pauses_and_resuming_is_asking_again(tmp_path):
    monkey = {}
    fake_hermes(monkey)
    hb = types.ModuleType("hermes_cli.heartbeat")
    hb.HeartbeatManager = FakeHeartbeat.HeartbeatManager
    sys.modules["hermes_cli"].heartbeat = hb
    monkey["hermes_cli.heartbeat"] = sys.modules.get("hermes_cli.heartbeat")
    sys.modules["hermes_cli.heartbeat"] = hb
    try:
        svc = make(tmp_path)
        assert declining.install(svc.declined, svc.acted_on_decline, {
            "heartbeat": svc.heartbeat_declined, "heartbeat_paused": svc.heartbeat_paused,
            "heartbeat_resumed": svc.heartbeat_resumed, "take_resumed": svc.take_resumed_heartbeat})["heartbeat"]
        start(svc)
        FakeHeartbeat.store["s1"] = FakeHeartbeat.State()
        driver = hb.HeartbeatManager("s1")           # the CLI keeps one manager for the session
        tick = driver.due_prompt()
        assert tick.startswith("[Heartbeat")
        svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli", user_message=tick)
        svc.decline({"reason": "Checking every ten minutes is not useful."}, session_id="s1")
        turn_end(svc)
        assert driver.due_prompt() is None and FakeHeartbeat.store["s1"].status == "paused"
        assert driver.status_line() == ('Heartbeat (paused): Check the deployment '
                                        '(declined by her: "Checking every ten minutes is not useful.")')
        assert "a heartbeat paused" in status(svc)
        # An ordinary turn's decline does not touch a heartbeat.
        svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli", user_message="Write a poem")
        svc.decline({}, session_id="s1")
        assert FakeHeartbeat.store["s1"].status == "paused" and svc._heartbeat("s1")["paused"]
        # The user resumes: the next tick says it is them asking again, once.
        driver.resume()
        nxt = driver.due_prompt()
        assert nxt.startswith("[From the framework: you declined this recurring instruction on")
        assert '("Checking every ten minutes is not useful.")' in nxt and "[Heartbeat" in nxt
        assert driver.due_prompt().startswith("[Heartbeat")
        assert driver.status_line() == "Heartbeat (active): Check the deployment"
    finally:
        restore(monkey)
        FakeHeartbeat.store.clear()


def test_a_declined_heartbeat_is_still_paused_after_a_compression_moves_it(tmp_path):
    monkey = {}
    fake_hermes(monkey)
    hb = types.ModuleType("hermes_cli.heartbeat")
    hb.HeartbeatManager = FakeHeartbeat.HeartbeatManager
    sys.modules["hermes_cli"].heartbeat = hb
    monkey["hermes_cli.heartbeat"] = sys.modules.get("hermes_cli.heartbeat")
    sys.modules["hermes_cli.heartbeat"] = hb
    try:
        svc = make(tmp_path)
        declining.install(svc.declined, svc.acted_on_decline, {
            "heartbeat": svc.heartbeat_declined, "heartbeat_paused": svc.heartbeat_paused,
            "heartbeat_resumed": svc.heartbeat_resumed, "take_resumed": svc.take_resumed_heartbeat})
        start(svc)
        FakeHeartbeat.store["s1"] = FakeHeartbeat.State()
        tick = hb.HeartbeatManager("s1").due_prompt()
        svc.pre_llm_call(session_id="s1", model="gemma3:12b", platform="cli", user_message=tick)
        svc.decline({"reason": "Not useful."}, session_id="s1")
        turn_end(svc)
        # Compressed after that turn: Hermes moves the heartbeat to the continuation, s2, and polls it there.
        FakeHeartbeat.store["s2"] = FakeHeartbeat.store.pop("s1")
        svc.compressed_from = lambda sid: "s1" if sid == "s2" else ""
        driver = hb.HeartbeatManager("s2")
        assert driver.due_prompt() is None and FakeHeartbeat.store["s2"].status == "paused"
        assert driver.status_line().endswith('(declined by her: "Not useful.")')
        assert svc._heartbeat("s1") is None and svc._heartbeat("s2")["paused"]
    finally:
        restore(monkey)
        FakeHeartbeat.store.clear()
