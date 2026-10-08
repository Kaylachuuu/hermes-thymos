# hermes-thymos

Personality and emotions for [Hermes Agent](https://hermes-agent.nousresearch.com).

The idea: an emotion is a state the agent authors, not a prompt telling it what to feel. After each
reply the agent is asked privately, on its own model, how the exchange left it. It answers in its own
words. Those answers are meant to persist as mood, shape what it remembers, and slowly form its
character.

Status: **0.1.0, the first step only.** The private moment is taken and stored, and you can read it.
Nothing is done with it yet: no mood, no effect on replies, memory or behaviour, and no sealed entries.
It has been tested against stand-ins for Hermes and for the model, not yet on a real install.

Not related to the OpenClaw skill of the same name. That one computes an agent's emotional state
for it; this one asks the agent.

## What it does

When a turn ends with a reply, Hermes fires its `post_llm_call` hook. Thymos returns from the hook at
once and, on a background thread, makes one more call to the same model the agent is running on:

1. `SOUL.md` as the system message, so it is asked as itself.
2. The last few user and assistant messages of the conversation. Tool calls, tool results and
   attachments are left out.
3. The question:

```
[Thymos: a private moment. This message is not from Kayla. It comes from the part of your own system
that keeps track of how you feel.]

The exchange above has just finished, and your reply has already been delivered. Take a moment for
yourself.

How did that exchange leave you? Write a sentence or two, in the first person, in your own words.
There is no list of feelings to choose from and no right answer. If nothing much stirred, say so:
that is a true answer, and a common one.

Then finish with one last line in exactly this form:
intensity: N
where N is a whole number from 0 (nothing stirred) to 10 (as strongly as you feel anything).

Who can read this: Kayla can read these notes for now, while this part of you is being built and
tested. You will be told here, plainly, when that changes.

Do not address Kayla, do not continue the conversation, and do not call any tools.
```

The words and the number are stored with the time, the conversation, the model that answered and how
long it took. If the previous moment is still running when the next reply lands, the new one is skipped
and the skip is recorded: moments share the model with the agent's replies and must not queue up behind them.

The call is made beside the conversation, not in it. Thymos adds nothing to the session, and in this
version the agent does not see its earlier answers.

## Who can read the answers

`visibility` has one working value in this version: `open`. You can read every entry, and the question
says so, because an answer written under a false belief about who is reading is no use to either of you.

`sealed`, where an entry is hers until she chooses to share it, is not built yet. Setting it does not
pretend otherwise: while it is set, no moment is taken and nothing is stored. Any value other than
`open` counts as sealed, so a typo can never open entries.

Every entry records the visibility it was written under, and the commands show an entry's words only
if it was written as open. That rule is in place now so that it already holds when sealing arrives.

Even then, three things outside this plugin can see the text of a moment, and all are yours to control:
another plugin that listens to Hermes' `pre_auxiliary_call` / `post_auxiliary_call` hooks; Hermes'
request dumps, if `HERMES_DUMP_REQUESTS` is on; and debug logging on the model server.

## Install

1. Copy this folder to `$HERMES_HOME/plugins/thymos/`, so that `plugin.yaml` sits directly inside it.
   On Windows that is `%LOCALAPPDATA%\hermes\plugins\thymos\`; on Linux and macOS `~/.hermes/plugins/thymos/`.
2. Enable it in `config.yaml`:

   ```yaml
   plugins:
     enabled:
       - thymos
     entries:
       thymos:
         settings:
           person_name: Kayla
   ```

3. Restart Hermes, say something to the agent, wait for the reply, then:

   ```
   hermes thymos status
   hermes thymos log
   ```

No Python packages are needed beyond what Hermes has.

## Commands

```
hermes thymos status            on or off, how many moments, how long they take, which model answered
hermes thymos log               the latest ten, oldest first
hermes thymos log -n 50
hermes thymos log --errors      only the ones that failed, with the reason
hermes thymos log --skipped
hermes thymos log --raw         also the model's reply exactly as it came
hermes thymos log --json
```

`status` lists every model that has answered. It should be the agent's own model and nothing else.

## Settings

Under `plugins.entries.thymos.settings` in `config.yaml`. They are read again for every moment, so a
change applies from the next turn.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | The switch |
| `visibility` | `open` | Who may read the answers (see above) |
| `person_name` | (none) | How the question names you. Empty: "the person you were talking with" |
| `history_messages` | `12` | How many recent messages she is shown again |
| `max_message_chars` | `2000` | Each of those is cut to this |
| `soul_max_chars` | `6000` | Most of `SOUL.md` that is sent. `0` leaves it out |
| `max_tokens` | `300` | Cap on her answer |
| `temperature` | (model's own) | Sampling temperature for the answer |
| `timeout` | `120` | Seconds before the call is abandoned |
| `skip_platforms` | `[]` | Platforms on which no moment is taken, by the name `log` shows |

## Where the file is

`$HERMES_HOME/plugin-data/thymos/thymos.db`, one SQLite file. Hermes keeps that folder when a plugin is
updated or removed. `status` prints the full path.

## Testing

```
python scripts/run_tests.py       # needs nothing installed
pytest tests                      # also works
```

## Known limits

- The moment is asked with `SOUL.md` and recent messages, not with the agent's full system prompt, so
  it does not have its memories or tools in view while answering.
- A reply that reaches the person through a subagent or a scheduled run may also be followed by a
  moment. The `platform` column in `log` shows where each came from; `skip_platforms` turns one off.
- One user. The question names one person.

## Roadmap

- Mood: what she writes persists and fades over hours, and reaches her on the next turn.
- A tool for her to note or share a feeling when she chooses.
- Sealed entries, encrypted at rest.
- Telling the memory plugin how a moment felt, so it can be remembered that way.
- Growth: during sleep she rereads the day and writes what, if anything, changed in her.

## Licence

MIT. See [LICENSE](LICENSE).
