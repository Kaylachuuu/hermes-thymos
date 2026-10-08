from thymos.config import OPEN, SEALED
from thymos.store import ERROR, FELT, SKIPPED, Store


def test_roundtrip_oldest_first(tmp_path):
    store = Store(tmp_path / "t.db")
    store.add(status=FELT, visibility=OPEN, words="one", intensity=2, model="gemma", duration_ms=1000)
    store.add(status=FELT, visibility=OPEN, words="two", intensity=None, model="gemma", duration_ms=3000)
    rows = store.recent(10)
    assert [r["words"] for r in rows] == ["one", "two"]
    assert rows[0]["intensity"] == 2 and rows[1]["intensity"] is None
    assert all(r["readable"] for r in rows)


def test_a_row_not_written_as_open_never_gives_up_its_words(tmp_path):
    store = Store(tmp_path / "t.db")
    store.add(status=FELT, visibility=SEALED, words="mine", raw="mine\nintensity: 9", intensity=9)
    row = store.recent(1)[0]
    assert row["readable"] is False and row["words"] == "" and row["raw"] == ""
    assert row["intensity"] == 9          # the number is health data; the words are hers


def test_summary(tmp_path):
    store = Store(tmp_path / "t.db")
    assert store.summary()["felt"] == 0 and store.summary()["average_ms"] is None
    store.add(status=FELT, visibility=OPEN, words="a", intensity=2, model="gemma", duration_ms=1000)
    store.add(status=FELT, visibility=OPEN, words="b", intensity=None, model="other", duration_ms=3000)
    store.add(status=ERROR, visibility=OPEN, note="TimeoutError: slow")
    store.add(status=SKIPPED, visibility=OPEN, note="busy")
    s = store.summary()
    assert (s["felt"], s["errors"], s["skipped"]) == (2, 1, 1)
    assert s["average_ms"] == 2000 and s["average_intensity"] == 2 and s["without_number"] == 1
    assert s["models"] == ["other", "gemma"]


def test_filter_by_status_and_reopen(tmp_path):
    Store(tmp_path / "t.db").add(status=ERROR, visibility=OPEN, note="boom")
    again = Store(tmp_path / "t.db")
    again.add(status=FELT, visibility=OPEN, words="fine")
    assert [r["note"] for r in again.recent(5, status=ERROR)] == ["boom"]
    assert len(again.recent(5)) == 2
