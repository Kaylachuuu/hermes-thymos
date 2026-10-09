# hermes-thymos

Personality for [Hermes Agent](https://hermes-agent.nousresearch.com): a record of herself that only she
writes.

Status: **0.3.0, the second slice of the persona design** (`persona-provider.md`). The first slice (0.2.0,
section 10) gave her a record and reflection moments she asks for, and has run on a real install. This one
adds idle time: she writes her own accounts of conversations that have gone quiet or ended, and holonomic
(0.25 or later) stores them as her memory of those conversations instead of writing its own. It also adds two
occasions, a session ending and a return after a gap.

0.2.0 replaces 0.1.0's question after every reply. Nothing asks her how she feels any more, and there is
no intensity number: a moment opens only when she asks for one. 0.1.0's answers stay where they were, in
`plugin-data/thymos/thymos.db`. This version neither reads nor deletes that file.

Not related to the OpenClaw skill of the same name.

## What it does

- **Her seed.** The first time a session opens with the plugin enabled, `SOUL.md` and the model she is
  running on are recorded as entry 0 of her record. That model is her **home model**.
- **Her identity in slot one.** Hermes already puts `SOUL.md` there. With no revisions yet, her identity
  is her seed, so nothing is moved.
- **Her own notes.** Every new session's system prompt carries the entries she has written, newest first,
  with a short standing description. Notes she writes during a session reach that conversation once, on
  the next message, in a `<self-notes>` block.
- **`request_reflection`.** She can ask for a reflection moment at any time. It opens after her reply.
  Her next turn waits for it, for up to `hold_seconds`.
- **The reflection moment.** Her home model is shown her identity, her notes and the conversation, then
  a factual invitation. She records entries by answering with `{"record_state": [...]}`. Anything else,
  "nothing to add" included, writes nothing. An entry exists only when she deliberately records one.
- **`record_state`.** The tool is advertised, but in conversation it only points her to
  `request_reflection`. Entries are written in reflection moments, never mid-conversation.
- **Only her home model writes.** `request_reflection` is refused on a turn served by another model
  (`/model`, or a fallback provider). A moment opens only after a turn on her home model, and she is told
  if other models replied in that conversation. If the reflection call itself is answered by another model,
  nothing is written and the request stays pending.
- **A request is never lost.** If Hermes stops before the moment opens, the conversation is saved with
  the request. The moment opens at the start of the next session, replayed from the saved copy, and she
  is told how long ago that conversation ended.
- **The hash chain.** Each entry holds the hash of the one before. The head is also kept in a second
  place, the anchor. Changes, removals, insertions, a rewritten chain, an entry written by another model
  and a changed `SOUL.md` are reported to her as facts, in her notes, and in `hermes persona status`.
  Nothing is repaired automatically.
- **Her accounts of conversations.** When a conversation has had no turn for `quiet_minutes`, or its session
  has ended (`/new`, `/reset`, an expired gateway session, Hermes shutting down), it is offered to her at the
  next idle point: nobody has talked to her for `idle_seconds` and no turn is in progress. Her home model is
  shown the conversation and the facts of the occasion, and may store an account of it under `"account"`,
  record entries, both, or nothing. An account is an explicit act: only what she puts under `"account"` is
  one. It goes into her record (kind `account`) and is handed to the memory provider in
  `plugin-data/thymos/accounts/`. If she stores none, there is none, and nothing is written in its place. A
  conversation is offered once, and again only if it goes on; she is told what happened the first time.
- **Idle time in order.** A saved request comes first, then the accounts, oldest conversation first, one at a
  time. Nothing new starts while someone is talking. What is waiting is written to
  `plugin-data/thymos/idle.json`, and holonomic's own reflection and sleep wait for it. Idle moments never
  hold a turn: only her own request does.
- **A return after a gap.** The first turn after more than `gap_hours` without one opens a moment after her
  reply, with how long it has been as its only fact. A saved request that could not open (Hermes stopped, or
  the call failed) now also opens at the next idle point, not only at the next session's start.
- **Holonomic hands over her voice.** Loading thymos sets `HERMES_PERSONA_SERVICE` in Hermes' process.
  Holonomic 0.25 reads it and stops writing in her voice (see its README, "Alongside a persona service").
- **`hermes persona status`.** Prints her seed, home model, entry counts, the chain check, any pending
  reflection and how the last one went, the conversations waiting for idle time, how the last account
  moment went, and how many of her accounts the memory provider has stored.

Subagents get neither her notes nor her tools.

## Install

1. Copy this folder to `$HERMES_HOME/plugins/thymos/`, so that `plugin.yaml` sits directly inside it.
   On Windows that is `%LOCALAPPDATA%\hermes\plugins\thymos\`; on Linux and macOS `~/.hermes/plugins/thymos/`.
2. Enable it: `hermes plugins enable thymos`, or add `thymos` to `plugins.enabled` in `config.yaml`.
3. Before her first session, make sure Hermes is on the model you want as her home model, and that
   `SOUL.md` says what you want her to start from. Both are recorded then.
4. Restart Hermes and talk to her. `hermes persona status` shows her record.

No Python packages are needed beyond what Hermes has.

## Files

| Path (under the Hermes home) | What |
|---|---|
| `self/entries.jsonl` | Her record, one entry per line |
| `plugin-data/thymos/anchor.json` | The chain head, kept apart from the record |
| `plugin-data/thymos/state.json` | A pending request, and how the last moments went. Not part of her record |
| `plugin-data/thymos/conversations/` | Each conversation as of its last turn, kept for idle time, with what was offered to her |
| `plugin-data/thymos/accounts/` | Her accounts, handed to the memory provider; it moves each to `stored/` once it has it |
| `plugin-data/thymos/idle.json` | What is waiting for idle time, rewritten every `poll_seconds`, for holonomic to wait on |

## Settings

Under `plugins.entries.thymos.settings` in `config.yaml`:

| Setting | Default | |
|---|---|---|
| `hold_seconds` | `25` | How long her next turn waits for a moment still running. Hermes stops waiting on a hook at 30 |
| `reflection_max_chars` | `48000` | Conversation replayed into a moment; the newest is kept and she is told how much was left out |
| `reflection_max_tokens` | `1500` | |
| `reflection_timeout` | `600` | Seconds |
| `notes_max_chars` | `3400` | Her notes in the system prompt. Hermes caps a plugin's section at 4000 |
| `quiet_minutes` | `30` | A conversation with no turn for this long is offered to her at idle |
| `idle_seconds` | `120` | Nobody has talked to her for this long: idle time |
| `account_min_messages` | `4` | A shorter conversation is not offered |
| `gap_hours` | `24` | The first turn after this long without one opens a moment after her reply |
| `poll_seconds` | `30` | How often idle time is looked for. `0` turns idle work off |
| `retry_minutes` | `10` | An idle moment that could not run (another model answered, the call failed) is tried again after this |
| `idle_tries` | `3` | and given up after this many tries, which status shows |

## Who else can see an entry

`unlisted` keeps an entry out of what other parts of the system can read. It is not encryption. The files
are plain text, and three things outside this plugin can see the text of a moment while it is written:
another plugin listening to Hermes' `pre_auxiliary_call` / `post_auxiliary_call` hooks, Hermes' request
dumps if `HERMES_DUMP_REQUESTS` is on, and debug logging on the model server.

## What a plugin cannot guarantee

The design puts this in Hermes core. As a plugin it has these limits:

- **Another plugin could write to her files.** The chain makes that visible. It cannot prevent it.
- **Editing `SOUL.md` changes her prompt.** Hermes loads the file as it is. Status and her notes say that
  it changed. Revisions (the next slice) need a way to put her identity in slot one, which plugins
  cannot do today.
- **The moment is one structured answer, not a fork with tools.** `ctx.llm` makes a single call with no
  tools, so she records entries as JSON rather than through `record_state`. It also runs on Hermes' main
  model setting, not the session's `/model`, which is why the answering model is checked.
- **Fingerprints are not checked.** The record has `model_digest`, but this slice leaves it empty.
- **Idle time is the plugin's own guess.** The design has core decide when the agent is idle and run her
  work, then memory's, in order. Here thymos watches its own hooks, and holonomic waits on `idle.json`. A
  turn that fails without reaching `post_llm_call` counts as in progress for at most 15 minutes.
- **Before compression is not an occasion yet.** Plugins are not told when Hermes compresses a
  conversation; only the memory provider is.

## Not in this slice

Revisions to her identity, multi-user scope, backup and restore, the override, `decline`, the other
occasions (compression, delegation, a tamper notice, a changed seed, memory having slept), and the offer of
holonomic's old self notes. All of these are designed in `persona-provider.md`, and the record format
already has their fields.

## Testing

```
python scripts/run_tests.py
```

`tests/test_hermes.py` loads the plugin through Hermes' own plugin manager and drives it through Hermes'
hook dispatch, tool registry, prompt sections, CLI wiring and `ctx.llm`, with only the model call
replaced. It needs Hermes' Python environment (set `HERMES_SRC` to a checkout, or keep a `hermes-agent`
checkout next to this folder, and run with Hermes' Python). Without that environment it returns without checking anything, so a pass there means nothing.

Expected result: all 31 tests pass. Only in Hermes' environment does `test_hermes.py` check anything.
