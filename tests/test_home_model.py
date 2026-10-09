"""Changing her home model (persona-provider.md 9.4, 9.5): only the user's command changes it, it is written into
her record as theirs, and she is told on the new model.  Where the model server reports a fingerprint, it is kept
with what she writes, and a change of the files behind the same name is told to her too."""
from thymos import models as mdl
from thymos.cli import do_home_model, parse_model, status

from test_service import FakeLlm, make, start, turn_end
from test_idle import idle

NEW = "gemma3:27b"


def test_the_user_moves_her_and_she_is_told_on_the_new_model(tmp_path):
    llm = FakeLlm('{"record_state": [{"entry": "I am on a larger model now. I want to see what changes.", '
                  '"unlisted": false}]}')
    svc = make(tmp_path, llm)
    start(svc)
    svc.chain.append("state", author="self", text="Written on the 12B.", model="ollama|gemma3:12b")
    assert do_home_model(svc, "ollama:gemma3:12b", yes=True) == "not changed: gemma3:12b is already her home model"
    asked = []
    out = do_home_model(svc, f"ollama:{NEW}", reason="More room to think.", ask=lambda q: asked.append(q) or "y")
    assert asked and out.startswith(f"her home model is now {NEW}; record") and "with your reason" in out
    rec = svc.chain.entries()[-1]
    assert rec["kind"] == "home_model" and rec["author"] == "user" and rec["text"] == ""
    f = rec["facts"]
    assert (f["old_provider"], f["old_model"], f["new_provider"], f["new_model"]) == ("ollama", "gemma3:12b", "ollama", NEW)
    assert f["user_reason"] == "More room to think." and svc.home_model() == ("ollama", NEW)
    # What the 12B wrote before the change is still hers; the record checks out.
    assert svc.chain.verify(soul_text=svc.soul()) == []
    assert "waiting to be told to her" in status(svc)

    # Until she is told, her prompt carries the fact.
    text = start(svc, session="s2", model=NEW)
    assert f"the user changed your home model on" in text and f"from gemma3:12b to {NEW}" in text

    # The quiet moment does not run on the old model: nothing is written, and it waits.
    svc._active.clear()
    out = idle(svc)
    assert "not her home model" in out["problem"] and len(svc.notes()) == 1
    # On the new model it runs, and she is told with the user's reason as theirs.
    llm.model = NEW
    out = idle(svc, after=7200)
    assert out["wrote"] == 1
    invite = llm.calls[-1][1]["content"]
    assert "Occasion: your home model changed" in invite and f"from gemma3:12b to {NEW}" in invite
    assert 'The user gave this reason, in their words: "More room to think."' in invite
    assert "stays marked as written on gemma3:12b" in invite
    assert svc.notes()[-1]["model"] == f"ollama|{NEW}"
    assert "your home model on" not in start(svc, session="s3", model=NEW)
    assert "after her home model changed: last moment" in status(svc)
    # An entry written by the old model after the change is reported, like tampering.
    svc.chain.append("state", author="self", text="Late.", model="ollama|gemma3:12b")
    assert [n["problem"] for n in svc.chain.verify(soul_text=svc.soul())] == ["foreign_model"]


def test_status_names_the_command_when_the_main_model_is_not_hers(tmp_path):
    import thymos.cli as cli
    svc = make(tmp_path)
    start(svc)
    saved = cli.configured_model
    cli.configured_model = lambda: ("ollama", NEW)
    try:
        assert f"To make it her home model: hermes persona home-model ollama:{NEW}" in status(svc)
        assert parse_model(NEW) == ("ollama", NEW)
        out = do_home_model(svc, NEW, yes=True)
        assert out.startswith(f"her home model is now {NEW}") and svc.home_model() == ("ollama", NEW)
        assert "To make it her home model" not in status(svc)
    finally:
        cli.configured_model = saved


def test_model_names_with_colons():
    assert parse_model("ollama:gemma3:27b") == ("ollama", "gemma3:27b")
    assert parse_model("gemma3:27b") == ("", "gemma3:27b")
    assert parse_model("openrouter:anthropic/claude") == ("openrouter", "anthropic/claude")
    assert mdl.short("sha256:0123456789abcdef") == "0123456789ab" and mdl.short("") == "none recorded"


def test_a_fingerprint_is_kept_and_new_files_under_the_same_name_are_told(tmp_path):
    llm = FakeLlm('{"record_state": []}')
    svc = make(tmp_path, llm)
    digest = {"now": "sha256:aaaa1111aaaa1111"}
    svc.fingerprinter = lambda provider, model: digest["now"] if model == "gemma3:12b" else ""
    start(svc)
    assert svc.chain.entries()[0]["facts"]["home_digest"] == "sha256:aaaa1111aaaa1111"
    svc.chain.append("state", author="self", text="One.", model="ollama|gemma3:12b")
    assert svc.chain.entries()[-1]["model_digest"] == "sha256:aaaa1111aaaa1111"
    assert "fingerprint aaaa1111aaaa in her record, aaaa1111aaaa now" in status(svc)
    svc._active.clear()
    assert idle(svc) is None                                   # nothing changed, nothing to tell

    digest["now"] = "sha256:bbbb2222bbbb2222"                  # `ollama pull` brought new files under the name
    svc._prints.clear()
    assert "different files under the same name" in status(svc)
    out = idle(svc)
    assert out["wrote"] == 0
    invite = llm.calls[-1][1]["content"]
    assert "Your home model's name, gemma3:12b, is the same, but the model files behind it are not" in invite
    assert '"fingerprint_before": "aaaa1111aaaa"' in invite and '"fingerprint_now": "bbbb2222bbbb"' in invite
    assert "Nobody ran the command" in invite
    # Told once for those files, even though she wrote nothing that records the new fingerprint.
    assert idle(svc, after=7200) is None
    # What she writes next records it.
    svc.chain.append("state", author="self", text="Two.", model="ollama|gemma3:12b")
    assert svc.recorded_digest() == "sha256:bbbb2222bbbb2222"


def test_no_fingerprint_is_asked_of_a_hosted_api():
    assert mdl._local("http://localhost:11434") and mdl._local("http://192.168.1.20:11434")
    assert not mdl._local("https://openrouter.ai/api") and not mdl._local("https://api.anthropic.com")
