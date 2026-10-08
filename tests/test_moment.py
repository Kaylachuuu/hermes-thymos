import pytest

from thymos import config, moment

CFG = config.load(lambda key, default: default)


def test_question_says_who_can_read_it_and_names_the_person():
    text = moment.question(dict(CFG, person_name="Kayla"))
    assert "Kayla can read these notes for now" in text
    assert "This message is not from Kayla" in text
    assert "intensity: N" in text


def test_question_without_a_name_still_reads_as_a_sentence():
    text = moment.question(CFG)
    assert "The person you were talking with can read these notes" in text


def test_no_question_exists_for_a_visibility_without_a_true_sentence():
    with pytest.raises(KeyError):
        moment.question(dict(CFG, visibility=config.SEALED))


def test_exchange_drops_tools_and_system_and_alternates():
    history = [
        {"role": "system", "content": "you are"},
        {"role": "user", "content": "find the file"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "found it"},
        {"role": "assistant", "content": "Looking."},
        {"role": "assistant", "content": "It is in Documents."},
    ]
    turns = moment.recent_exchange(history, "It is in Documents.", CFG)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["content"] == "Looking.\n\nIt is in Documents."


def test_exchange_adds_the_reply_when_history_lacks_it():
    turns = moment.recent_exchange([{"role": "user", "content": "hello"}], "Hello, Kayla.", CFG)
    assert turns == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hello, Kayla."}]


def test_exchange_keeps_the_last_messages_and_starts_with_the_user():
    history = []
    for i in range(10):
        history += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
    turns = moment.recent_exchange(history, "a9", dict(CFG, history_messages=3))
    assert [t["content"] for t in turns] == ["u9", "a9"]


def test_exchange_reads_parts_and_notes_pictures_without_sending_them():
    history = [{"role": "user", "content": [{"type": "text", "text": "who is this?"},
                                            {"type": "image_url", "image_url": {"url": "data:..."}}]}]
    turns = moment.recent_exchange(history, "That is Sushi.", CFG)
    assert turns[0]["content"] == "who is this?\n[an image]"


def test_long_messages_are_cut():
    turns = moment.recent_exchange([{"role": "user", "content": "x" * 5000}], "ok", dict(CFG, max_message_chars=100))
    assert len(turns[0]["content"]) == 100 and turns[0]["content"].endswith("[...]")


def test_messages_are_soul_then_exchange_then_question():
    messages = moment.build_messages([{"role": "user", "content": "hi"}], "Hi.", "You are Athena.", CFG)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[0]["content"] == "You are Athena."
    assert messages[-1]["content"].startswith("[Thymos: a private moment.")


def test_no_soul_means_no_system_message():
    messages = moment.build_messages([{"role": "user", "content": "hi"}], "Hi.", "", CFG)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_nothing_to_ask_about():
    assert moment.build_messages([], "", "soul", CFG) == []


@pytest.mark.parametrize("text, words, intensity", [
    ("I feel settled, and a little pleased.\nintensity: 4", "I feel settled, and a little pleased.", 4.0),
    ("Nothing much stirred.\n\nIntensity: 0", "Nothing much stirred.", 0.0),
    ("Warm.\n**intensity: 7/10**", "Warm.", 7.0),
    ("Warm.\nintensity = 12", "Warm.", 10.0),
    ("Warm.\n- Intensity: 3.5", "Warm.", 3.5),
    ("I liked that.", "I liked that.", None),
    ("<think>she asked about cats</think>\nGlad.\nintensity: 2", "Glad.", 2.0),
    ("intensity: 5", "", 5.0),
    ("The intensity: 5 of it surprised me.", "The intensity: 5 of it surprised me.", None),
])
def test_read_reply(text, words, intensity):
    assert moment.read_reply(text) == (words, intensity)


def test_read_reply_of_nothing():
    assert moment.read_reply(None) == ("", None)
