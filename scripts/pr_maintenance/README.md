# PR maintenance

This package coordinates one maintenance workflow per numeric GitHub PR ID.
It runs deep-module, cleanup, feedback, and conflict stages in that order.
The coordinator runs on sleeper-service. The worker runs as the normal OpenCode
user on contents-may-differ. Neither service is enabled by default.

By default, the coordinator reads GitHub using a read-only App installation token. The worker
uses the developer's existing Git credentials for normal fast-forward pushes.
The package has no operation for creating PR comments, reviews, or metadata
changes. Sharing the developer account is **not** a sandbox. An agent, repository
hook, test, or plugin can still use ambient credentials. Accept that exposure
explicitly before a pilot.

## Run the local checks

From this directory, run:

```sh
uv run --group dev pytest -q
```

The tests use fake HTTP, fake runners, and temporary Git repositories. They do not
submit model prompts, send Telegram messages, or push to GitHub.

## Configure the hosts

`config.example.json` and `worker.example.json` contain disabled examples. The Nix
modules generate the deployed configuration files and the private report wrapper.
Use the module-generated `report_command`, rather than copying the example path.
Keep credentials out of these JSON files and Nix settings.

The coordinator environment uses `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, and
`GITHUB_APP_PRIVATE_KEY_FILE`. Signed ingress additionally uses
`GITHUB_WEBHOOK_SECRET_FILE` or `GITHUB_WEBHOOK_SECRET`. The private report child
loads the existing bot environment through a systemd credential. The SSH child
does not inherit App or bot environment variables.

An explicitly selected `github_auth: "gh"` mode reuses host-local personal
authentication with `personal_auth_acknowledged: true`. Exact-allowlist mode
requires repository names and numeric repository/PR IDs. Organization-wide mode
requires `private_repositories_only: true` and verifies the organization's numeric
ID. Empty allowlists then include all private repositories; nonempty lists narrow
that scope. Public repositories never qualify in private-only mode. The client
verifies the authenticated account against `author_id` and constructs only scoped
GET requests. The credential itself can still have write or administration access.
Neither authentication mode falls back to the other.

Set `recent_interaction_days: 7` to require a recent contribution before discovering
new PRs in a repository. This optional filter requires personal `gh` authentication.
It selects repositories with evidence in **both the authenticated account's latest
300 feed events and the last seven days**. It verifies the event's numeric actor
against `author_id`. Pushes, issue and PR activity, issue comments, reviews, inline
review comments, and commit comments count. Views, stars, watching, and another
account's actions do not. Repository `updated_at` alone is not evidence.

Each poll recomputes the selection without retaining older eligibility. GitHub can
delay feed events by up to six hours. Authored commits pushed by somebody else may
not appear, and actions by automation using the configured account do count.
Normal private-organization and repository allowlist restrictions still apply.
Set the option to `null` to disable this additional discovery filter.

Tracked workflows continue even after their repository's activity evidence expires.
Unknown webhook PRs must pass the filter too. Inactive repositories keep their
discovery cursors for catch-up when they qualify again. If the activity feed cannot
be read, the poll skips new discovery rather than scanning all repositories.
Tracked PRs still receive priority refreshes; ordinary GitHub freshness checks and
backoff can delay their execution independently.

The runner defaults to explicit-URL access. Set `opencode_connection: "managed"`
to use the usual CLI-managed instance's existing authentication, with
`opencode_server` pinned to its expected loopback URL. Endpoint movement blocks the
invocation rather than switching silently. A server-filled `variant: "default"`
is accepted only when no model variant was explicitly requested.

`models` and `agents` require explicit entries for `deep_modules`, `cleanup`,
`feedback`, `conflicts`, and `review`. Model names use `provider/model#variant`,
with the variant optional. No default model or agent is substituted. Trusted
`verification_profiles` map repository names to lists of argv arrays. Otherwise,
the stage records selected existing checks in its initial scope manifest before
edits. The worker runs those checks itself and reviews the exact candidate.

`stage_deadline`, `verification_deadline`, and `review_deadline` configure the
coding, verification, and review timeouts. The coordinator also enforces the
overall stage deadline from actual execution start, not offline queue time.
`correction_rounds` bounds total final-conflict attempts after base advancement.
Review failures and other uncertain failures stop for inspection instead of
repeating model work automatically.

The default `quiescence_mode: "strict"` requires trusted host-side quiescence
probes. There is deliberately no default probe based only on an idle foreground
session. Configure fixed argv arrays, not shell source:

- `runner_quiescence_argv` accepts `{session_id}` and `{server}` placeholders. A
  successful probe returns JSON with the matching `session_id`, `quiescent: true`,
  and `descendants_quiescent: true`.
- `worker_quiescence_argv` accepts `{attempt_id}` and `{unit}` placeholders. It
  confirms local descendants have stopped, excluding the inspecting supervisor.
  A successful probe returns the matching `attempt_id` and
  `descendants_quiescent: true`.

The probes must cover the approved runner, background shells, descendants, and
local Git or verification processes. A nonzero exit, missing probe, or uncertain
proof keeps interrupted work held. A boolean rollout acknowledgement does not
prove that a probe is adequate. Validate it against the disposable pilot before
enabling execution. All checkouts and histories are retained by default.

Alternatively, `quiescence_mode: "best_effort"` with
`best_effort_acknowledged: true` permits successful turns to use consecutive
observations of owned session activity, inboxes, child sessions, and registered
shells/terminals. Versions, model selection, exact verification/review, fresh
grants, and private-scope checks still apply. This mode cannot prove detached
descendants stopped. It does not automatically release interrupted or failed
attempts; those retain ownership for inspection. Status records the selected
assurance; notifications warn when monitoring is best effort. Do not set
`runner_contract_verified` merely to use this
mode.

## Inspect and control workflows

`health` reports tick liveness separately from operational readiness. Worker
transport failures, retained execution holds, a recovery hold, stale ticks, and
stale pending reports make `healthy` false. Normal running work does not. Health
does not wait behind the coordinator's operator lock; private report delivery has
its own nonblocking lock.

Transport failures and unexpected coordinator exceptions retain owner-only JSON
diagnostics beside the coordinator database. Journal entries name the safe
category and artifact, not raw tool output. Diagnostic text can still contain
credentials and must remain private. Retention keeps at most 200 generated records,
with 64 KiB per private text field and 256 KiB per record. Truncation is explicit.
Storage failure reports that evidence was not retained.

Telegram summaries show stage outcomes, short explanations, verification status,
and decisions. PR, published commit, and review links replace inspection metadata.
Session IDs, attempt IDs, checkout paths, exact commands, and log hashes remain in
private status and artifacts. Single-message reports have no delivery footer.
Multipart reports repeat the report heading with `Part N/M`; internal deduplication
keys never become delivery labels. Previously queued messages keep their stored text.
Delivery does not depend on an extra model or readability subagent.

Known limitation: an unchanged checkout can be recorded as `noop` even when
`result.json` explicitly reports `BLOCKED`. Inspect the private result, checks,
and feedback dispositions before interpreting historical no-ops as success.

Discovery failure notices name the repository or PR and link to GitHub when known.
They explain the reason, HTTP status when available, automatic retry, impact, and
action. A retry timestamp is the earliest allowed retry, not a scheduled run time.
Initial-inventory failures prevent new work from starting; later repository scan
failures can delay discovery while independently refreshed PRs continue. Existing
queued reports remain unchanged. Contextual scan, malformed-record, and PR-read
notices are deduplicated separately from the older numeric-ID-only notices. Each
such notice still sends once per repository or PR identity, not once per outage.

The outbound credential filter is not a repository secret scanner. A rejected
notification now identifies its coordinator host, a private text file, and the
first matching line in that file. The file contains the rejected notification,
not proof that a repository file contains a credential. Inspect it to decide
whether to rotate a real credential or dismiss a false positive.

Rejected notifications are retained under `withheld-reports/` beside the configured
coordinator database. Each report identity keeps its first rejected body in a
`0600` file within a `0700` directory. These files may contain real secrets. Apply
the private-state backup and retention policy, and do not paste their contents
into Telegram or public issues. An internal-key-only match hashes the key
and sends the safe body without a secret warning. Unused PR bodies
and logs do not suppress summaries. Worker result rejections name the existing
private `result.json` file to inspect.

Use the local CLI as the service account. Its database and artifacts contain
private repository information. Use the immutable PR ID shown by `status`, not
the PR number.

```sh
pr-maintenance --config /etc/pr-maintenance/coordinator.json status
pr-maintenance --config /etc/pr-maintenance/coordinator.json pause 123456 --reason 'Inspect verification evidence'
pr-maintenance --config /etc/pr-maintenance/coordinator.json resume 123456 --reason 'Evidence accepted'
pr-maintenance --config /etc/pr-maintenance/coordinator.json cancel 123456 --reason 'Human takes over'
pr-maintenance --config /etc/pr-maintenance/coordinator.json enroll 123456 --reason 'Approved disposable pilot'
pr-maintenance --config /etc/pr-maintenance/coordinator.json retry-stage 123456 cleanup --reason 'Dependency is now installed'
```

`enroll` is the explicit backfill operation for a pre-enablement ready PR.
`retry-stage` selects one stage and creates a fresh attempt only after its prior
execution is quiescent and publication is known. It does not enroll excluded PRs.
Closing or reopening a PR never starts another automatic workflow.

Cancellation revokes future grants first. An unreachable worker means pending
cancellation, not confirmed cancellation. A push already in flight cannot be
recalled. Do not remove a branch claim or replace an attempt to work around an
unknown result.

Independent PRs run concurrently, including branches in the same repository.
There is no global maintenance queue or worker-slot limit. Stages within one PR
remain sequential. Durable ownership prevents overlapping attempts for the same
PR or head branch, including after restart. An interrupted attempt holds its own
PR and branch, not unrelated PRs. `status.metrics.active_workflows` counts active
workflows; same-branch conflicts appear as `waiting_same_branch` pauses.
A merged PR that never received an attempt gets one final report explaining that
maintenance never started. Previously recorded wait evidence remains in final
reports, but no new shared-worker queue notices are generated.

GitHub throttling, discovery budgets, worker outages, and ledger integrity holds
can still delay dispatch. Parallel execution does not remove these checks or
reserve disk space. Each attempt keeps its isolated checkout and checks free
space before execution; failures retain artifacts for inspection.

For inspected best-effort interruptions with known nonpublication, the worker's
local `resolve-interrupted ATTEMPT_ID --accept-best-effort --reason '...'` command
records explicit acceptance of detached-process uncertainty. It requires stopped
unit and verification-process observations, quiet owned sessions, and a fresh
unpublished-candidate check. It retains artifacts and ends the attempt as blocked
without another prompt or push. It is not available through the SSH transport.
Cancel the workflow on the coordinator before recovery, then reconcile the worker
outcome before restoring dispatch. This command does not prove detached
descendants stopped or recover an unknown publication outcome.

After a publication incident, `resume` records an audited acknowledgement with a
fresh epoch. The worker must confirm that acknowledgement before it releases the
branch claim or accepts the next stage. The original incident outcome remains in
both ledgers.

When two PRs share a head branch, the later workflow pauses. After the current
owner has stopped and its publication is known, `resume` with an audit reason
selects the paused workflow as the next owner. This is an explicit choice to run
another maintenance sequence on that shared branch.

See [Operate PR maintenance](../../docs/runbooks/pr-maintenance.md) for rollout,
inspection, backup, and recovery procedures.
