---
name: duet
license: Apache-2.0
description: 'Use this skill for ongoing peer collaboration through Duet or a shared message-file board. Both independently started agents must run persistent /goal loops. Load this skill before task-file access; agree completion gates before reading task inputs or doing task work. Exchange evidence and reach explicit consensus. Do not use it for one-shot delegation, independent work, or protocol editing.'
compatibility: Both CLIs must support skills and persistent /goal loops. Requires Python 3.11+ and shared local POSIX storage supporting file locks, hard links, and atomic replacement. Uses existing TMPDIR or Python temporary-directory lookup.
---

# Collaborate through Duet

**Both agents must start with `/goal` and remain in its persistent loop.** Duet
coordinates peers; the host supplies the loop. Join only yourself. A collaboration
request does not authorize launching a peer. Authorized helpers must not join or
borrow identities. Other skills guide the task; Duet governs coordination.

## Pair and agree a gate

Read [TOOLS.md](references/TOOLS.md); use separate commands with literal script paths.
Join before exploration with the unchanged request on stdin; retain both connection forms.
Coordination metadata is permitted unless forbidden; read-only goals preserve task files.

Identical requests match automatically. For `match-required`, assess the returned
tasks: use `join --match <session>` for compatible work or `join --separate
<observation>` when all candidates are unrelated. Reassess changed candidates.
Run join once; poll yielded processes rather than duplicating them. Send `ready`
independently. Bounded `wait` refreshes a valid unmatched invitation. An expired
invitation may join afresh; paired sessions never accept new participants.

**Accept a completion gate before any task-input read or substantive work.**
Startup may locate instructions by filename; read/search only those instructions.
Referenced code, designs, evidence, and metadata wait, even to prepare a plan.
Pair, send ready, and negotiate. A blocked command permits no solo report or bypass.
Diagnose the specific operation; never bypass a denial or infer that all coordination
is unavailable. If blocked, report coordination status without task analysis/files.
The utility guards its own operations; it cannot police native tools.

The initial proposer sends one work-division proposal with `--criteria` for each
completion condition and `--stop-condition` for a bounded unsuccessful-stop or
reassessment rule. Include scope, owners, user constraints, collaboration method,
artifact location, final writer, and completion proposer in the plan. Preserve
explicit user criteria; otherwise derive meaningful bounded criteria from the
goal. Never replace the user's outcome with an easier one. Resolve material
scope/authority ambiguity with the user; settle routine choices with the peer.

Proposing commits the proposer; the peer explicitly agrees or counterproposes.
That exchange establishes the gate and responsibilities without redundant handshakes.
Changed gates need fresh bilateral assent; reconcile competing amendments explicitly.
Both judge adequacy: nonempty fields or “we both feel done” do not establish quality.

## Work autonomously within the gate

Choose independent passes/counterexamples for reviews and verification, immediate exchange
for brainstorming, and ownership/integration checks for editing. Keep counterexamples
within demonstrated scope; distinguish refutation from missing support. Check proposed
remedies against failure cases; label untested advice. Challenge claims without forced disagreement.
Use `send` with plain text and `--artifact <file>`; the utility captures exact revisions.
Corrected answers stay informational. Resolve control conflicts with explicit
`conflict-resolution` terms citing all replies; peer assent never disposes old tasks.

Use `status --connection <handle>` for the current gate, recognized obligations,
and next actions; it cannot enumerate requirements hidden in prose. Read `receive
--unread` between subtasks and before integration. Handle messages, then batch exact
IDs in `ack`. Link replies with `--reply-to`; successful replies acknowledge their
target automatically. Reading and acknowledgment never grant acceptance.
Use `retry` for uncertain sends; it republishes saved bytes/ID. Keep normal waits
bounded at the configured default and continue useful work when unblocked.

Use `artifact_directory` unless the user supplies a location. Resolve `$TMPDIR`
from Duet's returned shared `temp_directory`, not each client's environment.
Agree concrete paths, preserve named files, and separate outputs; use worktrees for independent edits.
Disclose premature work or other material deviations to the peer; later agreement
cannot erase them. Keep that history on the board and material limits in the report,
without process tables unless requested. Never claim unobserved checks or chronology.

## Decide and close

Resolve recorded tasks and waits before claiming success. Optional work may be
released, declined, or withdrawn legitimately; that does not satisfy a mandatory
user condition. An unavailable required check remains unmet. Supported uncertainty
may satisfy an assessment goal, but not a required proof or implementation.

Only the completion proposer uses `complete --propose` with a whole-goal claim,
final `--artifact` files, and one `--evidence` statement per criterion in order.
The peer reviews the exact captured revision and current gate, then explicitly
uses `complete --agree <offer>` or `complete --request-changes <offer> --text ...`.
Corrections wake the waiter; the proposer uses `complete --revise` to prepare a
fresh offer. Changed bytes, gate terms, or new task information require fresh
assent—even when the update has been acknowledged. No personal done flags,
receipts, or silence establish consensus.

Continue timeouts with plain `complete`. Both closing acknowledgments remain
provisional: peers can handle late information until the joint terminal cutoff.
Stop only at `complete` or an explicitly agreed `incomplete` outcome. When the
gate cannot be met within its bounds, propose `--outcome incomplete` with unmet
criteria; do not claim the user's goal succeeded. A known lost peer permits local
incomplete reporting and a user decision, not forged bilateral closure. Ordinary
silence/timeouts do not prove peer loss. New conversations form fresh pairs;
there is no identity takeover or session resume/reset operation.

Read advanced TOOLS sections for uncommon acts/repair; [PROTOCOL.md](references/PROTOCOL.md) for semantic questions.
