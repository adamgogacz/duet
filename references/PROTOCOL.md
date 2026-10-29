# Duet collaboration protocol

Duet coordinates two independently started autonomous peers. It is independent of
agent vendor and task domain. Both independently started agents MUST operate
under persistent `/goal` loops supplied by their host clients. Ordinary chat turns
are unsupported because they may exit before the exchange finishes. Duet does not
implement that loop, launch a peer, or grant tool authority. User instructions define the goal, resources,
constraints, and external authorization. MUST, SHOULD, and MAY express required,
recommended, and optional behavior.

## 1. Pairing and startup

Users provide task content and optional output/completion criteria. Peers derive
faithful bounded completion criteria when the user supplies only a higher-level goal.
Participants handle generated IDs, registry files, and shared-path resolution.
Invoking Duet permits its coordination metadata unless the user explicitly forbids
those writes; read-only work uses the practical default below. For shared output paths,
`$TMPDIR` refers to the advertiser's returned temporary root, not separate client
environments. Participants agree the concrete report location, respecting any
explicitly named file and separating concurrent outputs within supplied directories.
When negotiating an ordinary read-only task, peers MUST apply the practical scope
in section 7 rather than add no-new-files or no-checkout-writes requirements on
the user's behalf. A check that only creates new disposable runtime files remains
eligible after gate agreement; it does not require a special waiver or scratch copy.

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
readiness messages. Load the skill and user directives before content inspection.
Narrow filename lookup within permitted areas may locate instructions and inputs
at startup; it is not task-content discovery. Join before inspecting contents.
User ordering overrides the discovery default: an instruction to agree before work
requires an accepted gate before task metadata or content inspection, even when
quality criteria are broad. Governing instructions and permitted coordination
metadata remain readable. Negotiate meaningful completion criteria and a stopping
bound from the request; that gate MAY authorize bounded inspection and later
bilateral refinement before main investigation. A placeholder to decide done later
is not a completion gate.
Otherwise, when user completion conditions are clear, peers extract and agree them
before investigation. When conditions are unclear, each peer MAY, after publishing ready,
perform bounded read-only discovery solely to understand the task and independently
propose meaningful done conditions. State a finite scope and stopping boundary in
normal conversation; helpers share that boundary. No extra discovery agreement is
required. User restrictions on inspection remain binding. Stop discovery when criteria
can be proposed or its boundary is reached; unresolved material ambiguity requires
a narrow clarification rather than indefinite investigation.
Discovery uses native reads and inline observations, not execution of project code, edits,
task artifacts or full investigation. Main work and task lifecycle acts still require
the accepted gate; native tools remain outside the utility's enforcement boundary.
A failed or denied coordination command MUST NOT waive this restriction or
authorize a solo task report. Diagnose the specific operation without evading
permissions. If coordination remains unavailable, report startup status to the
user without substantive analysis or task artifacts. Premature work MUST be
disclosed to the peer and assessed under the user's constraints; subsequent
agreement MUST NOT be represented as preceding that work or satisfying an unmet
user condition. Material deviations remain part of the outcome's limitations.
Participants MUST distinguish user requirements from peer-proposed process choices.
New process choices apply prospectively and MUST NOT manufacture obligations about
past events. Preserve actual prior user constraints and incident history. If faithful
interpretations still materially differ on permission or success, ask one focused
user question and continue only work permitted under either interpretation.
Peer-a proposes the initial-plan decision for that context revision, with exactly
the work-division scope and both readiness messages in its causal ancestry. The
peer agrees or counterproposes. Counterplans cite the opening and retain its scope.
The initial proposer leads consolidation without superior decision authority.
Receive replies and inspect the accepted gate before replacing terms; amendments
cite that gate. When acceptance crosses a replacement, withdraw the unsettled
replacement or amend the accepted gate. Competing acceptances require explicit
reconciliation; never request redundant acceptance just to settle a crossed exchange.

The opening MUST carry nonempty completion `criteria` and a `stop_condition`,
covering mandatory requirements from both peers' governing instructions and host
objectives. Pending approval does not remove a required action from scope. A draft
cannot satisfy required publication unless the user explicitly makes the task draft-only.
The opening also records scope, owners, user constraints, collaboration method, artifact location,
final writer, and completion proposer. Criteria must describe a meaningful outcome
and acceptance evidence; the stop condition bounds unsuccessful investigation or
reassessment. A success condition alone does not guarantee termination on an
impossible task. Peers preserve explicit user conditions and must not silently
replace the requested outcome with an easier deliverable. Material ambiguity about
scope or user-reserved authority requires user input. Its acceptance establishes those commitments;
participants MUST NOT repeat the same responsibilities through reciprocal task
requests. New or changed commitments require new agreement. Gate amendments include full
replacement criteria and a stop condition, and explain changed process choices and
why they change. Original user requirements remain binding unless the user explicitly
changes them. Derived comparisons expose changes and stale bases but do not judge
semantic adequacy; an incident must not be relabeled as compliance by amendment. Competing accepted amendments require
explicit causal reconciliation, never arrival-order selection. Only user-reserved
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
independent investigation and evidence-based challenge remain required. Participants
MUST distinguish a claim contradicted by evidence from a claim that lacks support.
A counterexample refutes only the proposition it actually contradicts; do not
extend it into unsupported claims about adjacent assumptions or environments.

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
actually handled. Routine sends SHOULD return compact receipts; full envelopes are for diagnosis.
A substantive linked reply MAY automatically acknowledge its target after publication.
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
Answers, including corrections and answers crossing a question withdrawal, are
information rather than competing commitments; they do not create a control conflict.

An `inform` or `receipt` may refer to any valid message. Receipts are optional
unless requested by the goal. Receipts MUST NOT be acknowledged with further
receipts. Duplicate requests MUST NOT create duplicate commitments.

A work-division proposal additionally requires `criteria` (nonempty text array)
and `stop_condition` (nonempty text). A goal-completion proposal additionally
requires `gate` (effective agreement ID), `evidence` (one statement per criterion,
in order), `effects` (utility-computed reported-effect context digest), and `outcome`
(`complete` or `incomplete`). These fields do not prove semantic correctness.

### Commitments and decisions

Separately requested tasks follow request → accept → submit → verify, or decline.
A rejected submission may be revised and submitted under the same terms. An owner
may release an accepted commitment after stopping affected actions and describing
completed or uncertain effects. The requester cannot withdraw an already accepted
request to revoke active work. Reassignment requires a new accepted request.

Task terms are immutable: changed scope, inputs, criteria, or owner require a new
task identity. Legitimate release/cancellation ends ownership without satisfying
an associated mandatory user criterion. The owner may abandon a submitted branch
by release causally citing that branch; concurrent submit/release alternatives
remain conflicting. A requester cannot withdraw an already accepted task. Only one submission may await review per task. Verification accepts
only its target submission and exact revisions. Acceptance authorizes ordinary
subtasks/checks inside scope without repeated approvals. The peer still evaluates
checks, consequences, and uncertainties instead of merely agreeing.

A proposal includes its author's assent; the peer's explicit agree establishes
bilateral agreement to those terms. Replacing an agreed decision needs a new
agreement citing the old one. Conflicting control replies, such as accept/decline
or verify/reject of one target, block affected work. Participants MUST expose the
conflict and agree replacement terms referencing all relevant evidence. Arrival
order MUST NOT select a winner, and explanatory text cannot erase history.

Use an ordinary `propose` with scope `["conflict-resolution"]`, full replacement
terms, and every conflicting reply ID directly in `causes`; its peer must `agree`.
An accepted work-division replacement may also reconcile conflicts it explicitly
cites this way. This retires the covered conflict blocker, not its immutable history
or its old tasks. Accepted commitments still need valid verification or owner release;
ambiguous submission/verification branches must be abandoned by owner release and,
if needed, replaced by a new accepted request. No old conflicting approval is revived.
New conflicting replies require fresh reconciliation. Competing accepted replacements
remain unresolved until a new bilateral proposal cites the original replies and all
competing replacement agreements. A reconciliation whose own responses conflict is
never treated as consent. `status` distinguishes active blockers from recorded
`reconciled_conflicts`; raw receive history retains both resolved and unresolved conflicts.

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
by the user/environment. Scoped helpers are encouraged for bounded discovery,
task slices and independent checks when useful within that authority. Give them
narrow inputs, allowed actions, constraints and a stopping boundary; request compact
findings with locations, evidence, checks and uncertainty. Helpers MUST NOT enroll,
receive peer credentials, replace a peer or approve the collaboration. Owners validate
and integrate their results; delegation is not verification or a second peer's judgment.
A collaboration request alone never authorizes launching the other peer.

Unless the user explicitly requires stricter preservation, read-only work MUST
preserve project content, Git refs/staging and existing project files, including
ignored files. It MAY create reports, scratch files, client state and disposable
caches for authorized work, preferably outside the checkout. Necessary new disposable
tooling files inside the checkout are permitted if they do not overwrite existing
files or alter project content. Normal client-owned state outside the project may
follow its usual lifecycle within host permissions. Explicit host restrictions and
user constraints take precedence; a report destination alone is not an output-only
restriction. Operational files do not authorize pre-gate task work or publication.
Peers SHOULD check protected state and investigate unexpected changes; they MUST NOT
invent a zero-filesystem-write requirement or treat ordinary temporary scaffolding
alone as a breach. Actual damage and consequential uncertainty remain reportable.
Creation and removal of known session-owned disposable scratch are ordinary task
work under this default. Preserve pre-existing/unfamiliar files, other sessions'
files, final deliverables, required evidence and the durable Duet board. Ownership
is not inferred from being under TMPDIR. Complete permitted cleanup before final
success consent; explicit retention, approval and host restrictions take precedence.

## 8. Durable memory

Immutable messages and captured artifacts are the collaboration record. A
participant's conversation context is a cache. Its checkpoint records goal,
participant, execution credential, handled peer message IDs, all own outgoing IDs,
deferred and invalid records with reasons, active commitments, and planned/running/
uncertain/resolved external actions. Only that participant writes it atomically. Recorded running/uncertain effects
and invalid caches block successful completion. A snapshot of reported effects is
bound to the final offer, so changed effect records require reassessment. A missing
cache is not evidence that no external action occurred. Journal consequential
changes and reconcile the external system under user authority; the utility does
not provide an authoritative external-operation log.

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
2. Honor agreement-first instructions or extract clear user criteria directly.
   Otherwise each peer performs bounded read-only discovery and proposes meaningful
   done conditions, optionally using scoped helpers. Peer-a proposes the gate and responsibilities; peer-b assesses
   it against its own understanding and agrees or counterproposes.
3. After gate agreement, both pursue the main investigation or implementation,
   exchange evidence and uncertainty, and challenge consequential claims. The accepted plan requires no redundant task handshakes.
4. One assembles the report and the other reviews captured revisions. Corrections
   receive evidence-based review.
5. The designated proposer makes the whole-goal completion claim at exact captured
   revisions. The peer explicitly agrees or objects.
6. Both observe the same agreement through automatic closing and stop.

## 11. Whole-goal consensus and bounded stopping

Personal completion and two independent done messages do not establish consensus.
Before proposing or accepting success, each peer MUST reconcile the shared claim
with the full objective its host would mark achieved and explicitly confirm all
mandatory task work, actions, approvals and cleanup are satisfied and it is ready
to exit. The completion proposal and agreement are these bilateral exit confirmations;
no additional handshake is required. Approval of a report is not approval of an
unfinished wider goal. A mandatory post awaiting permission keeps success pending:
present the concrete draft and request authority once, then wait or use the agreed
incomplete stopping condition. Peer agreement never supplies missing user authority.
The designated proposer offers a whole-goal claim under the effective gate, with
one evidence statement per criterion and exact final artifact revisions. The peer
reviews and explicitly agrees or requests changes. No user criterion is waived
by labeling missing verification a limitation. An uncertainty assessment may
legitimately conclude “unresolved”; a required proof or implementation may not.
Deliverable completion, constraint compliance and unknown prior state are distinct.
Recovery may contain damage without proving the original state was preserved or
undoing a historical breach. Use existing alerts, criterion evidence and reported
uncertain effects to retain these distinctions; peer assent is not user authority.

Successful completion requires legitimate dispositions for recognized commitments,
released or withdrawn completion-blocking waits, and no unresolved journal errors,
conflicts, or reported running/uncertain effects. Acknowledgment is a processing
cursor, not a task disposition. The utility checks recorded structure; peers judge
the gate's adequacy, evidence quality, and satisfaction of user intent.

The offer binds the current gate agreement, reported effects, and causal journal
basis. Acceptance and every finish recheck that binding. Every new nonreceipt/
nonclosing message conservatively stales an offer; the utility cannot decide
semantic materiality. Changed report origins or gate/effect context also require
fresh assent. Acknowledging such information never restores old consent. During
closing avoid routine progress chatter; use checkpoint acknowledgments and an
attention-bearing correction request for consequential information.

One pending usable offer is permitted. The proposer may mechanically withdraw a
pending offer or alert that an approved claim is being reassessed, then issue a
fresh one. An objection already settles the offer and needs no extra withdrawal.
Old approvals remain historical; they do not approve revised bytes or criteria.
Unchanged report bytes may be reused under a fresh reviewed offer.

Both finish acknowledgments remain provisional. Until global termination both
actors can assess and respond to late information. A fresh agreement resets old
closing acknowledgments. Publication and terminal closing share one session lock;
final validation and commit occur under it. After the serialized terminal cutoff,
new messages fail and exact historical delivery replays remain idempotent. A
suspended older waiter must return the actual terminal agreement and its artifacts,
never mix a new agreement with an old report revision. The cutoff orders protocol
events, not unrecorded native activity or facts discovered off-board afterward.

When a gate cannot be met within its bounds, peers may explicitly agree an
`incomplete` outcome, with evidence explaining unmet conditions. This ends the
collaboration without claiming the user's goal succeeded. It does not require
pretending unfinished work was verified. A known unavailable peer cannot supply
bilateral consent: report local incomplete work and seek a user decision without
inventing approval or silently assuming an identity. Ordinary silence or timeouts
alone do not establish peer loss. No gate promises eventual success or peer return.

Either terminal outcome ends collaboration work. Deliver the approved artifacts,
unmet conditions and any needed user decision once. Host goal-status policies remain
binding: terminal incomplete MUST NOT be reported as goal success or used to bypass
host blocked/pause prerequisites. Host continuations retain the terminal result;
they do not authorize repeated finished work, automatic renegotiation or repeated
waiver requests. New user instructions do not rewrite the closed history.

After successful joint completion, stop editing the approved output. Its captured
revision remains authoritative independently of later origin edits or processing
cache loss. Changed source inputs or native effects require honest new assessment;
protocol agreement is never a proof of substantive correctness. Deliver findings,
reasoning, evidence, and relevant limits; retain detailed process history on the
board unless the user requests it. Completion of one session does not affect others.
