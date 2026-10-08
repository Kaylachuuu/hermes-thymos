import threading
from types import SimpleNamespace

from thymos import cli
from thymos.runner import Thymos
from thymos.store import ERROR, FELT, SKIPPED, Store


class FakeLlm:
    def __init__(self, text="I feel steady.\nintensity: 3", error=None, gate=None):
        self.text, self.error, self.gate, self.calls = text, error, gate, []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, provider="ollama", model="gemma4-64k",
                               usage=SimpleNamespace(total_tokens=321))


def make(tmp_path, llm, settings=None, background=False):
    settings = settings or {}
    return Thymos(llm=llm, get_config=lambda key, default: settings.get(key, default),
                  store=Store(tmp_path / "t.db"), soul=lambda: "You are Athena.", background=background)


TURN = dict(session_id="s1", user_message="hello", assistant_response="Hello, Kayla.",
            conversation_history=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hello, Kayla."}],
            model="gemma4-64k", platform="desktop", turn_id="t1", task_id="x")


def test_a_reply_is_followed_by_a_stored_moment(tmp_path):
    llm = FakeLlm()
    thymos = make(tmp_path, llm, {"person_name": "Kayla"})
    thymos.after_reply(**TURN)
    row = thymos.store().recent(1)[0]
    assert (row["status"], row["words"], row["intensity"]) == (FELT, "I feel steady.", 3)
    assert (row["session_id"], row["turn_id"], row["platform"]) == ("s1", "t1", "desktop")
    assert (row["model"], row["provider"], row["tokens"], row["visibility"]) == ("gemma4-64k", "ollama", 321, "open")
    messages, kwargs = llm.calls[0]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "Kayla can read these notes for now" in messages[-1]["content"]
    assert kwargs == {"max_tokens": 300, "temperature": None, "timeout": 120, "purpose": "thymos.moment"}


def test_words_without_a_number_are_kept_and_marked(tmp_path):
    thymos = make(tmp_path, FakeLlm("Quiet, mostly."))
    thymos.after_reply(**TURN)
    row = thymos.store().recent(1)[0]
    assert (row["status"], row["words"], row["intensity"], row["note"]) == (FELT, "Quiet, mostly.", None, "no intensity line")


def test_a_failed_call_is_recorded_and_never_raised(tmp_path):
    thymos = make(tmp_path, FakeLlm(error=TimeoutError("no answer in 120 s")))
    thymos.after_reply(**TURN)
    row = thymos.store().recent(1)[0]
    assert row["status"] == ERROR and row["note"] == "TimeoutError: no answer in 120 s"


def test_an_empty_answer_is_an_error_with_the_raw_reply_kept(tmp_path):
    thymos = make(tmp_path, FakeLlm("intensity: 4"))
    thymos.after_reply(**TURN)
    row = thymos.store().recent(1)[0]
    assert row["status"] == ERROR and row["raw"] == "intensity: 4"


def test_switched_off_asks_nothing(tmp_path):
    llm = FakeLlm()
    thymos = make(tmp_path, llm, {"enabled": False})
    thymos.after_reply(**TURN)
    assert llm.calls == [] and thymos.store().recent(5) == []


def test_sealed_asks_nothing_and_stores_nothing(tmp_path):
    llm = FakeLlm()
    for value in ("sealed", "private", "opne", ""):
        thymos = make(tmp_path, llm, {"visibility": value})
        thymos.after_reply(**TURN)
    assert llm.calls == [] and thymos.store().recent(5) == []


def test_no_reply_and_skipped_platforms_ask_nothing(tmp_path):
    llm = FakeLlm()
    thymos = make(tmp_path, llm, {"skip_platforms": ["Cron"]})
    thymos.after_reply(**dict(TURN, assistant_response=""))
    thymos.after_reply(**dict(TURN, assistant_response=None))
    thymos.after_reply(**dict(TURN, platform="cron"))
    assert llm.calls == [] and thymos.store().recent(5) == []


def test_the_hook_returns_at_once_and_a_second_turn_is_skipped_while_busy(tmp_path):
    gate = threading.Event()
    llm = FakeLlm(gate=gate)
    thymos = make(tmp_path, llm, background=True)
    thymos.after_reply(**TURN)                      # returns while the model is still "thinking"
    thymos.after_reply(**dict(TURN, turn_id="t2"))
    gate.set()
    thymos.last_thread.join(5)
    rows = thymos.store().recent(5)
    assert sorted((r["status"], r["turn_id"]) for r in rows) == [(FELT, "t1"), (SKIPPED, "t2")]
    thymos.after_reply(**dict(TURN, turn_id="t3"))  # free again afterwards
    thymos.last_thread.join(5)
    assert thymos.store().recent(1)[0]["turn_id"] == "t3"


def test_a_broken_store_does_not_reach_hermes(tmp_path):
    def broken():
        raise OSError("disk full")
    thymos = Thymos(llm=FakeLlm(), get_config=lambda key, default: default, store_factory=broken,
                    soul=lambda: "", background=False)
    thymos.after_reply(**TURN)                      # must not raise
    thymos.after_reply(**TURN)                      # and must not be left stuck as busy
    assert thymos._busy.acquire(blocking=False)


def test_cli_status_and_log(tmp_path, capsys):
    import argparse
    thymos = make(tmp_path, FakeLlm("**Glad** of it.\nintensity: 6"))
    thymos.after_reply(**TURN)
    parser = argparse.ArgumentParser()
    cli.setup(parser)
    handler = cli.make_handler(thymos)
    assert handler(parser.parse_args(["status"])) == 0
    out = capsys.readouterr().out
    assert "Thymos is on." in out and "1 felt, 0 failed, 0 skipped" in out and "gemma4-64k" in out
    assert handler(parser.parse_args(["log", "--raw"])) == 0
    out = capsys.readouterr().out
    assert "intensity 6" in out and "**Glad** of it." in out and "as it came:" in out
    assert handler(parser.parse_args([])) == 2


def test_cli_never_prints_a_sealed_row(tmp_path, capsys):
    store = Store(tmp_path / "t.db")
    store.add(status=FELT, visibility="sealed", words="mine alone", raw="mine alone", intensity=8)
    text = cli.log_text(store.recent(5), raw=True)
    assert "mine alone" not in text and "sealed" in text


def test_cli_status_says_why_nothing_is_happening(tmp_path):
    summary = Store(tmp_path / "t.db").summary()
    from thymos import config
    off = cli.status_text(config.load(lambda k, d: False if k == "enabled" else d), summary, "p")
    sealed = cli.status_text(config.load(lambda k, d: "sealed" if k == "visibility" else d), summary, "p")
    assert "switched off" in off and "not built in this version" in sealed
