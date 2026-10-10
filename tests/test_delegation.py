"""Delegation finished (persona-provider.md 4.2): when subagents she started come back, a moment opens after her
reply, with the facts of each: her task, how it ended, how long it took, its tool calls.  One moment per turn."""
from test_service import HISTORY, FakeLlm, make, start, turn_end


def test_a_moment_opens_after_the_reply_in_which_her_subagents_came_back(tmp_path):
    llm = FakeLlm('{"record_state": ["I liked handing that off."]}')
    svc = make(tmp_path, llm)
    start(svc)
    svc.subagent_start(parent_session_id="s1", child_session_id="c1", child_goal="Read the README and list the moments")
    svc.pre_llm_call(session_id="c1", model="gemma3:12b", platform="subagent")      # the child's own turn
    svc.subagent_stop(parent_session_id="s1", child_session_id="c1", child_status="completed", duration_ms=42000,
                      tool_call_history=[{"tool_name": "read_file", "status": "ok"},
                                         {"tool_name": "read_file", "status": "error"}])
    svc.subagent_start(parent_session_id="s1", child_session_id="c2", child_goal="Check the tests")
    svc.subagent_stop(parent_session_id="s1", child_session_id="c2", child_status="failed", duration_ms=1000)
    turn_end(svc)
    invite = llm.calls[-1][-1]["content"]
    assert "Occasion: 2 subagents you started finished" in invite
    assert '"task": "Read the README and list the moments"' in invite and '"tool_calls_failed": 1' in invite
    assert '"ended": "failed"' in invite and '"took_seconds": 42' in invite
    assert svc.notes()[-1]["text"] == "I liked handing that off."
    calls = len(llm.calls)
    start(svc)                          # the next turn: nothing came back in it, so no moment
    turn_end(svc)
    assert len(llm.calls) == calls


def test_a_subagents_own_subagents_open_nothing(tmp_path):
    llm = FakeLlm()
    svc = make(tmp_path, llm)
    start(svc)
    svc.pre_llm_call(session_id="c1", model="gemma3:12b", platform="subagent")
    svc.subagent_start(parent_session_id="c1", child_session_id="g1", child_goal="Deeper")
    svc.subagent_stop(parent_session_id="c1", child_session_id="g1", child_status="completed")
    turn_end(svc)
    assert llm.calls == [] and svc._delegations == {} and svc._goals == {}
