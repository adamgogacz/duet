# Duet collaboration protocol

Duet coordinates two independently started autonomous peers. It is independent of
agent vendor and task domain. Both independently started agents MUST operate
under persistent `/goal` loops supplied by their host clients. Ordinary chat turns
are unsupported because they may exit before the exchange finishes. Duet does not
implement that loop, launch a peer, or grant tool authority. User instructions define the goal, resources,
constraints, and external authorization. MUST, SHOULD, and MAY express required,
recommended, and optional behavior.

## 1. Pairing and startup

Users provide task content, an optional output location, and completion criteria.
Participants handle generated IDs, registry files, and shared-path resolution.
Invoking Duet permits its coordination metadata unless the user explicitly forbids
those writes; a read-only task keeps task content read-only. For shared output paths,
`$TMPDIR` refers to the advertiser's returned temporary root, not separate client
environments. Participants agree the concrete report location, respecting any
explicitly named file and separating concurrent outputs within supplied directories.

Both task prompts MUST start with `/goal`. The goal loop keeps each participant
iterating across turns, peer replies, and bounded waits until joint consensus.
Finishing a personal scope or reaching a wait timeout MUST NOT end that loop.
If a client/setup has no persistent `/goal` mechanism, the prerequisite is absent.

A collaboration session has exactly two participants, peer-a and peer-b. A project
MAY contain any number of concurrent paired sessions. Each has a unique ID, board,
credentials, messages, commitments, and completion agreement. No session has a
permanent substantive leader. Peer-a is the initial and completion proposer only
to prevent simultaneous competing control operations.

Before task exploration, an arriving agent MUST inspect the project invitation
registry and join an eligible compatible invitation before advertising a new one.
The availability check and claim/advertisement MUST be serialized under a short
project lock. Merely locking individual writes is insufficient. Identical requests
match after whitespace normalization; differing task texts require the joining
agent's compatibility assessment. A stale selection MUST be rechecked before
commit. A decision to advertise a different task MUST account for every currently
eligible invitation, including any added during semantic assessment.

The registry is `.duet.jsonl`. A `waiting` event records session ID, task, absolute
board path, and lease deadline. A `paired` event records the same ID and the joining
peer's task. A `renewed` event may advance an unmatched invitation's deadline.
The registry is logically append-only and maintained under `.duet.lock`; atomic
whole-file replacement MAY be used to prevent torn JSONL records. Paired sessions
MUST NOT reenter discovery or accept a third participant. Old paired sessions do
not block fresh work. Different tasks MAY each have an unmatched invitation.

Only unmatched invitations have a lease. The waiting participant refreshes it
through its bounded wait loop. Expired invitations MUST NOT be claimed or revived.
A participant whose unmatched invitation expires MAY enroll afresh; it has no
joint commitments to transfer. Lease expiry limits stale discovery but does not
prove process death: an advertiser that just stopped may still be claimed before
its deadline. Paired sessions have no liveness inference or automatic expiry.

The advertiser chooses an existing TMPDIR or Python's temporary-directory lookup.
Its board is `<temporary-root>/duet/<project-label>-<canonical-project-path-sha256>/<session-id>`.
The joining peer MUST use the advertised absolute board despite differences in
its own temporary environment. The registry contains task text and local paths;
metadata and credentials MUST remain outside commits. Existing `.duet.json`
pointers are unrelated to new discovery and MUST NOT be modified by it.

Each participant has one publishing conversation and retains its connection.
Normal turn interruptions within that conversation keep the connection. A new
conversation forms a fresh pair; it MUST NOT guess, borrow, recover from another
client's logs, or transfer an old paired identity. There is no session resume,
restart, reset, or takeover operation. Multiple collaborations needing the same
output location MUST choose distinct files/session subdirectories. Independent
concurrent editing collaborations SHOULD use separate worktrees; boards do not
isolate project-file edits.

Both participants MUST independently publish ready with the same context revision.
Enrollment and readiness are different facts; an empty inbox or normal wait
timeout does not establish a failed peer launch. Joint work acts require both
readiness messages. Investigation MAY proceed while the opening is negotiated.
Peer-a proposes the initial-plan decision for that context revision, with exactly
the work-division scope and both readiness messages in its causal ancestry. The
peer agrees or counterproposes. Counterplans cite the opening and retain its scope.

The opening MUST record bounded owners/scopes, criteria, artifact location, final
writer, and completion proposer. Its acceptance establishes those commitments;
participants MUST NOT repeat the same responsibilities through reciprocal task
requests. New or changed commitments require new agreement. Only user-reserved
choices or missing external prerequisites warrant user intervention; ordinary
partitioning, feedback, and waiting are peer responsibilities.

## 2. Language and evidence

Natural language carries reasoning, findings, uncertainty, and task content.
Structured fields carry identities, causal relationships, and explicit acts.
An inform or casual expression of approval MUST NOT implicitly accept a task,
verify a submission, release a dependency, or establish whole-goal completion.

Goals define shared outcomes; tasks define bounded commitments; decisions record
shared choices; questions seek information; waits name dependencies; incidents
make conflicts or failures visible. An accepted plan and an accepted task request
are both commitments. Progress and findings are normally asynchronous.

Artifact references contain exactly `id`, `revision`, and `description`, all
nonempty strings. Revisions MUST identify preserved bytes, such as a content
digest; “latest” is insufficient. Utility-generated references capture bounded
regular files, compute digests, and identify immutable copies. Approval applies
to its exact target revision. A changed working origin before approval requires
fresh submission and review. After consensus, the captured revision is the approved
result. Agreement and matching digests do not establish substantive correctness;
independent investigation and evidence-based challenge remain required.

## 3. Publication and causal history

Within a session board, each participant writes immutable messages to
`messages/<goal>/<participant>/<message-id>.json`. Each writes only its own
checkpoint at `checkpoints/<goal>/<participant>.json`. IDs use nonempty ASCII
letters, digits, hyphens, or underscores; message IDs are unique within a goal.

Publication MUST make complete bytes visible atomically without replacing an
existing message. Identical ID/byte retries are idempotent. Different bytes under
an existing ID are an error. File creation, directory enumeration, wall-clock
timestamps, and filename sorting do not establish message precedence. Causal
references do. Missing prerequisites leave a message pending; invalid prerequisites
or cycles invalidate its dependent transitions. Neither may authorize work.

Helpers SHOULD journal generated IDs/envelopes before publication and retain them
until delivery uncertainty is reconciled. Retrying MUST reuse exact bytes/ID.
Receiving does not acknowledge or consume. Acknowledgment records only messages
actually handled. A substantive reply MAY automatically acknowledge its target after publication.
Interrupted acknowledgment MUST be retried idempotently; unrelated arrivals remain
unread.

## 4. Message envelope

Every message MUST contain exactly these envelope fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Unique message ID, matching its filename. |
| `goal` | string | Goal ID, matching its directory. |
| `from` | string | Sender participant ID, matching its directory. |
| `session` | string | Sender's generated execution credential; distinct from the collaboration ID. |
| `to` | string | The other participant ID. All messages are visible to both. |
| `subject` | object | Exactly `type` and `id`; type is `goal`, `task`, `question`, `decision`, `wait`, or `incident`. |
| `kind` | string | A message act defined in section 5. |
| `reply_to` | string or null | Message being answered or acted upon. |
| `causes` | array of strings | IDs whose content or effects this message relies on. |
| `body` | object | Content required by the message act. |

`causes` MUST contain `reply_to` when it is non-null and all other messages needed
to justify the transition. It MUST NOT contain the message's own ID. Causal
cycles are invalid. All references are within the same goal.

Messages may arrive out of order. A message with a missing prerequisite is
pending and MUST NOT change state until that prerequisite is available and
valid. An invalid prerequisite blocks dependent transitions and raises an
incident. Directory order, filename order, filesystem modification times, and
wall-clock timestamps MUST NOT decide precedence.

Envelope and body fields MUST conform to this specification. Unknown acts and
fields have no state-changing effect and MUST be reported. A participant MUST NOT
invent an extension during an exchange.

## 5. Message acts

The following table specifies required body fields. Every body MAY additionally
contain `text`, a string explaining context, and `artifacts`, an array of the
artifact references defined above. No other body fields are allowed.
`scope` is a nonempty array of logical resource or action IDs using the ID character rules; `criteria` and
`evidence` are nonempty arrays of explanatory strings; `inputs` and `artifacts`
are arrays of artifact references. Empty artifact arrays are allowed for results
whose complete content and evidence appear inline.

| Kind | Subject | Required body | Meaning |
| --- | --- | --- | --- |
| `ready` | Goal | `context_revision` string, `summary` string | Announce the current participant and shared context. |
| `inform` | Any | `text` | Share context or progress; no commitment or agreement. |
| `receipt` | Same as target | None | Acknowledge processing the message in `reply_to`; not acceptance. |
| `ask` | Question | `question` string | Open an information request. |
| `answer` | Same question | `answer` string, `evidence` | Answer the `ask` in `reply_to`. |
| `request` | Task | `objective` string, `scope`, `inputs`, `criteria` | Ask the recipient to own the task; sender undertakes to review it. |
| `accept` | Same task | None | Recipient accepts the exact `request` in `reply_to`. |
| `decline` | Same task | `reason` string | Recipient declines the `request` in `reply_to`. |
| `propose` | Decision | `proposal` string, `scope` | Offer exact terms for a shared decision. |
| `agree` | Same decision | None | Peer agrees to the exact `propose` in `reply_to`. |
| `object` | Same decision | `reason` string | Peer contests the `propose` in `reply_to`; no agreement occurs. |
| `submit` | Same task | `summary` string, `artifacts`, `evidence` | Owner presents a result for review; reply to its `accept` or latest `reject`. |
| `verify` | Same task | `evidence` | Requester accepts the exact `submit` in `reply_to` against its criteria. |
| `reject` | Same task | `reason` string, `criteria` | Requester identifies unmet criteria in the `submit` in `reply_to`. |
| `await` | Wait | `blocks`, `condition`, `deadline`, `reason` string | Sender declares a dependency on specified work. |
| `resume` | Same wait | `evidence` | Sender releases its own `await` in `reply_to` after proving its condition. |
| `release` | Same task | `reason` string | Owner ends its commitment after stopping affected actions; reply to `accept`. |
| `withdraw` | Same as target | `reason` string | Author withdraws its own pending `ask`, `request`, `propose`, or active `await`. |
| `alert` | Incident | `reason` string, `scope` | Make an actual conflict, blocked dependency, or failure explicit. |

An initiating act has `reply_to: null`. Responses MUST name the target and its
subject. Only the recipient of an `ask` may answer it; the asker decides whether
the answer is adequate. A further question gets a new ID. A correction to an
answer is communicated explicitly and requires reassessment of affected work.

An `inform` or `receipt` may refer to any valid message. Receipts are optional
unless requested by the goal. Receipts MUST NOT be acknowledged with further
receipts. Duplicate requests MUST NOT create duplicate commitments.

### Commitments and decisions

Separately requested tasks follow request → accept → submit → verify, or decline.
A rejected submission may be revised and submitted under the same terms. An owner
may release an accepted commitment after stopping affected actions and describing
completed or uncertain effects. The requester cannot withdraw an already accepted
request to revoke active work. Reassignment requires a new accepted request.

Task terms are immutable: changed scope, inputs, criteria, or owner require a new
task identity. Only one submission may await review per task. Verification accepts
only its target submission and exact revisions. Acceptance authorizes ordinary
subtasks/checks inside scope without repeated approvals. The peer still evaluates
checks, consequences, and uncertainties instead of merely agreeing.

A proposal includes its author's assent; the peer's explicit agree establishes
bilateral agreement to those terms. Replacing an agreed decision needs a new
agreement citing the old one. Conflicting control replies, such as accept/decline
or verify/reject of one target, block affected work. Participants MUST expose the
conflict and agree replacement terms referencing all relevant evidence. Arrival
order MUST NOT select a winner, and explanatory text cannot erase history.

## 6. Dependencies and waiting

Use synchronous waiting only for a real dependency. Other useful work MAY continue.
An await body contains `blocks` (a nonempty scope array), `reason`, `deadline`
(null or UTC `YYYY-MM-DDTHH:MM:SSZ`), and `condition`. The condition contains exactly
`kind`, `from`, `subject`, and `target`. Kind is answer, accept, agree, verify, or release; from is
a participant, subject contains type/id, and target is null or an exact message ID.
For example, await a verify from the reviewer targeting one exact submission.

A matched predicate is evidence to assess, not automatic permission. Only the
await sender publishes resume replying to that await, citing the matching event
and explaining why the condition is satisfied. This resume act releases a task
dependency; it has no relationship to reconnecting a conversation. A changed
artifact cannot satisfy a gate referring to an older revision without reassessment.

A published deadline survives later bounded wait calls. Timeout means the condition
has not been established; it never grants acceptance, ownership, or completion.
Expired or cyclic dependencies require peer replanning. A participant MUST NOT
hold resources needed by the peer while waiting for that peer to release its own
dependency. Correlated waits MUST surface other peer attention so crossed questions
can be answered. Poll an existing yielded process instead of duplicating waiters.

## 7. Ownership and authority

Independent reads and cross-checks are encouraged. Before editing shared resources,
settle overlapping accepted and outstanding scopes. Only explicit acceptance or
agreement grants a reservation. Preserve user changes and coordinate repository-wide
operations. Changes to agreed ownership require a handoff; silence does not release
it. Unrelated work can continue during a conflict. A checkpoint is evidence of
processing, not an exclusive OS file lock.

Peer agreement never grants authority reserved to the user. Publishing, deploying,
destructive operations, or external messages require the authority already supplied
by the user/environment. Task helpers, when separately authorized, MUST NOT occupy
a participant slot or reuse a participant's connection. A collaboration request
alone never authorizes launching the other peer.

## 8. Durable memory

Immutable messages and captured artifacts are the collaboration record. A
participant's conversation context is a cache. Its checkpoint records goal,
participant, execution credential, handled peer message IDs, all own outgoing IDs,
deferred and invalid records with reasons, active commitments, and planned/running/
uncertain/resolved external actions. Only that participant writes it atomically.

After context loss within the same conversation, retain the connection, replay
messages and exact artifact revisions, reconcile the checkpoint, and continue
existing commitments. Do not repeat readiness or the accepted opening. Missing or
corrupt checkpoints require full replay and repair, never invented state. Inspect
actual external effects before retrying an uncertain action. Nothing in durable
memory authorizes a new conversation to assume a previous participant's identity.

## 9. Delivery and progress

Check the board at startup, between subtasks, and before integration or scope
changes. Handle unread requests, questions, submissions, decisions, and incidents;
then acknowledge exact IDs. Optional receipts acknowledge processing only and MUST
NOT trigger chains of receipts. Do not resend a request merely because its reply
is delayed. Process-level waits are bounded and wake for relevant peer attention.

When personal work is finished while the peer remains active, share findings and
continue independent checking or bounded waiting. Do not declare the shared goal
blocked or complete because your own portion is done. Involve the user only when
there is a concrete missing authority or external prerequisite, not ordinary peer
latency. Check pairing/readiness before making a peer-absence claim.

## 10. Ordinary exchange

1. Two agents pair and independently send ready.
2. Peer-a proposes owners, scope, criteria, artifact paths, final writer, and
   completion proposer. Peer-b agrees or counterproposes.
3. Both investigate, exchange findings with evidence and uncertainty, and challenge
   consequential claims. The accepted plan requires no redundant task handshakes.
4. One assembles the report and the other reviews captured revisions. Corrections
   receive evidence-based review.
5. The designated proposer makes the whole-goal completion claim at exact captured
   revisions. The peer explicitly agrees or objects.
6. Both observe the same agreement through automatic closing and stop.

## 11. Whole-goal consensus

Personal completion and two independent done messages do not establish consensus.
After coverage, checks, peer reviews, and limitations are settled, only peer-a
proposes the decision with exactly the goal-completion scope. Its proposal MUST
state that the whole shared goal is complete and identify the final revisions,
checks, limitations, and any recorded disagreements. Peer-b must review and
explicitly agree to that exact claim, or object with reasons.

There is one unsettled completion offer. A withdrawn or objected offer cannot be
accepted. A changed report before agreement needs withdrawal and a new reviewed
offer. After agreement, a courtesy withdrawal or competing completion offer is
invalid. Both participants MUST close against the same agreement. Serialized
control operations prevent an acceptance/withdrawal race from committing both.

A helper MAY carry out each participant's closing acknowledgment automatically.
It MUST NOT manufacture consent from a wait, an idle completion call, or a personal
done statement. Unhandled peer information requires assessment before approval or
closing; if consent was not yet recorded, an explicit agreement is still needed.
Repeated completion calls continue mechanical closing without repeating consent.

After both participants observe the agreement, stop editing the approved output.
The captured revision remains authoritative; changed working files are not silently
approved. History is preserved. Session completion has no effect on discovering
or completing other collaboration sessions.
