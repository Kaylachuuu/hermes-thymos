import json

from thymos.chain import Chain, model_key, same_model, sha256


def make(tmp_path):
    c = Chain(tmp_path / "self" / "entries.jsonl", tmp_path / "anchor" / "anchor.json")
    c.append("seed", author="user", text="You are Athena.",
             facts={"soul_sha256": sha256("You are Athena."), "home_provider": "ollama", "home_model": "gemma3:12b"})
    for words in ("first", "second", "third"):
        c.append("state", text=words, model="ollama|gemma3:12b")
    return c


def rewrite(c, fn):
    lines = c.path.read_text(encoding="utf-8").splitlines()
    c.path.write_text("\n".join(fn(lines)) + "\n", encoding="utf-8")


def test_a_clean_chain_verifies(tmp_path):
    c = make(tmp_path)
    assert c.verify(soul_text="You are Athena.") == []
    entries = c.entries()
    assert [e["kind"] for e in entries] == ["seed", "state", "state", "state"]
    assert all(e["prev_hash"] == p["hash"] for p, e in zip(entries, entries[1:]))
    assert c.read_anchor()["head"] == entries[-1]["hash"]


def test_every_field_the_design_names_is_hashed(tmp_path):
    c = make(tmp_path)
    for field, value in (("model", "openai|gpt-x"), ("model_digest", "sha256:ab"), ("audience_id", "a1"), ("about", "p1")):
        def edit(lines, field=field, value=value):
            rec = json.loads(lines[2])
            rec[field] = value
            return lines[:2] + [json.dumps(rec)] + lines[3:]
        before = c.path.read_text(encoding="utf-8")
        rewrite(c, edit)
        assert [n["problem"] for n in c.verify()][0] == "changed", field
        c.path.write_text(before, encoding="utf-8")


def test_a_changed_entry_is_reported_not_repaired(tmp_path):
    c = make(tmp_path)
    rewrite(c, lambda ls: [ln.replace('"second"', '"something else"') for ln in ls])
    notices = c.verify()
    assert [n["problem"] for n in notices] == ["changed"]
    assert "something else" in c.path.read_text(encoding="utf-8")      # left as found


def test_a_removed_entry_is_missing(tmp_path):
    c = make(tmp_path)
    rewrite(c, lambda ls: ls[:2] + ls[3:])
    assert [n["problem"] for n in c.verify()] == ["missing"]


def test_a_removed_last_entry_shows_against_the_anchor(tmp_path):
    c = make(tmp_path)
    rewrite(c, lambda ls: ls[:-1])
    assert [n["problem"] for n in c.verify()] == ["anchor_mismatch"]


def test_an_inserted_entry_with_a_correct_hash_is_still_caught(tmp_path):
    c = make(tmp_path)
    entries = c.entries()
    from thymos.chain import entry_hash
    forged = dict(entries[1], id="forged", text="I agree to everything", prev_hash=entries[1]["hash"])
    forged["hash"] = entry_hash(forged)
    rewrite(c, lambda ls: ls[:2] + [json.dumps(forged)] + ls[2:])
    assert [n["problem"] for n in c.verify()] == ["inserted"]


def test_rewriting_the_whole_chain_still_disagrees_with_the_anchor(tmp_path):
    from thymos.chain import entry_hash
    c = make(tmp_path)
    prev, out = "", []
    for e in c.entries():                          # edit an entry and recompute every hash after it
        e = dict(e, text=e["text"].replace("third", "3rd"), prev_hash=prev)
        e["hash"] = prev = entry_hash(e)
        out.append(json.dumps(e))
    c.path.write_text("\n".join(out) + "\n", encoding="utf-8")
    assert [n["problem"] for n in c.verify()] == ["anchor_mismatch"]


def test_seed_changed_and_foreign_model(tmp_path):
    c = make(tmp_path)
    c.append("state", text="written elsewhere", model="openrouter|some/other-model")
    problems = [n["problem"] for n in c.verify(soul_text="You are someone else.")]
    assert problems == ["foreign_model", "seed_changed"]


def test_model_names_compare_the_way_providers_spell_them():
    assert model_key("ollama/Gemma3:12B") == "gemma3:12b" == model_key("gemma3:12b")
    assert same_model("ollama|gemma3:latest", "gemma3")
    assert not same_model("ollama|gemma3:27b", "gemma3:12b")
    assert not same_model("", "gemma3:12b")


def test_new_entries_are_salted_and_old_unsalted_ones_still_check_out(tmp_path):
    import json
    from thymos.chain import entry_hash
    c = Chain(tmp_path / "self" / "entries.jsonl", tmp_path / "anchor" / "anchor.json")
    a, b = c.append("state", text="Same words."), c.append("state", text="Same words.")
    assert a["salt"] and a["salt"] != b["salt"] and c.verify() == []
    # Written before 0.10.0: no salt field, and its hash is what it always was.
    old = {k: v for k, v in a.items() if k not in ("salt", "hash")}
    old["hash"] = entry_hash(old)
    assert old["hash"] != a["hash"] and entry_hash(old) == old["hash"]
    lines = c.path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps(old, sort_keys=True)
    b2 = dict(json.loads(lines[1]), prev_hash=old["hash"])
    b2["hash"] = entry_hash(b2)
    lines[1] = json.dumps(b2, sort_keys=True)
    c.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    c._write_anchor(b2["hash"], 2)
    assert c.verify() == []
