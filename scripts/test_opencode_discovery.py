"""Regression tests for OpenCode's finite discovery output and pipe backpressure."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


WRAPPER = (
    Path(__file__).resolve().parents[1]
    / "nixos-config/packages/opencode-buffered/buffered-discovery.sh"
)
PAYLOAD = b"finite discovery output\n" * 100_000


def delayed_read(command, *, delay=0.3, env=None):
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    ) as process:
        # Start backpressure after startup, without a buffered read-ahead.
        first = os.read(process.stdout.fileno(), 1)
        time.sleep(delay)
        stdout, stderr = process.communicate(timeout=30)
        return process.returncode, first + stdout, stderr


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        self.producer = self.root / "producer"
        self.producer.write_text(
            f"#!{sys.executable}\n"
            "import json, os, stat, sys\n"
            "args = sys.argv[1:]\n"
            "assert args == json.loads(os.environ['EXPECTED_ARGS'])\n"
            "fd = os.fstat(1)\n"
            "if not (args[:1] == ['models'] or args[:2] == ['agent', 'list']):\n"
            "    assert stat.S_ISFIFO(fd.st_mode)\n"
            "    os.write(1, sys.stdin.buffer.read())\n"
            "    sys.exit(0)\n"
            "if stat.S_ISREG(fd.st_mode):\n"
            "    assert stat.S_IMODE(fd.st_mode) == 0o600\n"
            "os.write(2, b'producer stderr\\n')\n"
            "os.set_blocking(1, False)\n"
            "payload = b'finite discovery output\\n' * 100_000\n"
            "for offset in range(0, len(payload), 4096):\n"
            "    try:\n"
            "        os.write(1, payload[offset:offset + 4096])\n"
            "    except BlockingIOError:\n"
            "        pass  # Model unawaited writes abandoned at process exit.\n"
            "os._exit(int(os.environ.get('PRODUCER_STATUS', '0')))\n"
        )
        self.producer.chmod(0o700)

    def environment(self, args, status=0):
        return dict(
            os.environ,
            TMPDIR=str(self.spool),
            EXPECTED_ARGS=json.dumps(args),
            PRODUCER_STATUS=str(status),
        )

    def test_raw_pipe_reproduces_truncation(self):
        args = ["models", "--verbose"]
        env = self.environment(args)
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [self.producer, *args], stdout=output, stderr=subprocess.PIPE,
                env=env, check=True,
            )
            output.seek(0)
            self.assertTrue(output.read() == PAYLOAD)
        status, output, stderr = delayed_read([self.producer, *args], env=env)
        self.assertEqual(status, 0)
        self.assertEqual(stderr, result.stderr)
        self.assertLess(len(output), len(PAYLOAD))
        print(f"raw producer: {len(output)}/{len(PAYLOAD)} bytes", flush=True)

    def test_discovery_survives_delayed_reader(self):
        for args in (["models", "--verbose"], ["agent", "list"]):
            for delay in (0.1, 0.2, 0.3):
                with self.subTest(args=args, delay=delay):
                    status, output, stderr = delayed_read(
                        ["bash", WRAPPER, self.producer, *args],
                        delay=delay, env=self.environment(args),
                    )
                    self.assertEqual(status, 0, stderr)
                    self.assertTrue(output == PAYLOAD)
                    self.assertEqual(stderr, b"producer stderr\n")
                    self.assertEqual(list(self.spool.iterdir()), [])

    def test_nonzero_status_keeps_output_and_stderr(self):
        args = ["models", "--verbose"]
        status, output, stderr = delayed_read(
            ["bash", WRAPPER, self.producer, *args],
            env=self.environment(args, status=17),
        )
        self.assertEqual(status, 17)
        self.assertTrue(output == PAYLOAD)
        self.assertEqual(stderr, b"producer stderr\n")
        self.assertEqual(list(self.spool.iterdir()), [])

    def test_other_commands_preserve_pipe_stdin_and_args(self):
        for args in ([], ["agent"], ["agent", "create"], ["run", "two words", ""]):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", WRAPPER, self.producer, *args],
                    input=b"passthrough stdin\n", capture_output=True,
                    env=self.environment(args), timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b"passthrough stdin\n")
                self.assertEqual(list(self.spool.iterdir()), [])

    def test_signal_cleans_spool(self):
        args = ["models", "--verbose"]
        with subprocess.Popen(
            ["bash", WRAPPER, self.producer, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment(args), start_new_session=True,
        ) as process:
            try:
                deadline = time.monotonic() + 5
                while not any(
                    path.stat().st_size == len(PAYLOAD)
                    for path in self.spool.iterdir()
                ):
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail("discovery did not fill its spool")
                    time.sleep(0.01)
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(list(self.spool.iterdir()), [])
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()


@unittest.skipUnless(os.environ.get("OPENCODE_REAL_BINARY"), "opt-in installed CLI check")
class InstalledDiscoveryTests(unittest.TestCase):
    def test_models_match_regular_file_baseline(self):
        binary = os.environ["OPENCODE_REAL_BINARY"]
        args = ["models", "--verbose"]
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [binary, *args], stdout=output, stderr=subprocess.PIPE, timeout=60,
            )
            self.assertEqual(result.returncode, 0)
            output.seek(0)
            expected = output.read()
        headers = re.findall(rb"^([\w.-]+)/[^\s]+$", expected, re.MULTILINE)
        self.assertTrue(headers, "no model headers in baseline")
        print(
            f"installed baseline: providers={','.join(sorted({p.decode() for p in headers}))} "
            f"models={len(headers)} bytes={len(expected)}", flush=True,
        )
        wrapper = os.environ.get("OPENCODE_PACKAGED_BINARY")
        command = [wrapper] if wrapper else [shutil.which("bash"), str(WRAPPER), binary]
        for delay in (0.2, 0.4, 0.6):
            status, actual, _ = delayed_read([*command, *args], delay=delay)
            self.assertEqual(status, 0)
            self.assertTrue(actual == expected, "discovery differs from regular-file baseline")
            print(f"installed delayed reader {delay}s: bytes={len(actual)} equal=True", flush=True)


if __name__ == "__main__":
    unittest.main()
