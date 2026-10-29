# Duet utility contract

## Simple interface

Both peers run persistent `/goal` loops. Duet coordinates their exchange; it does
not supply the host loop or launch a peer. Run `python3 -B <skill>/scripts/duet.py`.
JSON goes to stdout and diagnostics to stderr. `--help` lists complete arguments.
Users provide a task and optional output location/done criteria, not protocol IDs.
Pass the shared task wording, without the CLI's leading `/goal` invocation token.
Invoke one command at a time with the quoted literal script path; avoid shell
path variables, wrappers, and chained calls that obscure permission matching.
An execution denial applies to the rejected operation. Inspect its reason and
the permitted scope; use a different invocation only when it is independently
authorized, never to evade the denial. An unavailable coordination operation
does not authorize task work, alternate communication, or a unilateral report.
Before gate agreement, report an unresolved blocker to the user as coordination
status only, without task analysis or writing task artifacts.

### Pair

```bash
python3 -B <script> join --workspace <project> <<'TASK'
<unchanged request>
TASK
```

Join returns a short `connection` token plus its full `connection_path` fallback,
`status` (waiting/paired), generated identities, `settings_path`, `board`, shared
`temp_directory`, default `artifact_directory`, and initial/completion proposer.
Host and user output limits override that default for all task artifacts, including
scratch findings. If files are not permitted, share compact observations inline.
Retain both connection forms. The opaque short token binds a specific actor and
collaboration; it never means “latest session” or “this client's role.” Its lookup
is in the caller's temporary root. If that root changes or the lookup disappears,
use the retained full path. Failure to create optional shorthand returns the full
path, rather than stranding enrollment. Neither form authorizes another writer.

The project `.duet.jsonl` lobby is serialized under `.duet.lock`. It contains
waiting, renewed, and paired events and is logically append-only, with atomic
whole-file replacement preventing torn records. A paired session permanently
leaves discovery. Any number of pairs can run concurrently. Each board is at:

```text
<temporary-root>/duet/<project-label>-<workspace-sha256>/<collaboration-id>/
```

The advertiser uses existing TMPDIR or Python's temporary-directory lookup. The
joiner uses the advertised absolute board regardless of its own environment.
Old `.duet.json` pointers are left alone and ignored by discovery.

Identical requests match after whitespace normalization. Other waiting tasks return
`match-required`, candidate text, and an observation. Assess compatibility, then
resubmit the original request with `--match <session>` or `--separate <observation>`.
Claims and separate advertising are rechecked under the lock; reassess changed
candidates. Exact matches take precedence over another advertisement.

Join once; poll yielded processes rather than duplicating them. `--dry-run`
reserves nothing. While unmatched, bounded waits renew a valid invitation's
five-minute default lease. An expired invitation cannot be revived; join afresh.
Paired sessions never expire. Silence and timeout do not prove process death.
Keep the workspace path and lobby registry intact while a pair is active;
removing discovery metadata or moving the workspace is not a session-resume mechanism.

### Define done before main work

Apply the practical read-only scope below while negotiating the gate. Do not turn
an ordinary read-only request into a no-new-files or no-checkout-writes condition.
In particular, a supplied check that creates only new disposable runtime files may
run after agreement without a special exemption or compulsory scratch copy.

```bash
python3 -B <script> send --connection <handle> --kind ready --text 'Ready to negotiate.'
python3 -B <script> send --connection <handle> --kind propose --scope work-division --criteria '<first done condition>' --criteria '<second done condition>' --stop-condition '<bounded unsuccessful-stop/reassessment rule>' --text '<scope, owners, user constraints, method, output, final writer>'
python3 -B <script> send --connection <handle> --kind agree --reply-to <plan-id> --text '<explicit acceptance>'
```

The initial proposer offers the plan after both readiness messages; the peer
agrees or counterproposes. `criteria` is a required nonempty string list and
`stop_condition` required meaningful text. The utility checks presence and exact
agreement, not whether a criterion expresses the user's goal well. Preserve user
criteria; derive faithful bounded criteria when the user supplies a broad goal.
No user prompt boilerplate or separate gate-handshake phase is required.

The gate must cover mandatory requirements from both peers' governing instructions
and the objective each host is pursuing. An approval boundary does not remove an
action from that objective: if posting is required but approval is pending, a draft
is not successful completion. Resolve differing interpretations before assent;
only an authoritative user instruction can narrow the task to review/draft handoff.

User ordering takes precedence over the discovery default. “Agree before work”
requires a gate before task metadata or content inspection, even when the quality
criteria are broad. Reading governing instructions and coordination metadata is
preparation unless forbidden. Agree a meaningful bounded gate from the request;
it may authorize scoped inspection and subsequent bilateral refinement before the
main investigation. “Decide done later” alone is not a completion criterion.
For example, a read-only review can require coverage of the agreed change, supported
findings, explicit validation limits and joint approval of the report, while the
first authorized step identifies the revisions and refines coverage.

Absent an agreement-first restriction, extract clear user completion conditions
and agree them before investigation. If unclear, each peer after publishing ready
may inspect within a stated finite read-only scope solely to define done. Use scoped
helpers when useful; share provisional observations inline. Stop at the boundary
and propose criteria or seek a narrow clarification. Discovery permits no execution
of project code, edits, artifacts or full investigation. The utility keeps task
lifecycle acts and capture behind the accepted gate; native inspection remains
the agents' responsibility.

Separate user requirements from peer-proposed process choices. Resolve ambiguity
within the user's constraints, choosing a faithful interpretation that permits
progress. If interpretations materially change permission or success and cannot
be resolved, ask one focused user question; continue only work allowed under either.
New process choices apply prospectively, not as invented historical obligations.
When an earlier action raises a possible violation, distinguish the observed facts,
the exact applicable instruction and the peers' interpretation; seek clarification
once if it changes the disposition. Do not assume a waiver or conceal a real breach.
If premature work occurred, disclose what happened to the peer before continuing.
Agree how to reassess affected work within the user's constraints; peer consent
cannot waive a user requirement. Preserve material deviations in the final report
and never claim that later agreement preceded the earlier work.

Amendments use the same work-division scope and full replacement criteria and stop
condition. Link earlier proposals with `--cause`, not `--reply-to`; the utility adds
the accepted gate. State changed process choices and their reason, preserving user
requirements. `status --connection` retains `user_request` and adds `gate_review`
when needed: added/removed/reordered criteria, stopping/plan diffs, actual causal
bases, and whether they match the current gate. Stale-base comparisons also show
changes against the currently accepted gate. This view is read-only, not acknowledgment
or proof that removed conditions may be waived. Review the exact proposal before assent.
The initial proposer leads consolidation; either peer may challenge or counterpropose.
Receive replies and inspect the accepted gate before replacing terms. Cite that
gate when amending it. If acceptance crosses a replacement, withdraw the unsettled
replacement or amend the accepted gate with fresh assent; do not request acceptance
of both. If both were already accepted, reconcile those acceptances explicitly.
This is coordination responsibility, not superior decision authority. Accepted
peer-added rules also need bilateral amendment; do not silently discard them.
Competing accepted amendments need explicit reconciliation. Incidents belong in alerts
and completion evidence; cleanup never makes earlier noncompliance disappear.

### Run project commands within constraints

Interpret an unqualified read-only request practically: preserve source,
configuration, project data, Git refs/staging and existing project files, including
ignored outputs. Reports, scratch files, normal CLI state and disposable caches
needed for authorized work are allowed. Prefer the shared temporary area outside
the checkout. Necessary new disposable tooling files inside the checkout are also
allowed; a cache label does not authorize overwriting a pre-existing `.coverage`
or other project file. Normal client-owned state outside the project may follow
its usual lifecycle within host permissions.

Explicit restrictions such as “keep every file and directory unchanged” or “no
writes inside the checkout” override this default. A report output path alone
does not prohibit operational scratch elsewhere; an explicit output-only restriction
does. This allowance changes neither gate ordering nor publication authority.
During startup/discovery it permits operational files, not premature task deliverables
or execution of project code.

Creating and removing session-owned disposable scratch is ordinary task work under
this default. Tests may clean up only scratch they created. Ownership must be known,
not inferred merely from a path being under TMPDIR. Preserve pre-existing/unfamiliar
files, other sessions' files, final reports, required evidence and the durable board.
Do permitted cleanup before proposing successful completion; explicit retention,
approval or host restrictions override the default. Do not use broad deletion globs.

After the gate, inspect effective tool/project configuration before execution.
Account for implicit reports, caches, generated files and subprocesses. Use permitted
scratch storage, a copy or enforced isolation when appropriate; otherwise skip unsafe
execution and retain any mandatory check as unmet. No flag list guarantees read-only work.
For Python, `-I` ignores `PYTHON*` variables, including `PYTHONDONTWRITEBYTECODE`;
explicit `-B` still suppresses import bytecode writes. Neither prevents other outputs.
Pytest may inherit coverage/HTML options through `addopts` and create caches;
inspect its effective configuration rather than trusting a short command line.
Account for native command wrappers as well as project tools. New identifiable
temporary scaffolding alone does not establish a read-only breach or require an
incomplete outcome. Inspect unexpected changes and preserve consequential uncertainty;
do not demand forensic proof that no temporary file ever existed. Do not delete
unfamiliar files or bypass host permissions to manufacture a clean checkout.
See [Python options](https://docs.python.org/3/using/cmdline.html#cmdoption-I),
[pytest-cov configuration](https://pytest-cov.readthedocs.io/en/latest/config.html),
and [pytest cache behavior](https://docs.pytest.org/en/stable/how-to/cache.html).

If a command breaches a constraint, stop that operation and publish an `alert` with
`--scope <affected-area>` and `--cause <related-message>` where useful. Distinguish
what was written, what prior state is known, and what remains unverified; a current
hash cannot prove no earlier file was overwritten. Recover only within user authority.
Keep consequential uncertain effects recorded, and use criterion evidence to distinguish
a finished deliverable from compliant execution. A mandatory condition still unmet
requires an incomplete outcome unless the user explicitly changes the requirement.

### Delegate without creating another peer

Encourage scoped helpers for inspection, work slices and checks where they save
context or improve coverage. Pass the objective, permitted inputs/actions, constraints
and stopping boundary; ask for compact findings with evidence and uncertainty.
Helpers do not join Duet, receive connection credentials or approve completion.
Each peer validates helper output and retains responsibility for its own assessment.

### Exchange evidence and inspect state

```bash
python3 -B <script> send --connection <handle> --text '<finding and uncertainty>' --artifact <file>
python3 -B <script> receive --connection <handle> --unread
python3 -B <script> send --connection <handle> --reply-to <message-id> --text '<substantive response>'
python3 -B <script> ack --connection <handle> --messages <handled-id> <handled-id>
python3 -B <script> status --connection <handle>
python3 -B <script> wait --connection <handle>
```

Normal sends/retries return compact receipts and captured artifact references.
`--details` includes the constructed envelope if needed for diagnosis. Message IDs,
identity, reply subjects/causes, and captured-file digests are utility-generated.
Use `--text -` for bounded UTF-8 stdin instead of shell interpolation.

### Reconcile a control conflict

`status --connection` lists the conflicting reply IDs in each conflict blocker.
Propose full replacement terms with `send --kind propose --scope conflict-resolution
--cause <reply-a> --cause <reply-b> --text '<replacement terms>'`; the peer uses
`send --kind agree --reply-to <replacement-id>`. Cite every conflicting reply.
`reconciled_conflicts` names the resulting agreements; old messages remain intact.
Old task commitments still need verification or owner release. Reconciliation does
not select an old conflicting verification or satisfy a mandatory user criterion.
If replacement agreements compete, cite all of them as additional causes in a
fresh replacement. Corrected answers need no reconciliation: assess their evidence.

### Inspect processing state

`status --connection` shows the effective gate, recognized tasks/waits, reported
external-effect states, blockers, current offer, freshness, and actor-appropriate
next actions. It does not understand every requirement hidden in prose or prove
what happened in external systems. `status --workspace` inspects the lobby only.

`receive` reads history and own checkpoint without acknowledging. `--unread`
emits unprocessed peer content. Handle messages, then batch exact IDs in `ack`.
A successful linked reply acknowledges its own target only. Reading, processing
acknowledgment, receipt, task acceptance, and final consent remain separate.
Keep protocol history on the board; deliver results and relevant evidence to users.

Use `retry --connection <handle>` for uncertain send results. It republishes the
saved bytes/ID and completes an interrupted reply acknowledgment. A prepared
uncertain send blocks replacement until reconciled. Do not blindly rerun a send.

Artifact capture reads bounded regular bytes into immutable review copies. Review
those copies. New approvals recheck capture integrity and matching working origins.
Changed bytes require fresh review. After terminal completion, the captured copy
is the approved deliverable; future origin edits do not approve new bytes.

### Propose, correct, and close

Before either consent, compare the shared completion claim with the full objective
your host would mark achieved. Explicitly confirm all required work and permitted
cleanup are finished, no mandatory approval/action is pending, and you are ready to
exit successfully. The proposer includes that judgment in its `--text`; the reviewer
makes the same judgment with `--agree`. There is no additional exit handshake.
The helper records explicit exit readiness in both messages, preserving any supplied
comment. These are agent assertions: the utility cannot inspect a host goal or prove
that prose covers every requirement. Do not invoke successful consent unless true.
Ordinary report review can use `send` without whole-goal completion if required work
remains. For mandatory publication awaiting authority, present the concrete draft
and approval request once; do authorized work or bounded waiting until resolution.
At the agreed unsuccessful stop bound, use incomplete, not success. A user-requested
draft-only task may complete with publication excluded by that explicit request.

Only the completion proposer:

```bash
python3 -B <script> complete --connection <handle> --propose --artifact <report> --text '<whole-goal claim>' --evidence '<evidence for first criterion>' --evidence '<evidence for second criterion>'
```

Supply one nonempty evidence statement per effective gate criterion, in order.
The utility binds the gate, journal context, and reported-effect fingerprint. A
populated evidence list does not establish correctness. Successful completion
also requires dispositions for recognized obligations and no unresolved journal
errors/conflicts. An accepted task stays outstanding until verified or legitimately
released; pending requests may be declined/withdrawn. A completion-blocking await
needs the sender's release or justified withdrawal. Cancelling optional work does
not satisfy a mandatory user condition. A limitation is not a waiver.

Reported running/uncertain external effects and invalid checkpoints block success.
Reconcile actual effects under user authority. Checkpoints are caches: missing
records do not prove no effects exist. Journal consequential discoveries as alerts;
a changed reported-effect context also makes a pending offer stale.

The reviewer explicitly accepts or requests changes:

```bash
python3 -B <script> complete --connection <handle> --agree <offer-id>
python3 -B <script> complete --connection <handle> --request-changes <offer-id> --text '<correction or new evidence>'
```

A request produces an attention-bearing object before approval or alert afterward.
New content also interrupts a completion wait as changed context; it never counts
as approval. Use the correction helper to make the requested action explicit. The proposer uses:

```bash
python3 -B <script> complete --connection <handle> --revise --artifact <report> --text '<reassessed whole-goal claim>' --evidence '<first criterion>' --evidence '<second criterion>'
```

The helper performs any necessary withdrawal/alert and prepares a fresh offer.
An objection needs no additional withdrawal. Unchanged captured bytes can be reused,
but fresh assent is still required. A dry-run revision previews its first control
action; it does not validate the entire prospective multi-message operation.
If interrupted, inspect status and use retry for the saved send. Do not repeat
`--revise` blindly or treat retrying a withdrawal as publishing a new offer.

Once an offer is pending, avoid routine progress/acknowledgment chatter. Processing
acks remain checkpoints. Conservatively, every new nonreceipt/nonclosing message
stales the offer; the utility does not judge semantic materiality. Changed gates,
report origins, or reported effects also stale it. Acknowledging an update does not
restore consent. Assess it, then obtain explicit consent to a fresh offer.

After normal timeouts, continue with plain `complete --connection <handle>`.
Both finish acknowledgments remain provisional. Every enrolled publication shares
the lock with terminal closing; each finish revalidates the current offer, gate,
evidence and obligations. A revised approval resets the old provisional flags.
No new messages publish after the joint terminal cutoff; exact historical delivery
replays remain idempotent. Old waiters reload the actual terminal report revision.
Normal agents do not call low-level finish themselves.

For a bounded unsuccessful outcome, propose `--outcome incomplete`, still with
one evidence statement per criterion explaining what remains unmet. The peer must
explicitly agree. Terminal `status: incomplete` has `terminal: true` and `complete:
false`; do not mark the user's goal achieved. If the peer is known unavailable,
report local incomplete work and seek a user decision; bilateral stop cannot invent
the absent peer's assent. Ordinary waiting alone does not establish peer loss.

After either joint terminal outcome, stop collaboration work. Deliver the approved
artifacts and explain unmet conditions and any required user decision once. Follow
the host's available goal statuses and their actual prerequisites: Duet's terminal
incomplete is not host success, nor authority to pause or declare blocked early.
If the host continues the goal, retain the terminal result and report the outstanding
condition concisely; do not repeat finished checks, reopen negotiation automatically,
or repeatedly ask for a waiver. New user instructions may require fresh work; they
do not alter the closed board or its recorded outcome.

## Advanced acts and settings

Use send's `--scope`, repeated `--criteria`/`--evidence`, `--subject-type`/`--subject-id`,
and `--cause` for task/decision relationships. `--body <json-file>` supplies uncommon
semantic fields such as an await predicate, never an envelope. Dependency resume
releases an await; it is unrelated to reconnecting a conversation. Read PROTOCOL.md
for exact acts and fields. Retained low-level setup/validate/publish/checkpoint/finish
interfaces support tooling; they do not authorize borrowing participant identities.

`join --options <json-file>` configures the advertiser. Joiners normally omit it;
explicit overrides must match. Unknown fields and identity overrides fail. Defaults
live in scripts/settings.py: invitation lease 300 seconds, message polling 1 second,
command wait 30 seconds, short-lock polling 0.01 second. All are typed settings.
Lock timing uses central defaults because task configuration may not yet exist.
Published dependency deadlines survive bounded command timeouts.

Other bounds are max_message_bytes=65536, max_messages=1000,
max_checkpoint_bytes=131072, max_artifact_bytes=16777216, and
max_registry_bytes=16777216. No partial scans or silent history eviction occur.

Checkpoints contain goal, participant, session, processed/outgoing IDs, deferred
and invalid records, active notes, and external_actions. Only the participant
writes its checkpoint. Effect entries have id, status (planned/running/uncertain/
resolved), and summary. Reconcile uncertain effects against the external system,
not a peer's confidence. Full replay/repair precedes unread delivery when a cache
is invalid. Serialized terminal records preserve the approved result independently
of later processing-cache loss; they do not certify unrecorded external activity.

| Exit | Meaning |
| --- | --- |
| 0 | Success or actionable nonterminal status; inspect status/terminal/complete. |
| 1 | Invalid data, I/O error, or required repair. |
| 2 | Bounded wait/closing timeout; no implied approval. |
| 3 | Attention, stale offer, blocked success, or expired unmatched invitation. |
| 5 | A correlated decline, objection, or rejection. |

Publication stages closed files, uses no-replace hard links for messages, and
atomically replaces control/cache files. IDs/bytes are idempotent; conflicting
reuse fails. Symlink metadata, nonregular records, traversal IDs, and oversized
inputs fail. File/directory fsync is attempted on supported POSIX storage. These
mechanisms assume cooperating local agents; they do not confer user authority,
intercept native tools, or guarantee arbitrary network-filesystem semantics.
