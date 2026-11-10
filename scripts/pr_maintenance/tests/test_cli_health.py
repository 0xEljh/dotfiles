import json
import select
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from pr_maintenance import cli
from pr_maintenance.common import STAGES
from pr_maintenance.config import load
from pr_maintenance.coordinator import Coordinator
from pr_maintenance.reporting import Outbox
from test_coordinator import pr


@pytest.fixture
def ledger(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "database": str(tmp_path / "coordinator.sqlite3"),
        "execution_enabled": True,
        "rollout_acknowledged": True,
        "allow_repositories": ["EigenformAI/test"],
        "models": {stage: "provider/model" for stage in (*STAGES, "review")},
        "agents": {stage: stage for stage in (*STAGES, "review")},
        "report_command": ["not-used"],
    }))
    config = load(config_path)
    at = time.time()
    coordinator = Coordinator(config["database"], config, now=lambda: at)
    coordinator.enable(500)
    coordinator.db.execute("INSERT INTO meta VALUES ('last_tick', ?)", (str(at),))
    outbox = Outbox(coordinator.db, now=lambda: at)
    try:
        yield config_path, coordinator, outbox, at
    finally:
        coordinator.db.close()


def health(config_path, monkeypatch, capsys):
    delivered = []
    monkeypatch.setattr(cli, "_sender", lambda config: lambda text: delivered.append(text) or 1)
    result = cli.main(["--config", str(config_path), "health"])
    output = capsys.readouterr()
    assert output.err == ""
    return result, json.loads(output.out), delivered


def add_attempt(coordinator, state="running", pr_id=100):
    coordinator.observe(pr(pr_id, ref=f"feature-{pr_id}", number=pr_id), 1000)
    spec = coordinator.claim(pr_id)
    coordinator.accept_status(spec["attempt_id"], {
        "state": state, "quiescent": False, "publication_state": "not_published",
        "error": "private raw reason and diff ghp_" + "x" * 30,
    })
    return spec["attempt_id"]


def test_live_worker_outage_is_not_healthy_and_notice_is_safe_hourly(ledger, monkeypatch, capsys):
    config_path, coordinator, _, at = ledger
    for pr_id in range(100, 108):
        add_attempt(coordinator, "cancel_requested", pr_id)
    coordinator.db.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("worker_connect_failures", "106"), ("worker_retry_at", str(at + 900)),
        ("recovery_hold", "private recovery details ghp_" + "x" * 30),
    ])
    coordinator.db.execute("UPDATE attempts SET last_contact=?", (at - 86400,))
    monkeypatch.setattr(cli.time, "time", lambda: at)
    for _ in range(2):
        code, result, delivered = health(config_path, monkeypatch, capsys)
        assert code == 1
        assert result["healthy"] is False
        assert result["live"] is True
        assert result["ready"] is False
        assert set(result["issue_codes"]) == {"worker_transport_failures", "recovery_hold", "cancel_requested"}
        assert result["worker"]["transport_failures"] == 106
        assert result["worker"]["retry_at"] == at + 900
        assert result["worker"]["last_contact"] == at - 86400
        assert result["worker"]["contact_age"] == 86400
        assert result["held_attempts"] == 8
        assert result["recovery_hold"] is True
        assert "private" not in json.dumps(result)
        assert all("ghp_" not in text and "raw reason" not in text for text in delivered)
    reports = coordinator.db.execute("SELECT text FROM reports").fetchall()
    assert len(reports) == 1
    assert "worker_transport_failures" in reports[0][0]
    assert "no replacement" in reports[0][0].lower()


@pytest.mark.parametrize("state", ["inspection_required", "deadline_exceeded", "publication_unknown", "cancel_requested"])
def test_unresolved_attempt_states_degrade_readiness(ledger, monkeypatch, capsys, state):
    config_path, coordinator, _, _ = ledger
    add_attempt(coordinator, state)
    code, result, _ = health(config_path, monkeypatch, capsys)
    assert code == 1
    assert result["live"] is True and result["ready"] is False
    assert result["issue_codes"] == [state]
    assert result["attempt_issues"] == {state: 1}
    assert result["held_attempts"] == 1


@pytest.mark.parametrize("hold", ["deadline_exceeded", "cancel_requested"])
def test_coordinator_side_hold_is_not_hidden_by_running_worker_state(ledger, monkeypatch, capsys, hold):
    config_path, coordinator, _, _ = ledger
    add_attempt(coordinator)
    if hold == "deadline_exceeded":
        coordinator.db.execute("UPDATE stages SET state=? WHERE active_attempt_id IS NOT NULL", (hold,))
    else:
        coordinator.db.execute("UPDATE workflows SET lifecycle_state=?", (hold,))
    code, result, _ = health(config_path, monkeypatch, capsys)
    assert code == 1
    assert result["issue_codes"] == [hold]
    assert result["held_attempts"] == 1


@pytest.mark.parametrize("execution_enabled", [False, True])
def test_disabled_execution_or_ordinary_running_work_is_healthy(ledger, monkeypatch, capsys, execution_enabled):
    config_path, coordinator, _, at = ledger
    supplied = json.loads(config_path.read_text())
    supplied["execution_enabled"] = execution_enabled
    config_path.write_text(json.dumps(supplied))
    if execution_enabled:
        add_attempt(coordinator)
    else:
        coordinator.db.execute("INSERT INTO meta VALUES ('worker_connect_failures', '106')")
    before = coordinator.db.execute("SELECT MAX(last_contact) FROM attempts").fetchone()[0]
    code, result, delivered = health(config_path, monkeypatch, capsys)
    assert code == 0
    assert result["healthy"] is True and result["live"] is True and result["ready"] is True
    assert result["issue_codes"] == [] and result["held_attempts"] == 0
    assert result["execution_enabled"] is execution_enabled
    assert result["worker"]["last_contact"] == before
    assert coordinator.meta("last_tick") == str(at)
    assert coordinator.db.execute("SELECT MAX(last_contact) FROM attempts").fetchone()[0] == before
    assert delivered == []


def test_stale_tick_is_liveness_failure_not_invented_worker_failure(ledger, monkeypatch, capsys):
    config_path, coordinator, _, at = ledger
    coordinator.db.execute("UPDATE meta SET value=? WHERE key='last_tick'", (str(at - 1800),))
    code, result, delivered = health(config_path, monkeypatch, capsys)
    assert code == 1 and result["healthy"] is False
    assert result["live"] is False and result["ready"] is True
    assert result["issue_codes"] == ["stale_tick"]
    assert result["worker"]["last_contact"] is None
    assert len(delivered) == 1


def test_stale_pending_report_degrades_snapshot_even_if_delivery_then_succeeds(ledger, monkeypatch, capsys):
    config_path, coordinator, outbox, at = ledger
    outbox.enqueue("previous-report", None, "An existing private report")
    coordinator.db.execute("UPDATE reports SET created_at=?", (at - 1800,))
    code, result, delivered = health(config_path, monkeypatch, capsys)
    assert code == 1 and result["live"] is True and result["ready"] is False
    assert result["issue_codes"] == ["stale_pending_reports"]
    assert result["reports"]["oldest_pending_age"] >= 1800
    assert len(delivered) == 2
    assert outbox.status()["pending_reports"] == 0


@pytest.mark.parametrize("degraded", [False, True])
def test_health_returns_while_another_process_holds_tick_lock_without_sending(ledger, tmp_path, degraded):
    config_path, coordinator, outbox, _ = ledger
    if degraded:
        coordinator.db.execute("INSERT INTO meta VALUES ('worker_connect_failures', '106')")
    outbox.enqueue("already-pending", None, "Retained report")
    lock = Path(coordinator.path).with_suffix(".operator.lock")
    marker = tmp_path / "sender-created"
    holder = subprocess.Popen([
        sys.executable, "-c",
        "import fcntl, sys; stream=open(sys.argv[1], 'a'); "
        "fcntl.flock(stream, fcntl.LOCK_EX); print('locked', flush=True); sys.stdin.read()",
        str(lock),
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        started = time.monotonic()
        try:
            completed = subprocess.run([
                sys.executable, "-c",
                "import sys; from pathlib import Path; from pr_maintenance import cli; "
                "cli._sender=lambda config: Path(sys.argv[2]).write_text('unexpected sender'); "
                "raise SystemExit(cli.main(['--config', sys.argv[1], 'health']))",
                str(config_path), str(marker),
            ], capture_output=True, text=True, timeout=2,
                cwd=Path(__file__).resolve().parents[1])
        except subprocess.TimeoutExpired:
            pytest.fail("health waited more than two seconds for the tick operator lock")
        elapsed = time.monotonic() - started
        assert elapsed < 2
        assert completed.stderr == ""
        assert completed.returncode == int(degraded)
        result = json.loads(completed.stdout)
        assert result["healthy"] is not degraded
        assert result["report_delivery"] == "skipped_operator_busy"
        assert not marker.exists()
        assert outbox.status()["pending_reports"] == 1
        assert coordinator.db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    finally:
        holder.communicate(timeout=2)


@pytest.mark.parametrize("phase", ["validation", "delivery"])
@pytest.mark.parametrize("tick_fails", [False, True])
def test_slow_health_sender_does_not_block_tick_or_allow_a_second_sender(ledger, tmp_path, phase, tick_fails):
    config_path, coordinator, outbox, _ = ledger
    coordinator.db.execute("INSERT INTO meta VALUES ('worker_connect_failures', '106')")
    outbox.enqueue("already-pending", None, "Retained report")
    sent = tmp_path / "health-sent"
    duplicate = tmp_path / "duplicate-send"
    root = Path(__file__).resolve().parents[1]
    health_script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from pr_maintenance import cli

        def wait():
            print('in-flight', flush=True)
            sys.stdin.read()

        def sender(config):
            if sys.argv[3] == 'validation':
                wait()
            def send(text):
                if sys.argv[3] == 'delivery' and not Path(sys.argv[2]).exists():
                    wait()
                with Path(sys.argv[2]).open('a') as stream:
                    stream.write(text + '\\n')
                return 1
            return send

        cli._sender = sender
        raise SystemExit(cli.main(['--config', sys.argv[1], 'health']))
    """)
    tick_script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from types import SimpleNamespace
        from pr_maintenance import cli, github, service, transport

        def send(text):
            Path(sys.argv[2]).write_text(text)
            return 99

        class Tick:
            def __init__(self, coordinator, github, worker, outbox):
                self.outbox = outbox
            def tick(self):
                if sys.argv[3] == 'True':
                    raise RuntimeError('private tick failure')
            def notice(self, *args):
                self.outbox.enqueue(*args)

        cli._sender = lambda config: send
        github.GitHub = lambda config: SimpleNamespace(retry_at=0)
        transport.SSHWorker = lambda config: None
        service.Service = Tick
        raise SystemExit(cli.main(['--config', sys.argv[1], 'tick']))
    """)
    health_process = subprocess.Popen([
        sys.executable, "-c", health_script, str(config_path), str(sent), phase,
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=root)
    try:
        assert select.select([health_process.stdout], [], [], 2)[0], "health sender did not start"
        assert health_process.stdout.readline().strip() == "in-flight"
        # Model a send that has outlived the outbox's retry backoff, without sleeping.
        coordinator.db.execute("UPDATE report_parts SET next_attempt=0 WHERE message_id IS NULL")
        started = time.monotonic()
        try:
            tick = subprocess.run([
                sys.executable, "-c", tick_script, str(config_path), str(duplicate), str(tick_fails),
            ], capture_output=True, text=True, timeout=2, cwd=root)
        except subprocess.TimeoutExpired:
            pytest.fail("slow health sender prevented tick from acquiring the operator lock")
        assert time.monotonic() - started < 2
        assert tick.returncode == int(tick_fails), tick.stderr
        assert "private tick failure" not in tick.stderr
        if not tick_fails:
            assert json.loads(tick.stdout)["tick"] == "complete"
        assert not duplicate.exists(), "tick ran a second sender while health was in flight"
        second_health = subprocess.run([
            sys.executable, "-c",
            "import sys; from pathlib import Path; from pr_maintenance import cli; "
            "cli._sender=lambda config: Path(sys.argv[2]).write_text('unexpected validation'); "
            "raise SystemExit(cli.main(['--config', sys.argv[1], 'health']))",
            str(config_path), str(duplicate),
        ], capture_output=True, text=True, timeout=2, cwd=root)
        assert second_health.returncode == 1, second_health.stderr
        assert json.loads(second_health.stdout)["report_delivery"] == "skipped_delivery_busy"
        assert not duplicate.exists(), "another health command bypassed the delivery lock"
    finally:
        output, errors = health_process.communicate(timeout=2)
    assert health_process.returncode == 1, errors
    assert json.loads(output)["report_delivery"] == "attempted"
    assert sent.read_text().count("Retained report") == 1


def test_unexpected_cli_error_retains_private_traceback_and_only_prints_reference(ledger, monkeypatch, capsys, caplog):
    config_path, _, _, _ = ledger
    secret = "ghp_" + "x" * 30

    def fail(*args, **kwargs):
        raise RuntimeError("private upstream payload " + secret)

    monkeypatch.setattr(cli, "Coordinator", fail)
    assert cli.main(["--config", str(config_path), "status"]) == 1
    output = capsys.readouterr()
    artifacts = [path for path in config_path.parent.rglob("*.json") if path != config_path]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    retained = artifact.read_text()
    json.loads(retained)
    assert "Traceback" in retained and "RuntimeError" in retained and secret in retained
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700
    assert artifact.name in output.err
    assert secret not in output.err and "private upstream payload" not in output.err
    assert "Traceback" not in output.err and output.out == ""
    assert secret not in caplog.text and "Traceback" not in caplog.text


def test_cli_diagnostic_storage_failure_does_not_claim_artifact(ledger, monkeypatch, capsys):
    config_path, _, _, _ = ledger

    def fail(*args, **kwargs):
        raise RuntimeError("private error")

    monkeypatch.setattr(cli, "Coordinator", fail)
    monkeypatch.setattr(cli, "retain_diagnostic", lambda *args, **kwargs: None, raising=False)
    assert cli.main(["--config", str(config_path), "status"]) == 1
    output = capsys.readouterr()
    assert "diagnostic storage failed" in output.err
    assert "private error" not in output.err and "reference:" not in output.err


@pytest.mark.parametrize("contents,expected_code", [("{", 2), (None, 1)])
def test_config_load_failure_does_not_invent_private_artifact(tmp_path, capsys, contents, expected_code):
    config_path = tmp_path / "broken.json"
    if contents is not None:
        config_path.write_text(contents)
    assert cli.main(["--config", str(config_path), "status"]) == expected_code
    output = capsys.readouterr()
    assert "diagnostic unavailable" in output.err
    assert "configuration not loaded" in output.err
    assert "reference:" not in output.err and "Traceback" not in output.err
    assert not list(tmp_path.glob("**/diagnostics/*"))


def test_blocked_cli_error_keeps_existing_public_message(ledger, monkeypatch, capsys):
    from pr_maintenance.common import Blocked

    config_path, _, _, _ = ledger

    def fail(*args, **kwargs):
        raise Blocked("safe operator decision")

    monkeypatch.setattr(cli, "Coordinator", fail)
    assert cli.main(["--config", str(config_path), "status"]) == 2
    assert capsys.readouterr().err == "blocked: safe operator decision\n"
