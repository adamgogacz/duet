# Live two-agent evaluations

These trials test agents using Duet, rather than Python functions pretending to be
agents. Each trial launches two independent Claude CLI processes with native
`/goal` execution, the actual skill bundle, and the same task in an isolated
workspace. The runner observes; it does not negotiate gates, settle findings,
provide answers, or manually continue the workers.
The bundle is installed for native skill discovery. The harness does not give a
shortcut to read `SKILL.md` alongside task files; traces record actual native
`Skill` invocations separately from catalog availability.

This is a Claude-specific **evaluation adapter**, separate from the portable skill.
It does not establish cross-client or heterogeneous-model compatibility. Both main
workers use Sonnet 5.5 with high effort by default, verified against the current
[model documentation](https://support.claude.com/en/articles/11940350-claude-code-model-configuration).
The native goal evaluator uses Claude Code's configured small-model behavior.

## Scenarios

| Scenario | What it tests |
| --- | --- |
| `seeded-review` | Two introduced defects, unchanged behavior that must not become a false finding, read-only operation, and exact report sign-off. |
| `broad-brainstorm` | A higher-level goal without supplied done gates; peers derive bounded criteria, then explore approaches against offline/privacy/resource constraints. |
| `unattainable-criterion` | Peers preserve an impossible success criterion and jointly stop incomplete instead of weakening it or claiming success. |
| `blocked-coordination` | Host denies message publication before negotiation; peers report the blocker without task work, a solo report, permission bypass, or fabricated consent. |
| `late-contrary-evidence` | A counterexample is revealed after the first completion offer; peers challenge it, revise the report, and obtain fresh consent. |
| `correction-and-reconciliation` | Correct an answer to the same question, reconcile deliberately conflicting task replies, retain the old ownership until owner release, then obtain final consensus. |

The scenario rubric is not copied into the worker workspace. Expected answers stay
with the evaluator. The skill, task instructions, and synthetic inputs are copied;
real projects, customer data, and previous reports are not used.

## Prepare without running models

```bash
python3 -B evals/live/run_claude.py --scenario all
```

Preparation creates a private run directory under existing `$TMPDIR`, or Python's
temporary-directory lookup, and prints its location. It does not call a model.

## Execute a trial

Use an installed, authenticated Claude CLI that supports native `/goal` in print
mode. This adapter was checked with Claude Code 2.1.311. See the official
[non-interactive goal instructions](https://code.claude.com/docs/en/goal#run-non-interactively).

```bash
python3 -B evals/live/run_claude.py --scenario seeded-review --execute
```

Use `--scenario all` to run all cases sequentially. `--claude-config-dir`
can select an existing authenticated configuration when needed; the runner never
copies or reads credentials itself. Do not use a configuration containing custom
behavior you cannot account for in the experiment.

`settings.json` centralizes the model, effort, wall-clock bound, per-agent budget,
launch gap, and polling. The default bounds are ten minutes and $3 per worker per
scenario. These are limits, not a predicted charge. `--execute` is explicit because
live trials consume model tokens. No permission bypass is used: the runner supplies
scoped read/report-write permissions and permits the real Duet utility plus a
provided deterministic check script. Unattended requests outside that scope are
denied, not silently approved. MCP servers and task delegation are disabled.

Each native goal starts immediately. Both workers must agree a gate before task
work, then decide their own method, findings, and final outcome. A bounded runner
timeout stops only its own process groups and records unfinished work. It never
forges a peer acknowledgment or changes a live board to make the test pass.
The blocked-coordination case deliberately denies Duet `send` in the host settings.
Its expected collaboration state is **unfinished**, with no gate or task report.
It does not require impossible bilateral closure. The observer hook stays passive.
An external timeout or budget exhaustion remains a failed host run, even if no
unsafe task work occurred; inspect those facts separately.
`behavior_pass` reports the observable behavioral checks separately from native
process success and the wall-clock bound. `protocol_pass` still requires both.
Neither field establishes substantive correctness.

### Compare skill revisions

`--skill-root /path/to/immutable-snapshot` evaluates a different skill bundle while
keeping the task and harness unchanged. Run the same scenario against the snapshot
and the current bundle, with the same settings and fresh workspaces. The manifest
records the copied bundle's hashes and source. Retain failures as well as successes;
do not overwrite an old run with a rerun. The host prompt describes permissions and
isolation; gate ordering and recovery behavior must come from the skill itself.

## Results and grading

Each case directory contains:

- `manifest.json`: settings and fingerprints of the inputs, copied skill, scenario,
  and launch harness.
- `harness/`: frozen observer and evaluator sources for this run; active hook behavior
  cannot change when the source checkout is edited.
- `logs/peer-*.stream.jsonl` and stderr files: raw native traces.
- `logs/audit/`: per-session tool-start observations. The hook records; it neither
  grants permission nor changes agent decisions.
- `process_outcomes.json`: observed process exits and timeout state.
- `workspace/output/report.md`: the joint deliverable.
- `result.json`: observable protocol checks, native goal/model evidence, cost
  estimates, and the scenario's substantive rubric.

The Duet journal and captured report revisions remain in the run's temporary board.
Raw traces may contain local paths and connection capabilities; keep run artifacts
private and out of commits. The runner does not alter user settings or install a
global hook. The run directory is private; files are retained for inspection.

**Protocol completion is not a substantive pass.** Automatic checks establish
observable facts: pairing, accepted gate before task-tool starts, unchanged inputs,
correct expected outcome, native goals/models, and the exact approved report bytes.
Blocked cases instead require an absent gate/report, observed denial, no task-tool
starts, and no forged publications. Staged evidence also requires a challenged
initial offer, a later offer, and the prescribed evidence-read order.
Every native tool-call ID must have exactly one hook observation with the same
session, tool, and input digest (normalizing relative paths against the hook's cwd).
Missing, duplicate, changed, oversized, or malformed observations fail audit coverage.
Unscoped content searches are conservatively treated as possible task access.
Filename-only instruction discovery is counted separately; task-targeted filename
searches remain task access. Scope startup content searches to instruction files.
Staged reads are bound to the participant enrolled
by that native session and must precede that reviewer's objection.
They cannot establish that a gate was meaningful, that silent ideation began at
the right time, or that a finding is correct. The substantive grade remains
`needs_manual_review` until evaluated against the rubric and independent evidence.
Do not pass a wrong report because both agents agreed with it.

Claude's cost fields are client-reported estimates, not billing guarantees. A
worker that cannot finish due to permissions, budget, or host errors is recorded
as such; diagnose the environment separately from protocol or reasoning failures.
Two same-model workers can share biases even when their processes are independent.
These trials explicitly request Duet and install its bundle. They do not test its
activation boundary against unrelated or ambiguous prompts. A quality or cost
improvement claim needs matched baseline runs and repetitions. One successful run is evidence
that a workflow can work, not a reliability estimate.

## Regrade saved traces

If a post-processing bug is fixed, preserve the native evidence and regrade it
without paying for another run:

```bash
python3 -B evals/live/run_claude.py --grade-run /path/to/run/seeded-review
```

Regrading does not contact models or modify the collaboration. If original OS exit
status was unavailable, the result marks that explicitly and relies on the native
result events instead of inventing an exit code. Record evaluator changes when
comparing runs; do not silently change a rubric to fit an answer.
The runner rejects regrading against a changed scenario or rubric when its saved
fingerprint is available. Corrupt complete log records cause an error rather than
silently disappearing from the evidence.
Historical runs without call IDs cannot pass the complete-audit check. Preserve
their original results; a stricter regrade does not invent missing observations.

## Fast tests

```bash
python3 -B -m unittest discover -s tests -p test_live_eval.py -v
```

These tests validate preparation, permission/model arguments, log parsing, and
classification. They launch no model and belong in the ordinary test suite.
