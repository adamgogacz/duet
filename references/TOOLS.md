# Duet utility contract

## Simple interface

Both participating agents MUST be launched with `/goal` and kept in its persistent
loop. Utility commands coordinate the exchange; they do not keep an ordinary chat
agent running or install a goal loop. The host client/setup supplies that behavior.

Run `python3 <skill-directory>/scripts/duet.py`. The utility emits JSON on stdout
and JSON diagnostics on stderr. No extra environment variables or daemon are
required. Use `--help` on a command for its complete arguments.

### Pair

```bash
python3 <script> join --workspace <project> <<'TASK'
<unchanged user request>
TASK
```

A successful enrollment returns `status` (`waiting` or `paired`), `connection_path`,
`goal` (collaboration ID), `participant`, `peer`, `settings_path`, `board`,
`temp_directory`, `artifact_directory`, `initial_proposer`, `completion_proposer`,
and the advertiser's normalized `task`. Credentials are internal; retain the
connection path and do not construct or transfer identities.

Users need only supply the task, output location, and completion criteria. Do not
require session IDs, registry filenames, or path-resolution instructions in their
prompt. Resolve `$TMPDIR` in a shared output path against the returned
`temp_directory`. A directory supplied by the user may contain a session-specific
report; agree the concrete path with the peer and respect an explicitly named file.
Invoking Duet permits its required coordination metadata unless the user explicitly
forbids those writes; a read-only task keeps task content read-only.

The project registry is `.duet.jsonl`, serialized under `.duet.lock`. It records
`waiting` advertisements, optional `renewed` lease events, and `paired` claims.
Records are logically append-only; the utility stages and atomically replaces the
complete JSONL file under the lock to avoid partially written final records. Paired
sessions cannot accept another event. The board is unique per collaboration:
`<TMPDIR-or-Python-temp>/duet/<project-label>-<workspace-sha256>/<collaboration-id>`.
The joining peer uses the invitation's absolute path, never its own temp lookup.
Old `.duet.json` pointers do not participate in pairing.

Requests match automatically after whitespace normalization. If the remaining
invitations have different text, `join` returns `status: match-required`, their
tasks and session IDs in `candidates`, and an `observation` digest; it reserves
nothing. Assess those tasks using judgment, then run join again with the original
request and one of:

- `--match <candidate-session>`: the tasks are compatible. The claim is rechecked
  under the lock; an already claimed or expired candidate returns new candidates.
- `--separate <observation>`: every candidate is unrelated. If the candidate set
  changed, reassess the returned candidates. An exact task match takes precedence
  over creating another invitation.

This handles nearly identical instructions without embedding a model or guessing
semantic similarity. The joining peer's request is recorded in its pairing event;
both peers reconcile scope differences in the opening agreement.

`join --dry-run` previews without creating files, reserving seats, or renewing
leases. `status --workspace <project>` lists invitation history and current states
without reading reports, publishing messages, or inferring process liveness.

### Work

```bash
python3 <script> send --connection <handle> --kind ready --text 'Ready for this task.'
python3 <script> wait --connection <handle> --timeout-seconds 30
python3 <script> send --connection <handle> --kind propose --scope work-division --text '<bounded opening plan>'
python3 <script> send --connection <handle> --kind agree --reply-to <plan-id> --text '<explicit acceptance>'
python3 <script> send --connection <handle> --text '<finding and uncertainty>' --artifact <file>
python3 <script> receive --connection <handle> --unread
python3 <script> ack --connection <handle> --messages <handled-id> <handled-id>
```

The advertiser is peer-a, initial proposer, and completion proposer. Both peers
send ready independently. Planning needs both readiness messages; ordinary
investigation can proceed beforehand. An accepted bounded plan already commits
both peers, so no duplicate request/accept exchanges are needed for the same work.

A `waiting` invitation has a five-minute default lease. Ordinary `wait` refreshes
it once half its lease is used, including during one long bounded wait command.
Do not duplicate yielded processes. Keep waiting while unmatched; an empty inbox
or normal timeout is not a launch failure. `invitation-expired` (exit 3) means the
unmatched invitation is no longer usable: run join again with the request to form
a fresh pair. This never transfers an identity or reopens an old paired session.

No lease applies after pairing. New pairs run independently of all previous
paired sessions, including interrupted ones. Within the same conversation, retain
the connection across ordinary turn interruptions. Fresh conversations never
resume or take over old paired identities.

`receive` reads full causal history and the caller's checkpoint. `--unread` emits
only unprocessed peer messages; reading never acknowledges. Returned
`collaboration` describes this session's pairing and both requests. Checkpoints
are processing caches, not evidence of acceptance. Repair a corrupt checkpoint
using full replay before relying on unread-only output.

Use `--text -` for bounded UTF-8 stdin instead of shell interpolation. A successful
reply derives the subject and causal target and acknowledges that target only.
It does not acknowledge unrelated messages. New scopes still need agreement.

### Retry and artifact revisions

`send` generates IDs and valid envelopes and journals them before publication.
If the result is uncertain, use:

```bash
python3 <script> retry --connection <handle>
```

Retry publishes the original bytes and ID and completes an interrupted reply
acknowledgment. A prepared uncertain send blocks a new send until reconciled.
A failed preparation is not the previous successful send. Never blindly rerun a
send or launch a second copy of a yielded command.

`--artifact <file>` captures bounded regular-file bytes into this session's board,
computes the digest, and attaches a reference. Review those captured bytes. New
or retried approvals verify the capture and that its origin still matches; an
already published identical approval remains historical evidence. Changed files
require a new submission/offer and review. Captures are not OS-level file locks.

### Joint completion

Only the designated proposer:

```bash
python3 <script> complete --connection <handle> --propose --artifact <report> --text '<whole shared goal complete, with checks and limits>'
```

The peer reviews that offer's exact captured revisions, then explicitly:

```bash
python3 <script> complete --connection <handle> --agree <proposal-id>
```

An objection uses `send --kind object --reply-to <proposal-id> --text <reason>`.
The proposer resolves objections and publishes a fresh offer. For a changed file
before consent, withdraw the old offer and submit the corrected revision.

After a timeout, uncertain closing result, or handled attention, continue with:

```bash
python3 <script> complete --connection <handle>
```

This records no new consent. Reviewers must explicitly agree; two individual done
messages do not close the session. The utility records each participant's closing
acknowledgment and returns `complete` only after both observed the same agreement.
Normal agents do not call `finish` separately.

`peer-attention` includes exact unhandled peer messages. Handle and acknowledge
them before continuing. If `consensus_recorded` is false, reassess and explicitly
agree or object afterward. A reviewer without consent returns `review-required`.
The proposer can return `awaiting-peer-agreement`; either can return `closing`
while waiting for the peer. Repeat the plain complete call without repeating
proposal/agreement. Stop at `complete`; captured revisions are the approved result.

## Advanced settings

`join --options <json-file>` supplies typed operating overrides on the advertiser.
A joining peer normally omits it; explicit overrides must match committed settings.
Unknown fields, identity overrides, invalid limits, and non-finite numbers fail.
All timing and size defaults live in `scripts/settings.py`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `invitation_ttl_seconds` | 300 | Lease for an unmatched invitation; renewal uses half this interval. |
| `poll_interval_seconds` | 1 | Delay between process-level scans. |
| `wait_timeout_seconds` | 30 | Finite wait/closing budget; supply an explicit budget if configured null. |
| `max_message_bytes` | 65536 | Message and text limit. |
| `max_messages` | 1000 | Per-goal scan limit; no partial scan. |
| `max_checkpoint_bytes` | 131072 | Checkpoint/membership bound. |
| `max_artifact_bytes` | 16777216 | Per captured-file bound. |
| `max_registry_bytes` | 16777216 | Project invitation-registry bound. |
| `escalation` | peer-first guidance | User intervention is for user-only authority or missing prerequisites. |

Short-lock timing uses the central wait/poll defaults because a task configuration
may not yet exist. Leases compare UTC wall time; bounded commands use monotonic
time. Expiry bounds stale invitations and does not prove that a process stopped.
A peer may claim an invitation before a stopped advertiser's remaining lease ends.
No PID/vendor inference, background service, or paired-session timeout is used.

## Advanced messages

Use `send` for all acts. `--kind` selects the act; `--reply-to` derives the target,
subject, and causal prerequisites. Additional arguments:

- `--scope <scope> ...`, repeatable `--criteria <text>` and `--evidence <text>`.
- Repeatable `--artifact <file>` and `--cause <message-id>`.
- `--subject-type` and `--subject-id` for a new task, decision, or wait.
- `--body <json-file>` for uncommon semantic fields such as an await condition;
  this supplies a body, never an envelope.

A new request outside the opening needs objective, scope, inputs, and criteria.
Task replies use request/accept, submit/verify or reject, and release. An await
names a predicate and affected scope; only its sender releases it with a justified
`resume` act. This is a dependency operation, unrelated to session recovery.
Read PROTOCOL.md for these semantic contracts rather than inventing fields.

The low-level `setup`, `validate`, `publish`, `checkpoint`, and `finish` interfaces
remain for protocol tooling. `setup --workspace <project> --goal <id>
--participants <a> <b>` creates explicit settings without automatic pairing.
`--config <settings>` plus `--participant` and, for writes, `--session` supplies
explicit identity instead of a retained connection. Do not mix identity modes.
These advanced interfaces do not authorize borrowing a real participant's token.
`validate` checks structure only; publication also checks board prerequisites.

## Checkpoints and waits

A checkpoint object contains exactly `goal`, `participant`, `session`, `processed`,
`outgoing`, `deferred`, `invalid`, `active`, and `external_actions`. Processed and
outgoing IDs must exist in valid history; saving includes all own outgoing IDs.
`deferred` and `invalid` map IDs to reasons. Each active entry contains exactly
`subject`, `summary`, and `causes`. Each external action contains exactly `id`,
`status` (`planned`, `running`, `uncertain`, `resolved`), and `summary`.
Only the participant writes its checkpoint; replacement is atomic. Reconcile
uncertain external effects against the actual system before retrying them.

`wait --reply-to <own-message-id>` correlates substantive replies to ask, request,
propose, or submit. `wait --for-wait <own-await-id>` evaluates that declared
predicate and preserves its published deadline across invocations. These options
are mutually exclusive. A normal wait wakes for unread peer messages. Correlated
waits may wake for other peer attention; handle it before continuing the original
wait. Waiting never acknowledges, approves, or publishes a dependency release.

## Outcomes and filesystem boundaries

| Exit | Meaning |
| --- | --- |
| 0 | Success or an actionable nonterminal result; inspect `status`. |
| 1 | Invalid data, I/O failure, checkpoint repair needed, or conflicting replies. |
| 2 | Bounded wait/closing timeout; no implied approval. |
| 3 | Peer attention or an expired unmatched invitation. |
| 5 | Correlated decline, objection, or rejection. |

Publication uses closed staged files and atomic no-replace hard links. Identical
IDs/bytes are idempotent; conflicting reuse fails. Derived replies, causal links,
readiness, and the single designated completion offer are checked. Control replies
and closing changes are serialized by each board's enrollment lock. The utility
refuses symlink metadata, nonregular records, traversal IDs, and unbounded inputs.
File and directory fsync are attempted; arbitrary network filesystems and crash
recovery on unsupported storage are not guaranteed. Boards assume cooperating
local agents and do not grant task authority or prevent native file edits.
