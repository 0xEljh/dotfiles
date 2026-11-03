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
attempts; those retain ownership for inspection. Status and private reports name
the selected assurance. Do not set `runner_contract_verified` merely to use this
mode.

## Inspect and control workflows

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
