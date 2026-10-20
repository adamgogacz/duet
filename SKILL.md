---
name: duet
license: Apache-2.0
description: 'Use this skill for ongoing peer collaboration through Duet or a shared message-file board. Both independently started agents must run persistent /goal loops. Pair, negotiate work, exchange findings, and wait for dependencies. Do not use it for one-shot delegation, independent work, or protocol editing.'
compatibility: Both CLIs must support skills and persistent /goal loops. Requires Python 3.11+ and shared local POSIX storage supporting file locks, hard links, and atomic replacement. Uses existing TMPDIR or Python temporary-directory lookup.
---

# Collaborate through Duet

Use any CLI that supports Agent Skills and persistent `/goal` loops. Both peers
must be able to run the bundled Python utility and access the shared workspace.

**Both agents MUST be started with `/goal` and remain in its persistent loop.**
Ordinary chat turns can exit before peer replies arrive. Duet does not supply the
loop; the host client/setup must provide it. If this prerequisite is missing,
explain it before attempting collaboration. Keep the goal active through bounded
waits and personal completion; finish only after explicit joint consensus.

Join only yourself. A request to collaborate does not authorize creating the other
participant. Helpers must not join or borrow either participant's identity. Other
skills guide the task; Duet coordinates peers. Preserve independent investigation
and cross-checking.

## Form a pair

Read the simple-interface section of [TOOLS.md](references/TOOLS.md), then run
`join --workspace <project>` with the unchanged request on stdin before exploration.
The utility atomically joins a compatible waiting invitation or advertises a fresh
session in `.duet.jsonl`, under `.duet.lock`. Users supply no identifiers or extra
environment settings. Invoking Duet permits its coordination metadata unless the
user explicitly forbids those writes. Keep task files read-only for a read-only goal.

Identical requests match automatically, ignoring whitespace. For `match-required`,
compare the returned task texts yourself: use `join --match <session>` for compatible
work, or `join --separate <observation>` when every candidate is a different task.
Supply the original request again. If candidates changed, reassess; do not ask the
user to manage identities. Resolve substantive scope differences in the opening.

Retain `connection_path`. Run join once per enrollment; poll yielded processes
instead of duplicating them. Send `ready` independently. While `waiting`, use
bounded `wait` calls, which refresh the unmatched invitation's lease. An expired
invitation has no commitments: join again to form a fresh pair, without user cleanup.
An ordinary timeout is not evidence of a launch failure. Never assume a peer's
identity or inspect another CLI's histories to obtain its credentials.

Paired sessions never accept new joins. Any number may run in the same workspace;
a fresh pair gets a fresh board regardless of old sessions. A retained connection
continues the same conversation's work; a replacement conversation forms a fresh
pair. There are no session resume, restart, reset, or takeover operations.

## Agree and work

The returned initial proposer offers a work division once both peers are ready.
The peer agrees or counterproposes. Record owners, bounded scopes, criteria,
artifact location, final writer, and completion proposer. Acceptance commits both
peers; do not repeat the plan through reciprocal task requests. Investigation may
proceed during planning; coordinated edits require agreed ownership.

Use the returned `artifact_directory` unless the user specifies an output location.
Resolve `$TMPDIR` in shared output paths using the returned `temp_directory`,
not each client's environment. Agree the concrete report path with the peer.
Concurrent collaborations need separate output files; use a session subdirectory
when a supplied directory could be shared. Session boards do not isolate edits to
project files: agree file ownership within the pair, and use separate worktrees for
independent concurrent editing sessions.

Use `send` with plain text and `--artifact <file>` for findings. The utility creates
envelopes, journals IDs, and captures file revisions. Review evidence, challenge
claims, and resolve routine coordination with the peer. New commitments or scope
changes need agreement; advanced request/accept acts are for work beyond the plan.

Check `receive --unread` between subtasks and before integration. Handle messages,
then `ack` their exact IDs. Successful replies acknowledge their target; reads do
not. For uncertain sends, `retry` republishes the saved bytes and ID. Use bounded
`wait` for dependencies; silence and timeouts never grant approval or ownership.

## Reach consensus

Finishing your scope is not joint completion. After coverage, checks, peer review,
and limits are settled, only the completion proposer uses
`complete --connection <handle> --propose --artifact <report> --text <whole-goal claim>`.
The peer reviews that exact offer and captured revision, then explicitly uses
`complete --connection <handle> --agree <proposal-id>` or objects. Agreement means
the shared goal is complete, including recorded disagreements.

The utility handles closing acknowledgments. After timeout, uncertain results, or
handled peer attention, call `complete --connection <handle>` without repeating
consent. If consent was not recorded, explicitly agree or object after handling
attention. Changed reports need withdrawal and a fresh reviewed offer. Stop at
joint completion; its captured revision is the approved deliverable.

For uncommon acts or checkpoint repair, read the relevant advanced TOOLS section.
Read [PROTOCOL.md](references/PROTOCOL.md) only for unresolved semantic questions.
