import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from personal_telegram_bot.tpot.evidence import EvidenceItem
from personal_telegram_bot.tpot.synthesizer import OpenCodeSynthesizer, SynthesisError


TZ = ZoneInfo("Asia/Singapore")


def _evidence() -> list[EvidenceItem]:
    return [
        EvidenceItem(
            key="github:event:1",
            source="github",
            kind="commit",
            occurred_at=datetime(2026, 7, 20, 12, tzinfo=TZ),
            title="Committed evidence pipeline",
            detail=None,
            url=None,
            private=True,
        )
    ]


def test_synthesizer_uses_isolated_builtin_agent_and_free_deepseek(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    captured = {}
    payload = {
        "ideas": [
            {
                "text": "Building better prompts starts with preserving the evidence behind them.",
                "evidence_ids": ["github:event:1"],
                "angle": "lesson",
                "confidence": "high",
            }
        ]
    }

    def runner(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        event = {"type": "text", "part": {"text": json.dumps(payload)}}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(event), "stderr": ""})()

    monkeypatch.setenv("OPENCODE_CONFIG", "/unsafe/config.json")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", "/unsafe/config-dir")
    ideas = OpenCodeSynthesizer(auth_path=auth, runner=runner).synthesize(_evidence())

    command = captured["command"]
    env = captured["kwargs"]["env"]
    assert command[command.index("--agent") + 1] == "build"
    assert command[command.index("--model") + 1] == "opencode/deepseek-v4-flash-free"
    assert "--pure" in command
    assert "--auto" not in command and "--share" not in command
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert env["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "1"
    assert env["OPENCODE_PURE"] == "1"
    assert "OPENCODE_CONFIG" not in env and "OPENCODE_CONFIG_DIR" not in env
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"] == "deny"
    assert config["agent"]["build"]["permission"] == "deny"
    assert ideas[0].evidence_ids == ("github:event:1",)


def test_synthesizer_rejects_tools_and_unknown_evidence(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")

    def tool_runner(*args, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"type": "tool_use"}), "stderr": ""})()

    with pytest.raises(SynthesisError, match="tool"):
        OpenCodeSynthesizer(auth_path=auth, runner=tool_runner).synthesize(_evidence())

    def part_tool_runner(*args, **kwargs):
        event = {"type": "part", "part": {"type": "tool", "name": "read"}}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(event), "stderr": ""})()

    with pytest.raises(SynthesisError, match="tool"):
        OpenCodeSynthesizer(auth_path=auth, runner=part_tool_runner).synthesize(_evidence())

    payload = {"ideas": [{"text": "A sufficiently long grounded post idea.", "evidence_ids": ["missing"], "angle": "lesson", "confidence": "high"}]}

    def unknown_runner(*args, **kwargs):
        event = {"type": "text", "part": {"text": json.dumps(payload)}}
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(event), "stderr": ""})()

    with pytest.raises(SynthesisError, match="unknown evidence"):
        OpenCodeSynthesizer(auth_path=auth, runner=unknown_runner).synthesize(_evidence())
