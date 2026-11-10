from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from .common import Blocked, canonical, check_secrets
from .config import load
from .coordinator import Coordinator
from .diagnostics import retain_diagnostic
from .health import snapshot


def parser():
    result = argparse.ArgumentParser(description="Private PR maintenance coordinator (no deployment)")
    result.add_argument("--config", required=True)
    sub = result.add_subparsers(dest="command", required=True)
    for command in ("tick", "status", "health", "serve", "inventory"):
        sub.add_parser(command)
    for command in ("pause", "resume", "cancel", "enroll", "retry-stage"):
        child = sub.add_parser(command)
        child.add_argument("pr_id", type=int, help="immutable numeric GitHub pull request ID, not PR number")
        if command == "retry-stage":
            child.add_argument("stage", choices=("deep_modules", "cleanup", "feedback", "conflicts"))
        child.add_argument("--reason", required=True)
    return result


def _sender(config):
    from .reporting import BotSender

    command = config.get("report_command")
    if not isinstance(command, list) or not command or any(not isinstance(v, str) for v in command):
        raise Blocked("configure a fixed private bot report_command before running the service")
    return BotSender(command)


def _deliver(config, outbox, sender=None):
    # Outbox retry backoff can expire before a slow sender returns. Keep a
    # separate process lock across validation and delivery, not just the DB CAS.
    lock = Path(config["database"]).with_suffix(".delivery.lock")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        outbox.deliver(sender if sender is not None else _sender(config))
    return True


def _health(config, coordinator, outbox):
    at = time.time()
    result = snapshot(coordinator, outbox.status(), at=at)
    lock = Path(config["database"]).with_suffix(".operator.lock")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result["report_delivery"] = "skipped_operator_busy"
            print(canonical(result))
            return 0 if result["healthy"] else 1
        if not result["healthy"]:
            outbox.enqueue(f"health:{int(at // 3600)}", None,
                           "PR maintenance coordinator health is degraded. Issue codes: "
                           + ", ".join(result["issue_codes"])
                           + ". Inspect private coordinator state and diagnostics. Existing attempts and publication state "
                           "are retained; no replacement coding attempt was authorized.")
    # Never hold the operator lock over health-side validation or network work.
    delivered = _deliver(config, outbox)
    result["report_delivery"] = "attempted" if delivered else "skipped_delivery_busy"
    print(canonical(result))
    return 0 if result["healthy"] else 1


def _diagnostic_reference(config, command, error):
    if config is None:
        return "private diagnostic unavailable (configuration not loaded)"
    try:
        reference = retain_diagnostic(config["database"], "coordinator_exception", command=command,
                                      exception_type=type(error).__name__, traceback=traceback.format_exc())
    except Exception:
        reference = None
    if reference is None:
        return "private diagnostic storage failed"
    return f"private diagnostic reference: {reference.name}"


def main(argv=None):
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parser().parse_args(argv)
    config = None
    c = None
    try:
        config = load(args.config)
        if args.command == "health" and not Path(config["database"]).exists():
            raise Blocked("coordinator database is missing; inspect service startup")
        # Schema initialization may still need SQLite access, but health must not
        # inherit the normal 30-second write-contention wait before its snapshot.
        c = Coordinator(config["database"], config, connection_timeout=0.2 if args.command == "health" else 30)
        if args.command == "serve":
            from .webhook import serve

            # The webhook owns a connection per request, not the shared tick connection.
            def apply(delivery_id, event, payload, received_at):
                coordinator = Coordinator(config["database"], config, connection_timeout=2)
                try:
                    return coordinator.delivery(delivery_id, event, payload, received_at)
                finally:
                    coordinator.db.close()

            return serve(config, apply)
        from .reporting import Outbox

        outbox = Outbox(c.db)
        if args.command == "status":
            print(json.dumps(c.status() | {"reports": outbox.status()}, indent=2))
            return 0
        if args.command == "health":
            return _health(config, c, outbox)
        # Serialize complete tick/control operations, but not webhook acknowledgement.
        lock = Path(config["database"]).with_suffix(".operator.lock")
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            if args.command in {"pause", "resume", "cancel", "enroll", "retry-stage"}:
                check_secrets(args.reason)
                c.control(args.command, args.pr_id, getattr(args, "stage", None), args.reason)
                print(canonical({"pr_id": args.pr_id, "state": c.workflow(args.pr_id)["lifecycle_state"]}))
                return 0
            if args.command == "inventory":
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('inventory_required','1')")
                print("Full open-PR inventory requested; next tick applies the existing enablement watermark.")
                return 0
            from .github import GitHub
            from .service import Service
            from .transport import SSHWorker

            sender = _sender(config)  # Validates the private destination before dispatch.
            worker = SSHWorker(config) if config["execution_enabled"] else None
            github = GitHub(config | {"github_retry_at": float(c.meta("github_retry_at", "0"))})
            service = Service(c, github, worker, outbox)
            try:
                service.tick()
            except Exception:
                # External diagnostics may contain private URLs, payloads, or credentials.
                service.notice(f"tick-failed:{int(time.time() // 3600)}", None,
                               "PR maintenance coordinator tick failed. Inspect private diagnostics and GitHub integration access. Existing attempts and publication state are retained; inspect them before retrying.")
                _deliver(config, outbox, sender)
                raise
            finally:
                c.db.execute("INSERT OR REPLACE INTO meta VALUES ('github_retry_at',?)", (str(github.retry_at),))
            _deliver(config, outbox, sender)
            print(canonical({"tick": "complete", "execution_enabled": config["execution_enabled"], "reports": outbox.status()}))
            return 0
    except (Blocked, ValueError) as error:
        # Only our content-free operator diagnostics are printed.
        if isinstance(error, Blocked):
            print(f"blocked: {error}", file=sys.stderr)
        else:
            print("invalid configuration or external response; " + _diagnostic_reference(config, args.command, error), file=sys.stderr)
        return 2
    except Exception as error:
        print("coordinator failed; " + _diagnostic_reference(config, args.command, error), file=sys.stderr)
        return 1
    finally:
        if c is not None:
            c.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
