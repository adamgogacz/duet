# Contributing to Duet

Duet is a portable Agent Skill with a Python standard-library utility. It has no
runtime package dependencies, plugin registration, build step, or daemon.

## Work on the skill

Live collaboration tests require both agents to start with `/goal` and remain in
persistent goal loops. Utility subprocess tests exercise mechanics only; they do
not establish that an ordinary chat client can sustain collaboration.

Clone the repo and use Python 3.11 or newer on Linux or macOS. The repository root
is the skill bundle: keep SKILL.md, scripts, references, and LICENSE together.
For a local development install, follow the copy and symlink instructions in
[installation](docs/installation.md). Use a disposable workspace for collaboration
experiments; tests create their own isolated temporary directories.

Run the test suite from the repository root:

```bash
python3 -B -m unittest discover -s tests -v
```

If Ruff is already available, run:

```bash
ruff check .
ruff format --check .
```

The checked-in Ruff configuration handles standalone test imports. CI is configured to run the
suite on Linux with Python 3.11 and 3.14, and macOS with Python 3.14. New runtime
dependencies need a concrete requirement; keep standard-library operation intact.

## Change behavior deliberately

Explain the user-visible problem and the resulting behavior. Add a regression
case for a meaningful protocol or concurrency failure. Use independent processes
when testing filesystem coordination, and controlled clocks for deadline logic.
Never run tests against a user's real collaboration board.

Keep SKILL.md short. It should tell an agent what to do and when to load a bundled
reference. Put mechanical work in scripts and uncommon details in references.
Maintain the README quick start and utility/protocol contracts together.

Preserve these invariants:

- Join an eligible compatible invitation before advertising another, under the
  project registry lock. Paired sessions never reenter discovery.
- Keep each collaboration's messages, credentials, artifacts, and completion
  state separate. A fresh conversation never borrows an old paired identity.
- Retain immutable messages and exact retry identities. Receiving never grants
  approval or acknowledges unhandled messages.
- Require an accepted completion gate before substantive work, including for broad
  goals where peers derive the conditions. Bound unsuccessful work without calling
  it successful completion.
- Bind explicit whole-goal consent and provisional closing to the effective gate,
  current evidence, exact artifact revisions, and legitimate obligation dispositions.
  Individual completion, processing acknowledgments, and silence grant no consent.
- Keep task authority with the user and substantive judgment with the peers.

## Evaluate agent behavior

`evals/trigger-queries.json` contains activation cases. `evals/outcomes.json`
contains interaction scenarios and assertions. `evals/substantive-outcomes.json`
contains small tasks with independently checkable answers or constraint rubrics.
Give peers only the goal and input, not the expected outcome; score against the
reference evidence rather than another agreement vote. For instruction changes, compare
old/new behavior under matched conditions and inspect traces as well as reports.
Structural checks and utility tests are not evidence of live-agent quality.
The opt-in [live suite](evals/live/README.md) runs two actual CLI agents with native
`/goal` loops, private traces, bounded spend, and separate substantive grading.

For a bug report, include client/OS/Python versions, the command or relevant
prompt, and a minimal reproduction. Redact participant credentials, private task
text, customer data, and absolute paths you do not want public. Do not attach a
whole live board by default.

## License

This project uses the existing [Apache License 2.0](LICENSE). Contributions are
submitted under that license. Preserve third-party notices when adding material.
