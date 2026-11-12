# Operate PR maintenance

## Inspect the deployed private-organization pilot

The deployed scope is private EigenformAI repositories, PRs authored by `0xEljh`,
and newly eligible work after the startup baseline. Existing ready PRs are excluded
unless explicitly enrolled. All stages and review use `openai/gpt-6.1-sol` in the
usual shared instance. Best-effort completion observations are explicitly enabled;
they do not prove arbitrary detached children stopped.

On sleeper-service, inspect the coordinator's system units:

```sh
~/.local/share/pr-maintenance/nix/package/bin/pr-maintenance --config /etc/pr-maintenance/coordinator.json status
systemctl status pr-maintenance.timer pr-maintenance.service
journalctl -u pr-maintenance.service -n 30
```

Coordinator ticks run every five minutes. Worker reconciliation runs every minute
on contents-may-differ. To prevent new dispatch while inspecting an incident, stop
the coordinator timer:

```sh
sudo systemctl stop pr-maintenance.timer
```

Stopping the timer does not stop an in-flight stage or revoke an existing grant.
Use the per-PR pause/cancel controls for that. An unreachable worker or interrupted
best-effort attempt retains ownership; do not delete its ledger or checkout.

Nix-generated maintenance artifacts live under
`~/.local/share/pr-maintenance/nix/` as garbage-collection roots. The coordinator
uses system units on sleeper-service. The worker uses Nix-generated user units on
contents-may-differ. Its dependency-pinned SSH wrapper is
`~/.local/share/pr-maintenance/nix/worker/bin/pr-worker`, not a bootstrap script
or an assumed executable in the worker's older `/run/current-system`.

The migration activates only maintenance-specific Nix artifacts, not a complete
NixOS generation. The coordinator's `/etc/systemd/system` is immutable, so five
links in `/etc/systemd/system.control/` select the generated coordinator units.
Two `timers.target.wants/` links and one `multi-user.target.wants/` link in that
directory preserve startup enablement. Worker links in `~/.config/systemd/user/`
select generated worker units and take precedence over `/etc/systemd/user/`.
Advance or remove these selections explicitly during a future whole-host switch.
Retain old bootstrap releases and backups; they are not the active scheduler.
Never enable both user and system coordinator timers against the same database.
No public webhook is enabled.
Configuration files and production state are separate from the smoke-test artifacts.

Before resuming dispatch after any deployment, verify the exact configured worker
command through noninteractive strict SSH. Require the expected ledger identity,
matching Python package hashes on both hosts, and only one active coordinator
timer set. A successful SSH login or an active timer is not sufficient.

On sleeper-service, verify the configured read-only transport and compare its
ledger identity with the coordinator's previously accepted identity:

```sh
python3 - <<'PY'
import json, shlex, sqlite3, subprocess

with open('/etc/pr-maintenance/coordinator.json') as stream:
    config = json.load(stream)
command = shlex.join([config['worker_executable'], '--config', config['worker_config'], 'transport'])
result = subprocess.run([
    'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=10', '-o', 'UserKnownHostsFile=' + config['known_hosts'],
    '--', config['worker_user'] + '@' + config['worker_host'], command,
], input='{"operation":"collect"}', text=True, capture_output=True, timeout=30)
assert result.returncode == 0, 'Worker transport failed; keep dispatch stopped'
response = json.loads(result.stdout)
database = sqlite3.connect('file:' + config['database'] + '?mode=ro', uri=True)
accepted = database.execute("SELECT value FROM meta WHERE key='worker_ledger_id'").fetchone()[0]
assert response['ledger_id'] == accepted, 'Ledger identity changed; keep dispatch stopped'
print('Transport verified; accepted ledger identity matches; attempts:', len(response['attempts']))
PY
```

Compare these outputs from the two hosts. Every module hash must match:

```sh
sha256sum ~/.local/share/pr-maintenance/nix/package/lib/pr-maintenance/pr_maintenance/*.py
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes sleeper-service 'sha256sum ~/.local/share/pr-maintenance/nix/package/lib/pr-maintenance/pr_maintenance/*.py'
```

On sleeper-service, `systemctl list-timers 'pr-maintenance*'` must show the system
coordinator timers. `systemctl --user list-timers 'pr-maintenance*'` must not show
active bootstrap coordinator timers. On contents-may-differ,
`systemctl --user show pr-maintenance-reconcile.service -p ExecStart -p FragmentPath`
must select the Nix-built worker wrapper and generated unit. Keep dispatch stopped
if any comparison fails. Backups retain the prior unit definitions and ledgers.

### Replace the targeted selections during a whole-host switch

1. Stop coordinator timers and ticks. Pause worker reconciliation, inspect active
   attempt units, and take consistent ledger, configuration, and unit backups.
   Do not restart the shared OpenCode server or delete retained attempts.
2. Build and inspect the intended full host generations. Keep dispatch stopped
   while replacing the targeted selections.
3. On sleeper-service, remove only the five backed-up `pr-maintenance*` unit links
   in `/etc/systemd/system.control/` and their three backed-up target-wants links.
   Preserve unrelated `.control` files. Remove the backed-up targeted
   `/etc/pr-maintenance/coordinator.json` link before the generation installs its
   own configured leaf.
4. On contents-may-differ, remove only the backed-up `pr-maintenance@.service`,
   `pr-maintenance-reconcile.service`, and `pr-maintenance-reconcile.timer` links
   from `~/.config/systemd/user/`. Remove the backed-up targeted worker JSON link
   before the generation installs its configured leaf. Preserve timer enablement
   and all private state.
5. Activate the intended generations and reload the managers. Advance the stable
   `~/.local/share/pr-maintenance/nix/worker` GC root to the newly built worker
   wrapper, or explicitly configure the coordinator to use the now-installed
   `/run/current-system/sw/bin/pr-worker`. Do not silently leave SSH on an older
   package while worker units select the new one.
6. Repeat exact transport, accepted-ledger identity, module-hash, and single-
   scheduler checks before starting timers. Confirm `FragmentPath`, `DropInPaths`,
   and `ExecStart` select the intended generation, not a stale targeted override.

## Run a supervised smoke test

Use this narrower scope to test real model stages and private delivery before
enabling the automatic worker.

1. Keep worker execution, periodic timers, public webhooks, and organization-wide
   operation disabled. Do not fabricate a process-stop probe or mark the runner
   contract verified.
2. Prepare a private disposable repository and PR. Pin its numeric identities and
   use the explicitly acknowledged personal-auth mode if no App is provisioned.
3. Use the usual OpenCode instance and explicit stage/review models. Record one
   session before each prompt. Run only one stage at a time.
4. Inspect the session, tools, inbox, children, and known running shells after its
   turn. Stop on interruption, known background work, or an ambiguous result.
   These observations do not prove arbitrary detached children have stopped.
5. Verify the exact local candidate and obtain a separate read-only review before
   an explicit normal fast-forward push. Inspect again before the next stage.
   Preserve the real 15-minute delay before the feedback snapshot.
6. Reconcile private reports and compare discussion snapshots. Record automatic
   dispatch, process-stop, cancellation, and restart recovery as **not tested**.

This smoke test does not establish that unattended execution is ready. The
following rollout checks still apply before enabling that mode.

## Approve a disposable pilot

Do not enable unattended execution until you complete these checks.

1. Obtain organization approval for a GitHub App with Metadata, Contents, and Pull
   requests read access. Select a disposable repository and PR. Do not test
   permissions by attempting to create comments.
2. Provision the App private key and webhook secret outside the Nix store. Install
   the App on the approved repositories. Keep the App key and bot token on
   sleeper-service.
3. Provision the worker's SSH host key through a trusted channel. The coordinator
   requires `BatchMode=yes`, `StrictHostKeyChecking=yes`, and an explicit
   known-hosts file. Do not use automatic host-key acceptance.
4. Select an explicit model and inspected agent definition for each coding stage
   and the internal read-only review. Inspect effective shell, MCP, plugin,
   credential, and repository-instruction access. Confirm that the stage can
   select and run the repository's normal checks.
5. Identify the existing OpenCode server that owns your usual history. Configure
   its explicit URL and authentication on WSL. A direct server and the managed
   service can use different ports and credentials. Do not launch another server.
6. Verify the installed runner version, session creation, working directory,
   visible session history, tools, descendants, and interruption on the disposable
   PR. Interruption acknowledgement alone does not establish quiescence.
7. Acknowledge that the normal developer account exposes ambient credentials.
   Instructions and publication checks do not prevent every discussion write or
   arbitrary process. Record the telemetry gaps and the accepted exposure.
8. Enable the worker and coordinator only for the explicit pilot allowlist.
   Keep organization-wide operation and the public webhook disabled initially.

The Nix options are `services.prMaintenance` on sleeper-service and
`services.prMaintenanceWorker` on WSL. Their default is disabled. Setting an option
does not provision credentials or establish that the pilot passed. Evaluate or
build the intended host before a separately authorized deployment. Never switch a
different host as part of a local test.

## Inspect a delayed or blocked workflow

1. Run `pr-maintenance --config /etc/pr-maintenance/coordinator.json status` on
   sleeper-service. Record the PR ID, stage, attempt ID, and paused reason.
2. Inspect the coordinator journal and the worker's corresponding user unit.
   Keep logs private. Do not copy raw diffs, review bodies, or credentials into
   Telegram or public issues.
3. Open the recorded session in the configured OpenCode instance. Inspect its
   children, pending inputs, tools, and background processes as well as the
   supervising CLI.
4. Inspect the retained automation checkout and candidate commit. Compare the
   remote head with the recorded input and candidate. A candidate reachable from
   the remote head is published even if the push acknowledgement was lost.
5. For missing dependencies or inadequate verification, complete the reported
   setup as for an ordinary OpenCode task. Maintenance adds no restrictions on
   dependency preparation or tool use. At handoff, `result.json` supplies the
   verification commands when none are configured. The worker reruns those checks
   and reviews the committed candidate before publication.
6. After execution has stopped and publication is known, resume or explicitly
   retry the selected stage with an audit reason. Retained unpublished work is not
   silently replayed on a different remote head.

For a publication incident, `resume` also queues a fresh-epoch acknowledgement to
the worker. Confirm delivery in `status`; a local resume alone does not release
the worker's branch claim. The incident remains recorded after acknowledgement.

Publication expiry uses a fresh timestamp from the worker's exact attempt status,
not the coordinator's clock. Before deploying, confirm that coordinator
`publish_ttl` does not exceed worker `authorization_max_seconds` (both default to
60 seconds). Transport and scheduling consume this window. An expired grant
requires a fresh handshake for the retained candidate, not another coding attempt
or a larger lifetime to compensate for clock skew.

The worker never switches or cleans a developer checkout. Do not delete failed
attempt directories to release ownership. No automatic retry is safe while an
earlier attempt might still edit or publish.

## Release inspected best-effort ownership

Independent PRs execute concurrently without a global queue or worker-slot limit.
An interrupted attempt retains ownership of its PR and head branch, not unrelated
PRs. Inspect `status.attempts` for execution and publication status. A second PR
on the same head branch pauses with `waiting_same_branch`; it requires an explicit
handoff after ownership is resolved. A completed coordinator tick does not mean
coding ran. GitHub throttling, inventory failures, worker outages, and ledger
integrity holds can still delay dispatch across PRs.

Use this recovery only when accepting best-effort observations is appropriate.
The command cannot prove arbitrary detached processes stopped. It refuses active
or ambiguous unit state, active registered work, a live recorded verification
process group, and unknown or published candidate outcomes.

1. Stop the coordinator timer on sleeper-service and wait for its current tick to
   finish. Stop the worker reconciliation timer and wait for reconciliation to
   finish. Do not stop the shared OpenCode server.
2. Back up both ledgers using SQLite's backup API. Preserve the owned checkout,
   candidate, sessions, and logs.
3. On sleeper-service, run the coordinator's `cancel PR_ID --reason '...'` command
   for the held workflow. This revokes future grants. Cancellation remains pending
   until worker ownership is resolved.
4. Inspect both recorded sessions, their inboxes and child sessions, registered
   shells and terminals, the worker unit, and the recorded verification process
   group. Inspect the retained candidate and remote branch.
5. On contents-may-differ, run:

   ```sh
   ~/.local/share/pr-maintenance/nix/worker/bin/pr-worker \
     --config /etc/pr-maintenance/worker.json resolve-interrupted ATTEMPT_ID \
     --accept-best-effort --reason 'Inspected retained work; accept detached-process uncertainty'
   ```

   Expect `state: blocked`, `quiescent: true`, `publication_state: not_published`,
   and an `operator_recovery` record. This command never starts a prompt or pushes.
   Contention refuses the command. Retry after the competing operation finishes.
6. Reconcile the worker ledger on the coordinator before allowing new dispatch.
   Confirm that the cancelled workflow finishes and its unresolved claim clears.
   A lost acknowledgement is safe: repeated worker recovery returns the same
   recorded decision. Do not clear a coordinator claim or bypass a recovery hold
   to compensate for an unavailable worker.
7. Restore the previously active timers. Confirm the next eligible workflow can
   start. Do not resume or re-enroll merged or out-of-scope work.

### Recover an inspected setup failure without a session

The worker's private status includes `execution_phase`. Pure local setup rejection
is terminal. In acknowledged best-effort mode, a positively identified PR-lifecycle
or SHA rejection before checkout is also terminal when read-only setup completed
normally. This assumes trusted setup tools do not intentionally detach work.
Timeouts, failed tools, checkout effects, and interrupted prompts remain held.
The one-way execution marker never permits an automatic prompt retry.

For a historical held attempt with no recorded session, inspect its original unit
journal, retained checkout, artifacts, and host processes. Follow the same timer
freeze and ledger-backup steps above. If you accept the missing session observations
as well as detached-process uncertainty, add `--accept-sessionless`:

```sh
~/.local/share/pr-maintenance/nix/worker/bin/pr-worker \
  --config /etc/pr-maintenance/worker.json resolve-interrupted ATTEMPT_ID \
  --accept-best-effort --accept-sessionless \
  --reason 'Inspected setup failure and host processes; accept missing session observations'
```

This option requires absent sessions, prompt evidence, candidates, verification
evidence, and post-setup execution phases. Retained execution artifacts also refuse
the command. A feedback snapshot alone is not execution evidence. Stopped-unit,
recorded process-group, ownership-lock, and evidence-change checks still apply.
The command never starts a prompt, retries a stage, or publishes. Its audit records
`accepted_sessionless: true`, `session_observations: unavailable`, and
`execution_phase: legacy_unknown` when the old record has no phase. These are
accepted uncertainty, not proof of non-execution. Normal recovery still requires
all recorded sessions to be observed quiet and owned by the attempt.

### Recover an unpublished candidate after its PR branch was deleted

Normal recovery refuses a missing head branch. If you have inspected a closed PR
whose branch was deleted, add `--accept-deleted-branch` to the normal
`resolve-interrupted --accept-best-effort --reason ...` command. The separate
acknowledgement does not relax session ownership or quiet checks.

The worker requires the original PR identity and refs, a final PR head equal to
the attempt's input, and a successful lookup confirming the original branch is
absent. It fetches GitHub's retained PR head and the current base into separate
local recovery refs. Both must match fresh API evidence, and the candidate must
be unreachable from either. Closed PR `base.sha` is historical, so the worker
reads the live base ref separately. A second lookup must still find no head branch.
Failures, moved heads, reachable candidates, and unknown publication stay held.

The audit records `accepted_deleted_branch: true` and the observed publication
evidence. These are point-in-time observations of the original PR head and current
base, not proof that candidate bytes never appeared elsewhere. The command does
not recreate remote branches, push candidates, change pinned input/base refs, or
start replacement work. Retain the candidate and reconcile both ledgers normally.

## Confirm the pilot result

Historical model no-ops can include reported blockers. Inspect their retained
`result.json`, verification evidence, and unresolved findings before treating them
as successful. Status marks model completions without explicit outcome evidence
as `completion_outcome: unverified`; old ledgers and notifications are not rewritten.
Health does not establish that historical no-ops succeeded.

New model results require `outcome: changed`, `no_change`, or `blocked`. Blocked
results require structured blockers with a `reason` and `next_action`. Missing,
malformed, contradictory, or blocked results cannot become no-ops. An explicit
`no_change` also requires a clean checkout at the original head. Changed candidates
still require independent verification and review. The no-model Git conflict probe
can independently establish that no merge is needed.

Later stages continue after a terminal blocked or failed stage. A finished pass
with such stages is reported as finished with blockers, and status exposes
`completion_outcome: with_blockers` rather than `success`. Other PRs continue
independently. A paused `waiting_same_branch` workflow still requires an explicit,
audited `resume` after the old owner has been reconciled.

New execution snapshots resolve the current base branch through the Git reference
API. The PR payload's base SHA can be historical even on an open PR. Both coordinator
and worker use the exact current base ref; failed ref reads never fall back to the
old PR snapshot. Closed-PR recovery retains its separate historical-head checks.

1. Check both the initial and final private summaries. Confirm no-ops, exact
   verification results, deleted paths, commits, feedback dispositions, and
   retained work against the repository and sessions.
2. Disconnect WSL before dispatch and reconnect it after the feedback due time.
   Confirm the same persisted attempt resumes coordination without duplicate
   model invocation or publication.
3. Interrupt transport after a push and before acknowledgement. Confirm recovery
   uses remote reachability instead of another commit or prompt.
4. Exercise draft pause, close, retarget, head movement, and a deliberately
   ambiguous final merge. Confirm uncertain work stays blocked and preserved.
5. Compare before-and-after conversation comments, reviews, inline comments, and
   relevant timeline events. Inspect recorded agent tools and publisher requests.
   Attribute unrelated bots separately and report ambiguous use of the shared
   personal identity. This is best-effort telemetry, not complete network capture.
6. Measure the GitHub request budget before expanding repository coverage. Keep
   recovery scans enabled after signed webhook delivery is enabled.

Feedback runs once, after the initial stages and the 15-minute due time. Its
snapshot includes conversation comments, submitted reviews, and inline comments
with replies. Later feedback does not start another pass. The final conflict
stage merges the base only when an actual Git merge conflicts. A clean but behind
branch does not receive an unnecessary synchronization commit.

If the base advances before final publication, the coordinator can recompute in a
fresh attempt within `correction_rounds` (two total attempts by default). It first
requires quiescence, known nonpublication, unchanged head identity, and the same
input head. It retains the previous attempt and candidate. Head advancement never
uses this automatic retry path. Review findings and other interrupted attempts
require explicit inspection or retry rather than an automatic repair loop.

## Distinguish checkout routing from missing dependencies

Each maintenance attempt has its own checkout. An OpenCode project may show the
development checkout as its canonical directory while the session uses a separate
attempt directory. Inspect the session's `location.directory`, the attempt's
reported checkout, `git remote get-url origin`, and `git rev-parse HEAD` together.
Project membership does not share node_modules or other ignored files.

The worker's optional `dependency_sources` JSON setting maps an exact head
repository full name to an absolute development checkout hint:

```json
{
  "dependency_sources": {
    "EigenformAI/geocluster-native-ui": "/home/elijah/geocluster-fe-restructure"
  }
}
```

The hint is optional context, not an approved-source requirement. The agent chooses
setup as for an ordinary OpenCode task. Record the result and verification in the
private `result.json`. Changing this setting does not replay existing attempts.

New Telegram reports show short stage outcomes, blockers, verification status,
decisions, and useful PR, published-commit, or review links. Session IDs, attempt
IDs, checkout paths, exact commands, and log hashes remain in private status and
artifacts. Single-message reports have no delivery footer. Multipart reports repeat
the heading with `Part N/M`. Existing outbox parts remain immutable and retain
their original format. `Verification: not recorded` does not claim checks passed,
even when the stage reports no changes.

The compact template and concise stage-summary instructions are deployed on both
hosts. The rollout preserved active worker processes and restored the existing
timers. Previous releases and consistent pre-deployment ledger backups remain in
the release directories. Future stages use the new prompt; running stages are not
replayed to rewrite their summaries.

## Back up and restore state

1. Pause dispatch and revoke new publication grants. Wait for execution and any
   in-flight push to reconcile. If the worker is unreachable, keep the hold.
2. Back up both SQLite databases with SQLite's backup API or a consistent stopped
   copy. Copy each database with its WAL when you cannot guarantee a checkpoint.
   Preserve owned checkouts, candidate commits, and private verification artifacts.
3. Restrict backup access to the service owner. Apply an explicit retention policy.
   Do not delete normal OpenCode history as routine cleanup.
4. After restoring, compare worker ledger identity and every known attempt before
   authorizing new work. Unknown worker attempts, missing accepted attempts, or a
   same-ID/different-spec collision require an operator reconciliation decision.
5. Leave a recovery hold in place until the ledgers, sessions, processes, and
   remote branch state agree. There is no automatic command to erase this hold.
   Back up the conflicting records before an audited repair.

## Inspect a filtered notification

1. Read the notification's `Host`, `File`, and `Line` fields. Connect to that
   host as the service account and inspect that line in the named private file.
   The line belongs to a retained notification, not necessarily a repository file.
2. Check whether the match is a real credential. The outbound filter is a pattern
   check, not a repository secret scanner. For a real credential, revoke or rotate
   it at its provider and remove it from the original source.
3. If retention failed, repair free-space, directory-permission, or retained-file
   UTF-8 encoding problems. A later coordinator tick can retain and report the text.
   Do not rerun coding to recover a notification.
4. Treat `withheld-reports/` beside the configured coordinator database as sensitive
   private state. Files contain the first rejected notification per report identity
   and may contain real credentials. Keep their owner-only permissions and include
   them in the private backup and retention policy. Never publish their contents.

A generated internal report key can resemble a token. New notices hash that
key and deliver the safe body without reporting a credential leak. Worker
result failures name their existing private `result.json` artifact instead.
Historical generic warnings remain immutable and do not acquire retained files.

## Rotate credentials and recover reports

1. Pause dispatch before replacing App, webhook, SSH, or OpenCode credentials.
2. Update only the host-local secret source. Keep secret contents out of Nix
   settings, command arguments, URLs, Git, and operator audit reasons.
3. Validate read access, strict SSH identity, the selected OpenCode server, and the
   private bot destination before resuming. The destination must be the configured
   allowlisted private owner, not a group.
4. Inspect pending report age with `status`. The coordinator retries its durable
   outbox independently of coding. A send timeout can produce a labeled duplicate.
   Never rerun coding or fall back to a PR comment because Telegram failed.

The independent health timer distinguishes completed ticks from operational
degradation. Worker transport failures, held attempts, recovery holds, rejected
publication authorization, stale ticks, and stale pending reports produce an
unhealthy result. `publication_rejected` means an active candidate's handshake
failed; inspect its private diagnostic. Other PRs and reports continue, and a
successful handshake clears that warning. A normal running attempt
does not. Health does not wait for the tick's operator lock. Release the operator
lock before network delivery. A separate nonblocking delivery lock prevents
overlapping senders without holding up coordination.
If Telegram is also unavailable, use the local journal and pending outbox. No
delivery channel can report its own total outage reliably.

Transport failures and unexpected coordinator exceptions retain private JSON
diagnostics in `diagnostics/` beside the coordinator database. The journal names
the category and file, not raw stderr or a traceback. Inspect the file as the
service account. Files are owner-only and may contain credentials emitted by
external tools. Never paste their contents into Telegram or public issues.
Each private text field is capped at 64 KiB, each record at 256 KiB, and generated
history at the newest 200 files. Truncation flags identify incomplete evidence.
If retention fails, the diagnostic says `not retained`; inspect permissions and
free space rather than assume a file exists.
