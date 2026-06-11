from personal_telegram_bot.providers.health import (
    CheckResult,
    diff_transitions,
    http_status_ok,
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
