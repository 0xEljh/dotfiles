"""Keep installed and host-local agents safe when shared sync is disabled."""

from pathlib import Path
import fcntl
import subprocess
import sys
import tempfile
import time
import unittest


PACKAGE = Path(__file__).resolve().parents[1] / "nixos-config/packages/ai-agent-sync"


class LegacyActivationTests(unittest.TestCase):
    def test_seed_and_sync_wait_for_the_same_lock_then_preserve_sources(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            source = root / "ai-tools"
            home = root / "home"
            (source / "shared/agents").mkdir(parents=True)
            (source / "shared/agents/pilot.md").write_text(
                "---\nname: pilot\ndescription: Lock probe\n---\nReview.\n"
            )
            for namespace in ("opencode", "claude-code"):
                (source / namespace / "agents").mkdir(parents=True)
            lock_path = home / ".local/share/ai-tools/agents/.sync.lock"
            lock_path.parent.mkdir(parents=True)
            command = [
                sys.executable,
                PACKAGE / "sync_agents.py",
                "--source-root",
                source,
                "--home",
                home,
            ]
            processes = []
            try:
                with lock_path.open("w") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    processes = [
                        subprocess.Popen(
                            [*command, *flags],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        for flags in (["--seed-native"], [])
                    ]
                    time.sleep(0.3)
                    for process in processes:
                        self.assertIsNone(
                            process.poll(), "operation bypassed the held sync lock"
                        )
                    self.assertFalse((home / ".config/opencode/agents").exists())
                    self.assertFalse((home / ".claude/agents").exists())
                for process in processes:
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                for namespace, location in (
                    ("opencode", ".config/opencode/agents"),
                    ("claude-code", ".claude/agents"),
                ):
                    self.assertEqual(
                        list((source / namespace / "agents").iterdir()), []
                    )
                    destination = home / location
                    self.assertFalse(destination.is_symlink())
                    self.assertTrue((destination / "pilot.md").is_symlink())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)

    def test_disabling_sync_preserves_real_agent_directory_and_local_content(self):
        with tempfile.TemporaryDirectory(dir="/tmp/opencode") as temporary:
            root = Path(temporary)
            source = root / "ai-tools"
            home = root / "home"
            (source / "shared/agents").mkdir(parents=True)
            (source / "shared/agents/pilot.md").write_text(
                "---\nname: pilot\ndescription: Activation probe\n---\nReview.\n"
            )
            for harness, location in (
                ("opencode", ".config/opencode/agents"),
                ("claude-code", ".claude/agents"),
            ):
                native = source / harness / "agents"
                native.mkdir(parents=True)
                destination = home / location
                destination.parent.mkdir(parents=True)
                destination.symlink_to(native, target_is_directory=True)
            subprocess.run(
                [
                    sys.executable,
                    PACKAGE / "sync_agents.py",
                    "--source-root",
                    source,
                    "--home",
                    home,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            for harness, location in (
                ("opencode", ".config/opencode/agents"),
                ("claude-code", ".claude/agents"),
            ):
                destination = home / location
                local = destination / "local.md"
                local.write_text("Host-local content.\n")
                result = subprocess.run(
                    [
                        sys.executable,
                        PACKAGE / "sync_agents.py",
                        "--seed-native",
                        "--source-root",
                        source,
                        "--home",
                        home,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(destination.is_symlink())
                self.assertEqual(local.read_text(), "Host-local content.\n")
                self.assertTrue((destination / "pilot.md").is_symlink())


if __name__ == "__main__":
    unittest.main()
