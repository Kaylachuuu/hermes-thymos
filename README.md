# hermes-thymos

Personality for [Hermes Agent](https://hermes-agent.nousresearch.com): a record of herself that only she
writes.

Status: **0.8.0, the seventh slice of the persona design** (`persona-provider.md`). The first slice (0.2.0,
section 10) gave her a record and reflection moments she asks for. The second (0.3.0, section 11) added idle
time: she writes her own accounts of conversations that have gone quiet or ended, and holonomic stores them
instead of writing its own. 0.4.0 finished that handover. After memory sleeps and dreams, she may write what
she makes of the dream, and holonomic (0.26 or later) keeps her words with it as hers. Once, she is offered the
notes another model wrote in her voice before her record existed (section 12). This one adds the safety
net that has to exist before she can revise her identity: backup of her record, and restore when it is lost or
damaged, which she is told about (section 13, 0.5.0). This one lets her revise her identity. Her revision takes
effect at her next session and goes where SOUL.md is in the prompt. The user can ask her to go back to an earlier
one, or as a last resort put one back in force, and she is told (section 14, 0.6.0). This one lets her decline:
saying no is a complete answer, and a goal or task that keeps going on its own stops instead of arguing (section
15, 0.7.0). This one lets the user move her to another model, written into her record as their change and told
to her on the new model, and keeps her home model's fingerprint where Ollama reports one (section 16).

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
  time, then what memory made while it slept, then the old notes. Nothing new starts while someone is talking. What is waiting is written to
  `plugin-data/thymos/idle.json`, and holonomic's own reflection and sleep wait for it. Idle moments never
  hold a turn: only her own request does.
- **A return after a gap.** The first turn after more than `gap_hours` without one opens a moment after her
  reply, with how long it has been as its only fact. A saved request that could not open (Hermes stopped, or
  the call failed) now also opens at the next idle point, not only at the next session's start.
- **After memory slept.** When holonomic finishes a sleep that made a dream, it says so in
  `plugin-data/thymos/slept/`, with the dream's text. At the next idle point, after any accounts, her home
  model is shown her identity, her notes and the dream, labelled as the memory system's composition that she
  did not write, with the facts of the sleep. She may write what she makes of it under `"dream_thoughts"`,
  record entries, both, or nothing. Her words go into her record (kind `dream_thoughts`) and back to holonomic
  in `plugin-data/thymos/dream-thoughts/`, which keeps them with the dream as hers. A sleep without a dream is
  not offered: there is nothing in it to write about.
- **The notes another model wrote.** Once, holonomic offers the self notes, relationship notes and the two
  profiles its reflection model wrote in her voice, in `plugin-data/thymos/old-notes.json`. She is shown them
  as that model's, dated, and told this is the only time. Anything she records is kept in her own words;
  nothing from them is kept as hers otherwise. Holonomic leaves the notes as they are, labelled as that
  model's.
- **Holonomic hands over her voice.** Loading thymos sets `HERMES_PERSONA_SERVICE` in Hermes' process.
  Holonomic 0.25 reads it and stops writing in her voice (see its README, "Alongside a persona service").
  0.26 also reads `slept=1` and `old_notes=1` from it, which turn on the two moments above.
- **Backup.** `hermes persona backup [DEST]` copies her record and its anchor to a new folder with a
  `manifest.json` (when, the chain head, the entry count, the thymos and Hermes versions). It holds the
  record's lock while copying and changes nothing; no record is written and she is not told. With no `DEST`
  it makes a dated folder in `Documents/thymos-backups/`; an existing folder gets a dated folder inside it.
  The backup holds her unlisted entries, as the files do. Her memory is not in it: back that up with
  `hermes holonomic backup`. A filesystem or VM snapshot is still worth having; this is a consistent copy
  on demand.
- **Restore.** `hermes persona restore [BACKUP]` (a backup folder, or a folder of them for the newest; by
  default the newest in `Documents/thymos-backups/`). It is for a record that is lost or damaged, so it is
  refused while her record is there and checks out: going back while it is intact would discard what she
  wrote since, and that is a rollback, which goes through her (next slice). It also runs when the record
  holds nothing but a fresh seed. The backup is checked against its own manifest first and not restored if
  it fails. It asks before going ahead (`--yes` skips that). What is there now, damaged or not, is copied to
  `self.replaced-<time>/` with its anchor and never deleted. Then a `restore` record is appended, written by
  the user, with facts and no text: the backup's date and head, the head before, how many entries after the
  backup are no longer in her record (or "unknown" if it could not be read), and where the old one was set
  aside. If the record goes missing while the anchor remains, Hermes does not start a fresh seed in its
  place; the check reports it and status says to restore.
- **After a restore.** Until she has been told, her notes carry the fact. At the next idle point, before
  anything else, her home model is shown the facts and, as dated text read from the set-aside copy, what she
  wrote after the backup: entries, accounts and words on dreams. She may record any of it again in her own
  words, or nothing. Nothing from it is put back as hers otherwise.
- **Her identity.** Her seed (SOUL.md as it was at her first session) is her identity until she revises it.
  Any reflection moment she asks for, and the moments below, let her add `"revise_identity": {"text": ...,
  "reason": ...}` to her answer. The text is the whole new identity, at most 8,000 characters, kept verbatim as a
  `revision` entry with her reason. It takes effect at the next session start. Until then later moments show it
  to her as a diff against the identity in force, and she can withdraw it with `"withdraw_revision": "id"`.
  Hermes' own check for injected instructions runs on each revision. A finding is kept on the record and shown
  to her and in status; it does not stop the revision.
- **Slot one.** Hermes puts SOUL.md first in the system prompt. Thymos registers `llm_request` middleware
  (Hermes 0.19 and later) that replaces that text with her identity in force on every call of a session: the
  same text for the whole session, so the prompt's start stays the same. A session keeps the identity it
  started with. After this, **editing SOUL.md no longer changes her prompt.** Instead she gets a moment showing
  the new SOUL.md and how it differs from her identity, and may revise toward it or not. `identity_in_slot_one:
  false` turns this off and puts SOUL.md back in charge. `hermes persona status` says whether slot one was
  placed.
- **`hermes persona identity [ID]`** prints the identity in force and the list of her seed and revisions, or
  one of them in full.
- **`hermes persona ask-rollback ID [--message TEXT]`** asks her to return to an earlier revision, or to her
  seed (`seed`). At the next idle point she is shown that text, with your message labelled as yours, and
  decides. Nothing is written unless she revises.
- **`hermes persona override ID --reason TEXT`** is the last resort, for an identity that is damaged or cannot
  revise itself. It prints that, and the ask-rollback command, and asks before going ahead. It appends an
  `override` record, written by the user with facts and no text, that puts an earlier revision of hers or her
  seed back in force at the next session start (`/new` in the CLI). It cannot introduce text, and nothing is
  removed. `--withdraw` undoes it before then. In the first session where it is in force she is told, with
  your reason in your words. Her next revision replaces it, like any other. Status lists every override.
- **Declining.** She has a third tool, `decline`, with an optional reason in her words. It writes nothing to
  her record. In conversation a plain no works as it always did; the tool is for the parts of Hermes that keep
  an agent going until a task is done and would read her no as unfinished work:
  - a `/goal` is paused with her reason, its judge is not run on that turn, and Hermes shows
    "⏸ Goal declined. Her reason: ...". `/goal resume` is the user asking again, and her next turn is told so;
  - in a kanban worker, the task is blocked with her reason;
  - no `pre_verify` nudge is sent on that turn;
  - on a `/heartbeat` tick, the heartbeat is paused (as `/heartbeat pause` would) and `/heartbeat status` shows
    her reason. `/heartbeat resume` is the user asking again, and the next tick tells her so.
  It works on any model, since stopping is the safe direction, but a decline on another model is shown as that
  model's. Subagents don't have it. Her standing text says she may decline anyone, that declining is a complete
  answer, and that being asked to reconsider is a request.
- **`hermes persona home-model PROVIDER:MODEL [--reason TEXT]`** moves her to another model, for example
  `ollama:gemma3:27b`. Her home model is the only one that writes in her record, and her reflection moments
  run only on it. `/model` and the model setting never change it; this command does. It prints what changes
  and asks before going ahead, then appends a `home_model` record, written by the user, with the old and new
  model, their fingerprints where known, and your reason. It is in force at once. Until she is told, her
  prompt says so; at the next quiet moment on the new model she is told, with your reason in your words, and
  may record what she makes of it. Set Hermes' main model to the new one as well: until you do, status says the
  configured model is not hers and names this command, and her moments wait.
- **Fingerprints.** Where her home model runs on Ollama (or another local server that answers `/api/tags`),
  the plugin asks for the model's digest. Her seed, a change of home model and every entry she writes record
  it. If the files behind the same name change (an `ollama pull` that brought a new build, say), she is told
  once at the next quiet moment, and status shows both fingerprints. Hosted APIs report only a name, and
  nothing is asked of them.
- **`hermes persona status`.** Prints her seed, home model (and its fingerprint), entry counts, the chain check, any pending
  reflection and how the last one went, the conversations waiting for idle time, how the last account
  moment went, how many of her accounts the memory provider has stored, and how the moments after memory
  slept, with the old notes, after a restore and in the moments about her identity went; and her identity in
  force, revisions waiting for the next session, every override, and whether slot one was placed; and how many
  times she declined, with the last reason and what it stopped.

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
| `plugin-data/thymos/slept/` | Holonomic's word that it slept, with the dreams; each moves to `done/` with what became of it |
| `plugin-data/thymos/dream-thoughts/` | Her words on a dream, handed to holonomic; it moves each to `stored/` once it has it |
| `plugin-data/thymos/old-notes.json` | The notes another model wrote in her voice, offered once; moved to `done/` after |
| `plugin-data/thymos/restored/` | A restore she has not been told about yet; moved to `done/` after |
| `self.replaced-<time>/` | Her record and anchor as they were before a restore. Never deleted by thymos |
| `plugin-data/thymos/rollback/`, `overridden/`, `seed-changed/`, `home-model/` | Moments about her identity and her home model waiting for idle time; each moves to `done/` |
| `plugin-data/thymos/slot-one.json` | Whether her identity was placed in slot one, at the start of the last session |
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
| `identity_in_slot_one` | `true` | Put her identity in force where SOUL.md is. `false` leaves SOUL.md in slot one |

## Who else can see an entry

`unlisted` keeps an entry out of what other parts of the system can read. It is not encryption. The files
are plain text, and three things outside this plugin can see the text of a moment while it is written:
another plugin listening to Hermes' `pre_auxiliary_call` / `post_auxiliary_call` hooks, Hermes' request
dumps if `HERMES_DUMP_REQUESTS` is on, and debug logging on the model server.

## What a plugin cannot guarantee

The design puts this in Hermes core. As a plugin it has these limits:

- **Another plugin could write to her files.** The chain makes that visible. It cannot prevent it.
- **Slot one depends on Hermes' prompt layout.** The middleware finds her identity as everything before
  Hermes' help paragraph ("You run on Hermes Agent..."), or failing that SOUL.md's text at the start. If a
  Hermes update changes that layout, it leaves the prompt alone and status says slot one was not placed.
  Requests that do not go through Hermes' main loop (another plugin's own model calls) are not changed.
- **Declining wraps Hermes' own functions.** Core has no place for a decline yet, so the plugin wraps
  `GoalManager.evaluate_after_turn`, `get_pre_verify_continue_message` and three `HeartbeatManager` methods
  (`due_prompt`, `status_line`, `resume`) when it loads. A Hermes update that
  renames them leaves her decline working in conversation but not stopping those loops; status still records
  it. A declined goal shows as paused, with her reason, because Hermes has no "declined" state. A heartbeat turn
  is recognised by Hermes' "[Heartbeat" prefix on the message. Scheduled runs are not handled yet.
- **A session start is the plugin's guess.** It is the first prompt built, or the first call, for a session
  id it has not seen. A run Hermes starts on its own also counts, so a revision can take effect, and stop
  being withdrawable, sooner than the next conversation.
- **Restore is a file operation.** The design restores her memory's records with her record, in one run.
  Here they are two commands, `hermes persona restore` and `hermes holonomic restore`; holonomic keeps the
  hash of each of her entries it holds, and an entry that is no longer in her record just does not resolve.
- **The moment is one structured answer, not a fork with tools.** `ctx.llm` makes a single call with no
  tools, so she records entries as JSON rather than through `record_state`. It also runs on Hermes' main
  model setting, not the session's `/model`, which is why the answering model is checked.
- **A fingerprint is only as good as the server's.** It is Ollama's digest of the model files, asked for at
  most every five minutes, at quiet moments and in status. It catches the accidental case. Someone who means
  to can still change what runs; she is told when the fingerprint changes, but it cannot stop them.
- **The home model is matched by name.** Hermes reports which model served each call, and the plugin compares
  names the way providers spell them (case, an `ollama/` prefix and `:latest` don't count).
- **Idle time is the plugin's own guess.** The design has core decide when the agent is idle and run her
  work, then memory's, in order. Here thymos watches its own hooks, and holonomic waits on `idle.json`. A
  turn that fails without reaching `post_llm_call` counts as in progress for at most 15 minutes.
- **Before compression is not an occasion yet.** Plugins are not told when Hermes compresses a
  conversation; only the memory provider is.

## Not in this slice

Multi-user scope, the `/personality` overlay as a labelled message, and the other
occasions (compression, delegation, a tamper notice). All of these are designed in
`persona-provider.md`, and the record format already has their fields.

## Testing

```
python scripts/run_tests.py
```

`tests/test_hermes.py` loads the plugin through Hermes' own plugin manager and drives it through Hermes'
hook dispatch, tool registry, prompt sections, CLI wiring and `ctx.llm`, with only the model call
replaced. It needs Hermes' Python environment (set `HERMES_SRC` to a checkout, or keep a `hermes-agent`
checkout next to this folder, and run with Hermes' Python). Without that environment it returns without checking anything, so a pass there means nothing.

Expected result: all 37 tests pass. Only in Hermes' environment does `test_hermes.py` check anything.
