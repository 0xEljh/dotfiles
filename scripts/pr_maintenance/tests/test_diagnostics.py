import json
import os
import random
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest


def retain(database, category="coordinator_exception", **details):
    from pr_maintenance.diagnostics import retain_diagnostic

    return retain_diagnostic(database, category, **details)


def test_private_unique_bounded_records_exclude_requests_and_environment(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("PRIVATE_ENV", "environment-secret")
    details = {"stdout": "é" * 70000, "stderr": b"x" * 70000, "traceback": "private-traceback\n" * 10000,
               "request": {"body": "body-secret"}, "environment": dict(os.environ), "response": "success-secret"}
    first = retain(tmp_path / "coordinator.sqlite3", **details)
    second = retain(tmp_path / "coordinator.sqlite3", **details)
    assert isinstance(first, Path) and first.is_absolute() and second != first
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700
    assert len(first.read_bytes()) <= 256 * 1024
    record = json.loads(first.read_text())
    for field in ("stdout", "stderr", "traceback"):
        assert len(record[field].encode("utf-8")) <= 64 * 1024
        assert record[field + "_truncated"] is True
    for secret in ("body-secret", "environment-secret", "success-secret", "private-traceback"):
        assert secret not in caplog.text
    for secret in ("body-secret", "environment-secret", "success-secret"):
        assert secret not in first.read_text()
    assert not {"request", "environment", "response"} & record.keys()


def test_json_escaping_cannot_exceed_total_record_bound(tmp_path):
    path = retain(tmp_path / "db", stdout="\x00" * 65536, stderr="\x00" * 65536, traceback="\x00" * 65536)
    assert path is not None
    assert len(path.read_bytes()) <= 256 * 1024
    assert any(json.loads(path.read_text())[field + "_truncated"] for field in ("stdout", "stderr", "traceback"))


@pytest.mark.parametrize("mode", [0o755, 0o770])
def test_permissive_directory_is_rejected_not_chmodded(tmp_path, caplog, mode):
    directory = tmp_path / "diagnostics"
    directory.mkdir(mode=mode)
    directory.chmod(mode)
    assert retain(tmp_path / "db", stderr="private-secret") is None
    assert stat.S_IMODE(directory.stat().st_mode) == mode
    assert not list(directory.iterdir())
    assert "private-secret" not in caplog.text


def test_symlink_in_storage_path_is_not_followed(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert retain(link / "db", stderr="private-secret") is None
    assert not list(target.iterdir())


def test_storage_error_returns_none_without_leaking_exception(tmp_path, caplog):
    with patch("os.open", side_effect=OSError("private-storage-credential")):
        assert retain(tmp_path / "db", stderr="private-secret") is None
    assert "private-storage-credential" not in caplog.text
    assert "private-secret" not in caplog.text


def test_partial_write_is_removed_and_failure_is_safe(tmp_path, caplog):
    with patch("os.fsync", side_effect=OSError("private-storage-credential")):
        assert retain(tmp_path / "db", stderr="private-secret") is None
    assert not list((tmp_path / "diagnostics").glob("*.json"))
    assert "private-storage-credential" not in caplog.text


def test_retention_prunes_only_generated_owner_owned_regular_files(tmp_path):
    directory = tmp_path / "diagnostics"
    directory.mkdir(mode=0o700)
    unrelated = directory / "unrelated.json"
    unrelated.write_text("keep")
    outside = tmp_path / "outside"
    outside.write_text("keep")
    symlink = directory / ("diagnostic-" + "f" * 32 + ".json")
    symlink.symlink_to(outside)
    generated = []
    for i in range(200):
        path = directory / f"diagnostic-{i:032x}.json"
        path.write_text("{}")
        os.utime(path, ns=(i + 1, i + 1))
        generated.append(path)
    newest = retain(tmp_path / "db", stderr="latest")
    assert newest is not None and newest.is_file()
    assert not generated[0].exists()
    assert all(path.is_file() for path in generated[1:])
    assert unrelated.read_text() == outside.read_text() == "keep"
    assert symlink.is_symlink()
    assert len([p for p in directory.iterdir() if p.name.startswith("diagnostic-") and not p.is_symlink()]) == 200


def test_unsafe_category_does_not_become_a_path_or_log_injection(tmp_path, caplog):
    assert retain(tmp_path / "db", "../private-secret\ninjected", stderr="private-secret") is None
    assert "private-secret" not in caplog.text
    assert not (tmp_path / "diagnostics").exists()


def test_concurrent_writers_keep_complete_unique_private_records(tmp_path):
    def write(index):
        time.sleep(random.uniform(0, 0.01))
        return retain(tmp_path / "db", operation="tick", traceback=f"private-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(write, range(32)))
    assert None not in paths
    assert len(set(paths)) == 32
    assert len(list((tmp_path / "diagnostics").glob("*.json"))) == 32
    for path in paths:
        assert json.loads(path.read_text())["operation"] == "tick"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("value", ["é" * 32768, b"\xff" * 65536, "x" * 65535 + "é"])
def test_utf8_boundary_and_replacement_stay_bounded(tmp_path, value):
    path = retain(tmp_path / "db", stdout=value)
    assert path is not None
    record = json.loads(path.read_text())
    assert len(record["stdout"].encode("utf-8")) <= 65536
    assert record["stdout_truncated"] is (value != "é" * 32768)
