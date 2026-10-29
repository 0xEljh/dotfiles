"""Exercise watcher recovery and serialized concurrent syncs in an isolated home."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "nixos-config/packages/ai-agent-sync/sync_agents.py"
)


class SyncLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "ai-tools"
        self.home = self.root / "home"
        self.home.mkdir()
        for namespace in ("shared", "opencode", "claude-code"):
            (self.source / namespace / "agents").mkdir(parents=True)
        self.agent = self.source / "shared/agents/pilot.md"
        self.output = self.home / ".local/share/ai-tools/agents/codex/pilot.toml"
        self.installed = self.home / ".config/opencode/agents/pilot.md"
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            XDG_STATE_HOME=str(self.home / ".local/state"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
        )
        self.command = [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(self.source),
            "--home",
            str(self.home),
        ]
        self.save("pilot", "initial")

    def save(self, name, body):
        temporary = self.agent.with_suffix(".tmp")
        temporary.write_text(
            f"---\nname: {name}\ndescription: Lifecycle probe\n---\n{body}\n"
        )
        os.replace(temporary, self.agent)

    def sync(self):
        return subprocess.run(
            self.command,
            capture_output=True,
            text=True,
            timeout=20,
            env=self.env,
            cwd=self.root,
        )

    @contextmanager
    def watching(self):
        with tempfile.TemporaryFile(mode="w+", dir=self.root) as log:
            process = subprocess.Popen(
                [*self.command, "--watch"],
                stdout=log,
                stderr=log,
                start_new_session=True,
                env=self.env,
                cwd=self.root,
            )
            try:
                yield process, log
            except BaseException:
                log.seek(0)
                print(log.read())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.05)
        self.fail("watcher did not converge within 10 seconds")

    def prompt_is(self, text, path=None):
        try:
            data = tomllib.loads((path or self.output).read_text())
            return data["developer_instructions"].strip() == text
        except FileNotFoundError:
            return False

    def test_jittered_concurrent_syncs_converge_after_atomic_saves(self):
        first = self.sync()
        self.assertEqual(first.returncode, 0, first.stderr)

        def run_worker(seed):
            rng = random.Random(seed)
            results = []
            for _ in range(6):
                time.sleep(rng.uniform(0, 0.025))
                results.append(self.sync())
            return results

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_worker, seed) for seed in range(5)]
            for version in range(15):
                self.save("pilot", f"version {version}")
                time.sleep(random.Random(version).uniform(0.005, 0.02))
            results = [result for future in futures for result in future.result()]
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sync().returncode, 0)
        self.assertTrue(self.prompt_is("version 14"))
        self.assertFalse(self.output.is_symlink())
        self.assertTrue(self.installed.is_symlink())
        self.assertFalse((self.home / ".codex").exists())
        self.assertEqual(list(self.output.resolve().parent.glob("*.tmp")), [])

    @unittest.skipUnless(shutil.which("watchexec"), "watchexec not on PATH")
    def test_stopping_watcher_stops_its_process_group(self):
        process = subprocess.Popen(
            [*self.command, "--watch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=self.env,
            cwd=self.root,
        )

        def group_exists():
            try:
                os.killpg(process.pid, 0)
                return True
            except ProcessLookupError:
                return False

        try:
            self.wait_for(lambda: self.prompt_is("initial"))
            process.terminate()
            process.wait(timeout=5)
            self.wait_for(lambda: not group_exists())
        finally:
            if group_exists():
                os.killpg(process.pid, signal.SIGTERM)
            if process.poll() is None:
                process.wait(timeout=5)

    @unittest.skipUnless(shutil.which("watchexec"), "watchexec not on PATH")
    def test_watcher_startup_invalid_edit_rename_delete_and_manual_sync(self):
        with self.watching() as (process, log):
            self.wait_for(lambda: self.prompt_is("initial"))
            before = self.output.read_bytes()
            self.agent.write_text("---\nname: [invalid\n---\nbroken\n")

            def reported_error():
                log.seek(0)
                return "error:" in log.read().lower()

            self.wait_for(reported_error)
            self.assertEqual(self.output.read_bytes(), before)
            self.assertIsNone(process.poll(), "watcher exited on invalid source")
            self.save("pilot", "recovered")
            self.wait_for(lambda: self.prompt_is("recovered"))

            # A rename removes the old exported identity and its installed link.
            self.save("renamed", "after rename")
            renamed = self.output.with_name("renamed.toml")
            installed_renamed = self.installed.with_name("renamed.md")
            self.wait_for(
                lambda: (
                    self.prompt_is("after rename", renamed)
                    and not self.output.exists()
                    and not self.installed.is_symlink()
                    and installed_renamed.is_symlink()
                )
            )
            self.agent.rename(self.agent.with_name("moved.md"))
            native = self.source / "opencode/agents/native.md"
            native.write_text("---\ndescription: Native probe\n---\nNative prompt\n")
            native_output = self.installed.with_name("native.md")
            self.wait_for(native_output.is_symlink)
            self.assertEqual(native_output.resolve(), native.resolve())
            result = self.sync()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.agent.with_name("moved.md").unlink()
            native.unlink()
            self.wait_for(
                lambda: (
                    not renamed.exists()
                    and not native_output.is_symlink()
                    and not installed_renamed.is_symlink()
                )
            )
            self.assertIsNone(process.poll())
            self.assertFalse((self.home / ".codex").exists())

    @unittest.skipUnless(shutil.which("watchexec"), "watchexec not on PATH")
    def test_watcher_starts_with_missing_native_namespaces_and_detects_creation(self):
        for namespace, location in (
            ("opencode", ".config/opencode/agents"),
            ("claude-code", ".claude/agents"),
        ):
            with self.subTest(namespace=namespace):
                (self.source / namespace / "agents").rmdir()
                (self.source / namespace).rmdir()
                self.save("pilot", f"startup without {namespace}")
                with self.watching() as (process, log):
                    self.wait_for(
                        lambda: self.prompt_is(f"startup without {namespace}")
                    )
                    self.assertIsNone(
                        process.poll(), "watcher rejected an optional namespace"
                    )
                    native = self.source / namespace / "agents/appeared.md"
                    native.parent.mkdir(parents=True)
                    native.write_text(
                        "---\nname: appeared\ndescription: Newly created native agent\n"
                        "---\nNew native prompt.\n",
                        encoding="utf-8",
                    )
                    destination = self.home / location / "appeared.md"
                    self.wait_for(destination.is_symlink)
                    self.assertEqual(destination.resolve(), native.resolve())
                    self.assertEqual(destination.read_bytes(), native.read_bytes())
                    self.assertFalse((self.home / ".codex").exists())

    @unittest.skipUnless(shutil.which("watchexec"), "watchexec not on PATH")
    def test_codex_native_only_sources_are_not_watched(self):
        for missing_namespace in (False, True):
            with self.subTest(missing_namespace=missing_namespace):
                if missing_namespace:
                    (self.source / "opencode/agents").rmdir()
                    (self.source / "opencode").rmdir()
                token = f"watching without Codex: {missing_namespace}"
                self.save("pilot", token)
                with self.watching() as (process, log):
                    self.wait_for(lambda: self.prompt_is(token))
                    log.seek(0)
                    before = log.read().count("Agent sync complete")
                    self.assertGreaterEqual(before, 1)
                    native = self.source / "codex/agents/ignored.toml"
                    native.parent.mkdir(parents=True, exist_ok=True)
                    for version in range(3):
                        native.write_text(
                            f"not = [valid\n# {version}\n", encoding="utf-8"
                        )
                        time.sleep(0.4)
                    log.seek(0)
                    self.assertEqual(log.read().count("Agent sync complete"), before)
                    self.assertIsNone(process.poll())
                    self.assertTrue(self.prompt_is(token))
                    self.assertFalse((self.home / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
