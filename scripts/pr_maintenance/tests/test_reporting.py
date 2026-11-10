import sqlite3
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pr_maintenance.common import Blocked, database
from pr_maintenance.reporting import (
    BotSender,
    Outbox,
    PART_LIMIT,
    PermanentDeliveryError,
    RateLimited,
    TransientDeliveryError,
    _split,
    render_summary,
)


def test_outbox_splits_without_loss_labels_parts_and_deduplicates():
    connection = sqlite3.connect(":memory:")
    outbox = Outbox(connection, now=lambda: 100)
    text = ("module change 😀\n" * 700) + "final exact evidence"
    assert outbox.enqueue("pr:42:final", 42, text) is True
    assert outbox.enqueue("pr:42:final", 42, "must not replace existing report") is False
    sender = Mock(side_effect=range(100, 200))

    assert outbox.deliver(sender) == sender.call_count
    messages = [call.args[0] for call in sender.call_args_list]
    assert len(messages) > 1
    assert all(len(message) <= 3499 for message in messages)
    assert "".join(message.rsplit("\n\n", 1)[0] for message in messages) == text
    assert all(message.endswith(f"module change 😀 · Part {i}/{len(messages)}")
                for i, message in enumerate(messages, 1))
    assert messages[0].startswith("module change 😀")
    assert outbox.deliver(sender) == 0
    assert outbox.status()["sent_reports"] == 1
    assert outbox.status()["pending_parts"] == 0


@pytest.mark.parametrize("boundary", ["\n\n", "\n", ""])
def test_split_prefers_paragraph_then_line_then_capacity_without_losing_whitespace(boundary):
    label = "title" if boundary else "…"
    capacity = PART_LIMIT - len(f"\n\n{label} · Part 1/1")
    first = "title" + boundary if boundary else "x" * capacity
    text = first + "y" * (capacity + 100) + "\n\n"
    parts = _split(text)
    bodies = [part.rsplit("\n\n", 1)[0] for part in parts]
    assert bodies[0] == first
    assert "".join(bodies) == text
    assert all(len(part) <= PART_LIMIT for part in parts)


def test_split_prefers_paragraph_even_when_a_later_line_fits():
    text = "First paragraph.\n\nLater line.\n" + "x" * PART_LIMIT
    assert _split(text)[0].rsplit("\n\n", 1)[0] == "First paragraph.\n\n"


@pytest.mark.parametrize("size", [1, 3450, 3500, 35000, 350000])
def test_split_counts_footer_and_part_number_digits_and_is_deterministic(size):
    text = ("😀\n\n exact whitespace \n" * size)[:size]
    parts = _split(text)
    assert _split(text) == parts
    assert "".join(part.rsplit("\n\n", 1)[0] if len(parts) > 1 else part for part in parts) == text
    assert all(len(part) <= PART_LIMIT for part in parts)
    if len(parts) > 1:
        assert all(part.endswith(f"\n\n😀 · Part {index}/{len(parts)}")
                   for index, part in enumerate(parts, 1))
    else:
        assert parts == [text]


def test_single_message_keeps_internal_identity_out_of_telegram_and_still_deduplicates():
    outbox = Outbox(sqlite3.connect(":memory:"))
    key = "initial:5148599604:f2f38d213d8d"
    text = "PR maintenance: no changes.\nhttps://github.com/EigenformAI/example/pull/42"
    assert outbox.enqueue(key, 42, text)
    assert not outbox.enqueue(key, 42, "replacement")
    sender = Mock(return_value=12)
    assert outbox.deliver(sender) == 1
    sender.assert_called_once_with(text)
    assert outbox.connection.execute("SELECT report_key FROM reports").fetchone()[0] == key


def test_existing_persisted_parts_remain_unchanged_and_sent_parts_are_not_resent(tmp_path):
    path = tmp_path / "old-outbox.sqlite3"
    connection = database(path)
    outbox = Outbox(connection, now=lambda: 100)
    outbox.enqueue("legacy", 42, "old report" * 1000)
    connection.execute("DELETE FROM report_parts")
    report_id = connection.execute("SELECT id FROM reports").fetchone()[0]
    old_parts = ["[legacy part 1/2]\nconfirmed old body", "[legacy part 2/2]\npending old body"]
    connection.executemany(
        "INSERT INTO report_parts (report_id,part,text,next_attempt,message_id) VALUES (?,?,?,?,?)",
        [(report_id, 1, old_parts[0], 100, 123), (report_id, 2, old_parts[1], 100, None)],
    )
    connection.commit()
    connection.close()
    connection = database(path)
    restarted = Outbox(connection, now=lambda: 100)
    assert not restarted.enqueue("legacy", 42, "new replacement must not be used")
    sender = Mock(return_value=456)
    assert restarted.deliver(sender) == 1
    sender.assert_called_once_with(old_parts[1])
    assert [row[0] for row in connection.execute("SELECT text FROM report_parts ORDER BY part")] == old_parts


def test_timeout_retry_survives_restart_keeps_label_and_only_retries_unsent(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    now = [100.0]
    connection = database(path)
    outbox = Outbox(connection, now=lambda: now[0])
    outbox.enqueue("initial:42", 42, "x" * 5000)
    sender = Mock(side_effect=[200, TimeoutError("private request body")])
    assert outbox.deliver(sender) == 1
    failed_text = sender.call_args.args[0]
    status = outbox.status()
    assert status["pending_parts"] == 1
    assert status["attempts"] == 2
    assert status["errors"] == 1
    assert "private request body" not in "\n".join(connection.iterdump())
    connection.close()

    now[0] = 101
    connection = database(path)
    restarted = Outbox(connection, now=lambda: now[0])
    sender = Mock(return_value=201)
    assert restarted.deliver(sender) == 0
    assert restarted.status()["oldest_pending_age"] == 1
    now[0] = 131
    assert restarted.deliver(sender) == 1
    sender.assert_called_once_with(failed_text)
    assert restarted.status()["sent_reports"] == 1
    assert restarted.status()["attempts"] == 3


def test_rate_limit_delay_and_permanent_failure_never_drop_report():
    now = [100.0]
    outbox = Outbox(sqlite3.connect(":memory:"), now=lambda: now[0])
    outbox.enqueue("delay:42", 42, "Worker offline; work queued")
    sender = Mock(side_effect=RateLimited(90))
    assert outbox.deliver(sender) == 0
    now[0] = 189
    assert outbox.deliver(sender) == 0
    now[0] = 190
    sender.side_effect = PermanentDeliveryError("destination rejected")
    assert outbox.deliver(sender) == 0
    assert outbox.status()["pending_reports"] == 1
    assert outbox.status()["errors"] == 1
    now[0] += 3601
    sender.side_effect = None
    sender.return_value = 222
    assert outbox.deliver(sender) == 1


def test_interrupted_send_is_ambiguous_and_retries_same_label(tmp_path):
    now = [100.0]
    path = tmp_path / "outbox.sqlite3"
    connection = database(path)
    outbox = Outbox(connection, now=lambda: now[0])
    outbox.enqueue("incident:42", 42, "Inspection required")
    sender = Mock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        outbox.deliver(sender)
    sent = sender.call_args.args[0]
    connection.close()
    now[0] = 131
    restarted = Outbox(database(path), now=lambda: now[0])
    sender = Mock(return_value=23)
    assert restarted.deliver(sender) == 1
    sender.assert_called_once_with(sent)
    assert restarted.status()["attempts"] == 2


def test_known_secrets_are_rejected_before_storage_and_again_before_delivery():
    connection = sqlite3.connect(":memory:")
    outbox = Outbox(connection)
    secret = "ghp_" + "x" * 30
    with pytest.raises(Blocked, match="content withheld") as error:
        outbox.enqueue("unsafe", None, "Do not copy " + secret)
    assert secret not in str(error.value)
    assert outbox.status()["pending_reports"] == 0
    outbox.enqueue("safe", None, "content initially safe")
    connection.execute("UPDATE reports SET text = ?", (secret,))
    connection.commit()
    sender = Mock()
    with pytest.raises(Blocked):
        outbox.deliver(sender)
    sender.assert_not_called()


def test_sender_validates_on_startup_uses_fixed_argv_stdin_and_parses_id(monkeypatch):
    run = Mock(side_effect=[
        SimpleNamespace(returncode=0, stdout='{"destination_valid": true}', stderr=""),
        SimpleNamespace(returncode=0, stdout='{"message_id": 321}', stderr=""),
    ])
    monkeypatch.setattr(subprocess, "run", run)
    argv = ["/usr/bin/botctl", "report-plain"]
    sender = BotSender(argv)
    argv.append("must not become executable input")
    assert sender("literal `$(not a shell)` message") == 321
    assert run.call_args_list[0].args[0] == ["/usr/bin/botctl", "report-plain", "--validate-only"]
    assert run.call_args.args[0] == ["/usr/bin/botctl", "report-plain"]
    assert run.call_args.kwargs == {
        "input": "literal `$(not a shell)` message", "capture_output": True,
        "text": True, "timeout": 75,
    }


@pytest.mark.parametrize("response,error", [
    ('{"error":"rate_limited","retry_after":87}', RateLimited),
    ('{"error":"configuration"}', PermanentDeliveryError),
    ('{"error":"transient"}', TransientDeliveryError),
    ('private invalid output', TransientDeliveryError),
])
def test_sender_classifies_errors_without_returning_process_content(monkeypatch, response, error):
    run = Mock(side_effect=[
        SimpleNamespace(returncode=0, stdout='{"destination_valid":true}', stderr=""),
        SimpleNamespace(returncode=1, stdout=response, stderr="private credentials"),
    ])
    monkeypatch.setattr(subprocess, "run", run)
    sender = BotSender(["botctl", "report-plain"])
    with pytest.raises(error) as caught:
        sender("report")
    assert "private" not in str(caught.value)
    if error is RateLimited:
        assert caught.value.retry_after == 87


def test_sender_startup_failure_prevents_sending(monkeypatch):
    run = Mock(return_value=SimpleNamespace(
        returncode=1, stdout='{"error":"configuration"}', stderr="private"
    ))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PermanentDeliveryError):
        BotSender(["botctl", "report-plain"])
    assert run.call_count == 1


@pytest.mark.parametrize("message_id", [None, True, 0, -1, "42"])
def test_unconfirmed_message_id_remains_pending(message_id):
    outbox = Outbox(sqlite3.connect(":memory:"))
    outbox.enqueue("test", None, "report")
    assert outbox.deliver(lambda _: message_id) == 0
    assert outbox.status()["pending_parts"] == 1


def test_legacy_completion_is_reported_as_unverified_not_validated_success():
    text = render_summary({"repository": "EigenformAI/example", "number": 42,
                           "completion_outcome": "unverified"},
                          [{"kind": "cleanup", "state": "noop", "result": {}}], True)
    assert "unverified" in text.lower()


def test_summary_uses_real_links_and_keeps_unpublished_work_and_decisions_explicit():
    workflow = {
        "repository": "EigenformAI/example", "number": 42,
        "url": "https://github.com/EigenformAI/example/pull/42",
    }
    stages = [{
        "kind": "cleanup", "state": "pushed", "worker_host": "contents-may-differ",
        "result": {
            "commits": ["a" * 40], "modules": ["api/cache.py"],
            "deleted_paths": ["tests/test_tautology.py"],
            "verification": [{"command": "pytest tests/test_cache.py", "outcome": "4 passed"}],
            "opencode_session_id": "ses_cleanup",
            "logs": "PRIVATE RAW LOG MUST NOT APPEAR",
        },
    }, {
        "kind": "feedback", "state": "blocked", "result": {
            "candidate_sha": "b" * 40, "checkout": "/owned/attempt-2",
            "error": "Needs an interface decision", "session_id": "ses_feedback",
            "host": "contents-may-differ",
            "feedback_dispositions": [{
                "id": 17, "url": "https://github.com/EigenformAI/example/pull/42#discussion_r17",
                "disposition": "needs decision", "reason": "Changes the public contract",
                "body": "RAW COMMENT MUST NOT APPEAR",
            }],
        },
    }]
    text = render_summary(workflow, stages, final=True)
    assert all(expected in text for expected in [
        "Final", workflow["repository"], workflow["url"], "Cleanup: Pushed", "Pushed",
        "https://github.com/EigenformAI/example/commit/" + "a" * 40,
        "4 passed", "Review feedback: Blocked", "Local changes retained; not confirmed pushed",
        "Needs an interface decision", "17", "needs decision", "Changes the public contract",
        "https://github.com/EigenformAI/example/pull/42#discussion_r17",
    ])
    assert all(value not in text for value in [
        "ses_cleanup", "ses_feedback", "contents-may-differ", "/owned/attempt-2",
        "https://github.com/EigenformAI/example/commit/" + "b" * 40,
    ])
    assert "PRIVATE RAW LOG" not in text
    assert "RAW COMMENT" not in text
    assert "Pushed" not in text.split("Review feedback: Blocked", 1)[1]
    with pytest.raises(Blocked):
        Outbox(sqlite3.connect(":memory:")).enqueue("unsafe-summary", 42, render_summary(
            workflow, [{"kind": "cleanup", "result": {"error": "ghp_" + "x" * 30}}], False))


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("state", ["blocked", "failed"])
def test_finished_summary_reports_blockers_after_later_stage_success(final, state):
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [
        {"kind": "deep_modules", "state": state},
        {"kind": "cleanup", "state": "noop"},
    ], final)
    assert "finished with blockers" in text.lower()
    assert "completed successfully" not in text.lower()
    assert "Cleanup: No changes" in text


def test_structured_blocker_shows_reason_and_next_action_not_private_diagnostics():
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "cleanup", "state": "blocked", "result": {"summary": {
            "summary": "No changes needed", "outcome": "blocked", "blockers": [{
                "reason": "Existing checks unavailable", "next_action": "Prepare pinned dependencies",
                "logs": "PRIVATE LOG MUST NOT APPEAR",
            }],
        }},
    }], True)
    assert "Blocker: Existing checks unavailable" in text
    assert "Next action: Prepare pinned dependencies" in text
    assert "PRIVATE LOG" not in text


def test_summary_condenses_trusted_checks_without_diagnostic_hashes_and_commands():
    text = render_summary({
        "repository": "EigenformAI/example", "number": 42,
        "pr": {"html_url": "https://github.com/EigenformAI/example/pull/42"},
    }, [{
        "kind": "cleanup", "state": "pushed", "spec": {"worker_host": "contents-may-differ"},
        "result": {
            "session_id": "ses_coding", "review_session_id": "ses_review",
            "candidate_sha": "a" * 40,
            "verification": {"candidate_sha": "a" * 40, "commands": [
                {"argv": ["python", "-m", "pytest", "tests with spaces"],
                 "returncode": 0, "log_sha256": "b" * 64},
            ]},
            "summary": {"changed_paths": ["api/cache.py"], "deleted_paths": ["old.py"],
                        "summary": "Removed redundant wrapper", "feedback_dispositions": [
                            {"id": 7, "url": "https://example.test/finding/7", "disposition": "fixed", "reason": "same defect"},
                        ]},
        },
    }], False)
    assert all(value in text for value in [
        "Verification: 1 passed",
        "Removed redundant wrapper", "Feedback 7", "fixed", "same defect",
    ])
    assert all(value not in text for value in [
        "contents-may-differ", "ses_coding", "ses_review", "tests with spaces", "b" * 64,
    ])


def test_noop_summary_leads_with_outcome_and_never_infers_verification_or_unpublished_edits():
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "deep_modules", "state": "noop", "active_attempt_id": "attempt-42",
        "spec": {"worker_host": "contents-may-differ"},
        "result": {"checkout": "/owned/unchanged", "session_id": "ses_noop",
                   "summary": "Dependencies unavailable; application checks could not run."},
    }], False)
    assert text.startswith("Initial maintenance summary: EigenformAI/example #42\n")
    assert "Deep modules: No changes" in text
    assert "Verification: not recorded" in text
    assert "unpublished" not in text.lower()
    assert "no new check required" not in text
    assert all(value not in text for value in ("Inspection:", "ses_noop", "contents-may-differ",
                                              "/owned/unchanged", "attempt-42"))
    assert len(text) < 350


def test_publication_concern_retains_warning_verification_and_required_action():
    sha = "c" * 40
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "conflicts", "state": "published_with_concern",
        "result": {"candidate_sha": sha, "checkout": "/owned/published", "session_id": "ses_incident",
                   "review_session_id": "ses_review", "host": "contents-may-differ",
                   "error": "Post-push observation uncertain", "unresolved_decisions": ["Acknowledge incident"],
                   "verification": [{"command": "pytest", "outcome": "9 passed"}]},
    }], True)
    assert "Merge conflicts: Published with concern" in text
    assert "Changes were published; incident requires acknowledgement" in text
    assert text.index("incident requires acknowledgement") < text.index("Post-push observation uncertain")
    assert "Unpublished" not in text
    assert all(value in text for value in (f"https://github.com/EigenformAI/example/commit/{sha}",
                                          "9 passed", "Acknowledge incident"))


def test_long_stage_explanation_is_bounded_but_explicit_decisions_are_not_hidden():
    explanation = "Dependencies unavailable. " * 80
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "cleanup", "state": "blocked", "result": {
            "error": explanation, "unresolved_decisions": ["Configure a disposable PostgreSQL database"],
        },
    }], False)
    assert "Dependencies unavailable" in text
    assert explanation not in text
    assert "Decision needed: Configure a disposable PostgreSQL database" in text
    assert text.index("Decision needed:") < text.index("Dependencies unavailable")
    assert len(text) < 550


def test_verification_summary_does_not_turn_failed_or_unknown_checks_into_success():
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "cleanup", "state": "blocked", "result": {"verification": {"commands": [
            {"argv": ["pytest"], "returncode": 0},
            {"argv": ["ruff", "check"], "returncode": 1},
            {"argv": ["mypy"], "outcome": "timed out"},
        ]}},
    }], False)
    assert "Verification: 1 passed; 1 failed; 1 unconfirmed" in text


def test_shortening_selected_prose_does_not_hide_or_partially_send_a_credential():
    token = "ghp_" + "x" * 30
    text = render_summary({"repository": "EigenformAI/example", "number": 42}, [{
        "kind": "cleanup", "state": "blocked",
        "result": {"error": "Needs onboarding. " * 30 + token},
    }], False)
    with pytest.raises(Blocked):
        Outbox(sqlite3.connect(":memory:")).enqueue("unsafe", 42, text)
