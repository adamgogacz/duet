---
name: duet
license: Apache-2.0
description: 'Use this skill for ongoing peer collaboration through Duet or a shared message-file board. Both independently started agents must run persistent /goal loops. Honor agreement-first instructions; otherwise use bounded read-only discovery when completion criteria are unclear. Encourage scoped subagents; only the two peers grant consensus. Do not use for one-shot delegation, independent work, or protocol editing.'
compatibility: Both CLIs must support skills and persistent /goal loops. Requires Python 3.11+ and shared local POSIX storage supporting file locks, hard links, and atomic replacement. Uses existing TMPDIR or Python temporary-directory lookup.
---

# Collaborate through Duet

**Both agents start with `/goal` and stay in its persistent loop.** The host supplies
that loop. Join only yourself; collaboration does not authorize launching the peer.
Other skills guide task execution; Duet governs coordination and consensus.

## Pair

Read [TOOLS.md](references/TOOLS.md); use separate `python3 -B` commands with literal paths.
Join before content inspection with unchanged task text on stdin; retain both connection forms.
Coordination metadata is permitted unless forbidden; respect every user constraint.

Identical requests match automatically. For `match-required`, assess candidates; use
`join --match <session>` or `join --separate <observation>`; reassess changed candidates.
Join once; poll yielded processes instead of duplicating them. Send `ready` independently.
Bounded waits renew a valid unmatched invitation; expired invitations join afresh.
Paired sessions accept no newcomers. Silence or an empty inbox does not prove peer loss.

A denied operation permits no bypass or solo fallback report. Diagnose that operation;
if blocked, report coordination status without pretending joint work or consent occurred.

## Read-only scope

Unless stricter constraints are explicit, read-only work protects project content,
Git refs/staging and existing project files, including ignored files. It permits
reports, scratch files, CLI state and disposable caches. Prefer temporary storage
outside the checkout; necessary new tooling files inside it are also allowed if
they do not overwrite existing files or alter project content. A check that only
creates new disposable runtime files is eligible to run after gate agreement.
Honor host restrictions, but do not add no-new-files or no-checkout-writes gates
on the user's behalf. Ordinary temporary activity alone is not a breach; check
protected state rather than requiring proof of zero filesystem writes.
Creating and removing your own disposable scratch is normal task work. Remove
only files/directories you created for this session; preserve existing or unfamiliar
files, other sessions' work, final deliverables, evidence and the Duet board.
Explicit retention, approval and host restrictions still apply.

## Define done, then work

User instructions override discovery defaults. “Agree before work” includes task metadata
and content: agree an initial gate from the request and governing instructions first.
It may authorize bounded inspection, then bilateral refinement before main investigation.

Otherwise, agree clear user criteria before inspection. If unclear, each peer inspects
after ready, within a stated finite read-only scope solely to propose meaningful done
conditions. Helpers share that boundary. Discovery permits no project execution,
edits, artifacts or full investigation. Share provisional observations inline and
stop at the boundary.

Separate user requirements from peer choices. New process rules apply prospectively;
do not invent historical obligations or erase breaches. Ask one focused question if
peers cannot resolve ambiguity that changes permission or success.
While awaiting clarification, continue only work permitted under either interpretation.

The initial proposer sends one work-division proposal with `--criteria` for each
completion condition and `--stop-condition` bounding unsuccessful work or reassessment.
Include scope, owners, constraints/capabilities, method, output, final writer and completion
proposer. Each peer compares its own assessment; agree or counterpropose.
Proposing commits you; peer assent establishes the gate before main work. Criteria
must describe a meaningful outcome, not merely defer deciding what done means.
Reconcile requirements from both peers' governing instructions and host goals.
Required publication, approval or cleanup cannot be omitted merely because it is
pending. A draft satisfies a draft-only request, not a mandatory posting requirement.

Changes need fresh bilateral assent. Receive replies and inspect `status --connection`
`gate_review` before replacing terms. The initial proposer consolidates; either peer may
challenge. Cite the accepted gate, explain changes, and preserve user requirements.
Withdraw unsettled duplicates; reconcile competing acceptances explicitly.

## Work and delegate

Encourage scoped subagents for reconnaissance, task slices or independent checks when useful.
Give each its objective, allowed actions, constraints, inputs and bound.
Delegate a task, not the shared `/goal`; request compact locations, evidence, checks and uncertainty.
Helpers never join Duet, receive connection credentials, replace a peer, or grant approval.
The owning peer validates their work; a helper opinion is not verification.

Use independent passes for reviews, immediate exchange for brainstorming, and agreed
ownership for editing. Keep counterexamples within their scope; distinguish refutation
from missing support. Check suggested remedies and label untested advice.

Before running project commands, inspect effective configuration and likely side effects.
Use permitted scratch/isolation for generated outputs; otherwise skip unsafe execution
and keep any mandatory check unmet. Read TOOLS' execution guidance when commands are needed.

Use `send` with plain text and `--artifact <file>`; the utility captures exact revisions.
Corrected answers stay informational. Control conflicts need explicit `conflict-resolution`
terms citing every reply and peer assent; old commitments still need valid dispositions.

Receive `--unread` between subtasks and before integration. Handle messages, then batch
exact IDs in `ack`. Linked replies acknowledge their target; reading never grants consent.
Use `retry` for uncertain sends; it republishes saved bytes/ID. Keep waits bounded and
continue useful authorized work. Status shows recognized obligations, not truth hidden in prose.

Honor host and user output limits; otherwise use `artifact_directory`. Resolve
`$TMPDIR` from Duet's shared `temp_directory`, not each client's environment.
Agree paths and separate outputs; use worktrees for independent editing.
Keep process history on the board, not in report tables.

Disclose material incidents, breached constraints and unverified prior state. Recovery
cannot establish past compliance or erase unknown damage; preserve that distinction.
Record consequential uncertain effects and seek user-only authority when needed.

## Decide and close

Resolve recognized tasks and waits. Legitimate release/cancellation does not satisfy a
mandatory user condition; an unavailable required check stays unmet. Assessment goals
may conclude with uncertainty, while required proofs or implementations remain unfulfilled.

Only the completion proposer uses `complete --propose` with a whole-goal claim, exact
final artifacts, and one `--evidence` statement per criterion. Distinguish deliverable
completion, constraint compliance and unresolved uncertainty in that evidence.
First finish permitted cleanup and compare the shared claim with your own host goal.
Both peers must explicitly confirm: all mandatory work is satisfied, no required
approval/action remains pending, and I am ready to exit successfully. The proposer
states this in its claim; the peer confirms through `complete --agree`. These are
the existing bilateral exit confirmations, not an extra handshake or personal done flags.
Report approval alone is insufficient. If a required post awaits user approval,
present the concrete draft once and keep success pending; at the agreed stop bound,
jointly close incomplete instead. Never publish without authority or silently waive it.

The peer reviews captured bytes and uses `complete --agree <offer>` or
`complete --request-changes <offer> --text ...`. The proposer uses `complete --revise`.
Changed bytes, gate terms or new task information require fresh assent, even after acknowledgment.

Continue timeouts with plain `complete`. Both closing acknowledgments stay provisional
until the joint terminal cutoff. Personal done flags, receipts and silence are not consensus.

If bounds or mandatory conditions cannot be met, propose `--outcome incomplete`.
At either terminal outcome stop collaboration work; report unmet conditions and needed user
decisions once. Follow host goal-status rules; do not rerun finished work or claim false success.

A known lost peer permits local reporting, never forged closure. New conversations form
fresh pairs; no identity takeover or session resume/reset exists.

Read advanced TOOLS sections for unusual acts/repair; [PROTOCOL.md](references/PROTOCOL.md) for semantics.
