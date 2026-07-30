from types import SimpleNamespace

from personal_telegram_bot.providers.health import (
    CheckResult,
    check_disk,
    diff_transitions,
    http_status_ok,
    run_all,
)


def ok(name):
    return CheckResult(name=name, ok=True, detail="active")


def fail(name):
    return CheckResult(name=name, ok=False, detail="failed")


def test_new_healthy_check_is_silent():
    assert diff_transitions({}, [ok("nginx.service")]) == []


def test_new_failing_check_notifies():
    transitions = diff_transitions({}, [fail("nginx.service")])
    assert len(transitions) == 1
    assert transitions[0].new == "fail"
    assert transitions[0].old is None


def test_ok_to_fail_notifies():
    transitions = diff_transitions({"nginx.service": "ok"}, [fail("nginx.service")])
    assert [(t.old, t.new) for t in transitions] == [("ok", "fail")]


def test_fail_to_ok_notifies_recovery():
    transitions = diff_transitions({"nginx.service": "fail"}, [ok("nginx.service")])
    assert [(t.old, t.new) for t in transitions] == [("fail", "ok")]


def test_stable_states_are_silent():
    previous = {"nginx.service": "ok", "kodo-api.service": "fail"}
    results = [ok("nginx.service"), fail("kodo-api.service")]
    assert diff_transitions(previous, results) == []


def test_http_status_classification():
    assert http_status_ok(200)
    assert http_status_ok(302)
    assert not http_status_ok(404)
    assert not http_status_ok(500)
    assert not http_status_ok(502)


def disk_stat(*, total=100, free=20, available=20):
    return SimpleNamespace(
        f_blocks=total,
        f_bfree=free,
        f_bavail=available,
        f_frsize=1,
    )


def test_disk_usage_is_healthy_at_warning_threshold(monkeypatch):
    monkeypatch.setattr("os.statvfs", lambda _path: disk_stat(free=20, available=20))

    result = check_disk("/", warning_percent=80, critical_percent=90)

    assert result.name == "disk /"
    assert result.ok
    assert result.status == "ok"
    assert result.detail.startswith("80.0% used")


def test_disk_usage_warns_above_warning_threshold(monkeypatch):
    monkeypatch.setattr("os.statvfs", lambda _path: disk_stat(free=19, available=19))

    result = check_disk("/", warning_percent=80, critical_percent=90)

    assert not result.ok
    assert result.status == "warning"
    assert result.detail.startswith("81.0% used")


def test_disk_usage_is_critical_above_critical_threshold(monkeypatch):
    monkeypatch.setattr("os.statvfs", lambda _path: disk_stat(free=9, available=9))

    result = check_disk("/", warning_percent=80, critical_percent=90)

    assert not result.ok
    assert result.status == "critical"
    assert result.detail.startswith("91.0% used")


def test_disk_usage_at_critical_threshold_remains_warning(monkeypatch):
    monkeypatch.setattr("os.statvfs", lambda _path: disk_stat(free=10, available=10))

    result = check_disk("/", warning_percent=80, critical_percent=90)

    assert result.status == "warning"


def test_disk_usage_uses_space_available_to_service_user(monkeypatch):
    monkeypatch.setattr(
        "os.statvfs", lambda _path: disk_stat(total=100, free=20, available=10)
    )

    result = check_disk("/", warning_percent=80, critical_percent=90)

    assert result.status == "warning"
    assert result.detail.startswith("88.9% used")


def test_disk_probe_failure_is_unhealthy(monkeypatch):
    def unavailable(_path):
        raise FileNotFoundError

    monkeypatch.setattr("os.statvfs", unavailable)

    result = check_disk("/missing")

    assert result.name == "disk /missing"
    assert result.status == "fail"
    assert result.detail == "FileNotFoundError"


def test_run_all_includes_configured_disk_checks(monkeypatch):
    monkeypatch.setattr(
        "personal_telegram_bot.providers.health.check_disk",
        lambda path, warning, critical: CheckResult(
            f"disk {path}", True, f"thresholds {warning}/{critical}"
        ),
    )

    results = run_all([], [], ["/"], 75, 95)

    assert results == [CheckResult("disk /", True, "thresholds 75/95")]


def test_disk_usage_escalation_and_recovery_are_transitions():
    warning = CheckResult("disk /", False, "81.0% used", severity="warning")
    critical = CheckResult("disk /", False, "91.0% used", severity="critical")

    escalation = diff_transitions({"disk /": "warning"}, [critical])
    recovery = diff_transitions({"disk /": "critical"}, [warning])

    assert [(t.old, t.new) for t in escalation] == [("warning", "critical")]
    assert [(t.old, t.new) for t in recovery] == [("critical", "warning")]
