#!/usr/bin/env python3
"""Pair with the current user's T3 instance without exposing CLI diagnostics."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class PairError(Exception):
    def __init__(self, kind="unavailable"):
        self.kind = kind
        super().__init__(kind)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PairHelper:
    def __init__(self, package_spec, port, home):
        self.package_spec = package_spec
        self.port = port
        self.home = Path(home)
        runtime = f"/run/user/{os.getuid()}"
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        }
        self.deadline = time.monotonic() + 60

    def timeout(self, limit=5):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PairError()
        return min(limit, remaining)

    def command(self, *args, limit=5):
        try:
            return subprocess.run(
                list(args), env=self.env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, check=True, timeout=self.timeout(limit),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise PairError() from None

    def invocation(self):
        properties = dict(line.split("=", 1) for line in self.command(
            "systemctl", "--user", "show", "t3-serve.service",
            "--property=ActiveState", "--property=InvocationID",
        ).splitlines() if "=" in line)
        invocation = properties.get("InvocationID", "")
        if properties.get("ActiveState") != "active":
            return None
        if not re.fullmatch(r"[0-9a-f]{32}", invocation) or invocation == "0" * 32:
            raise PairError()
        return invocation

    def same_invocation(self, expected):
        if self.invocation() != expected:
            raise PairError("retryable")

    def ready(self):
        try:
            invocation = self.invocation()
            if invocation is None:
                raise PairError()
            dns = json.loads(self.command("tailscale", "status", "--json"))["Self"]["DNSName"].rstrip(".")
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", dns):
                raise PairError()
            url = f"https://{dns}" + (f":{self.port}" if self.port != 443 else "")
            runtime = json.loads((self.home / ".t3/userdata/server-runtime.json").read_text(encoding="utf-8"))
            origin = runtime["origin"]
            backend = urllib.parse.urlsplit(origin)
            if (runtime["version"] != 1 or backend.scheme != "http"
                    or backend.hostname not in ("127.0.0.1", "localhost", "::1")
                    or backend.username or backend.password or backend.query or backend.fragment
                    or backend.path not in ("", "/") or not backend.port):
                raise PairError()
            mapping = json.loads(self.command("tailscale", "serve", "status", "--json"))
            handlers = mapping["Web"][f"{dns}:{self.port}"]["Handlers"]
            # Extra handlers could route auth or pairing to a different instance.
            if (mapping["TCP"][str(self.port)].get("HTTPS") is not True
                    or set(handlers) != {"/"}
                    or handlers["/"]["Proxy"].rstrip("/") != origin.rstrip("/")):
                raise PairError()
            # Serve intercepts traffic from peers, not necessarily the host itself.
            # Check its exact backend locally; verify public HTTPS from another peer.
            endpoint = origin.rstrip("/") + "/api/auth/session"
            request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
            # Do not route tailnet probes through ambient HTTP proxies or redirects.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=self.timeout()) as response:
                if (response.status != 200 or response.geturl() != endpoint
                        or response.headers.get_content_type() != "application/json"):
                    raise PairError()
                session = json.loads(response.read(65537))
            auth = session["auth"]
            if (session["authenticated"] is not False
                    or auth["policy"] not in ("remote-reachable", "loopback-browser")
                    or not isinstance(auth["bootstrapMethods"], list)
                    or "one-time-token" not in auth["bootstrapMethods"]
                    or not isinstance(auth["sessionMethods"], list)
                    or "browser-session-cookie" not in auth["sessionMethods"]
                    or not isinstance(auth["sessionCookieName"], str)
                    or not auth["sessionCookieName"].strip()):
                raise PairError()
            self.same_invocation(invocation)
            return {"invocationId": invocation, "url": url}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, urllib.error.URLError):
            raise PairError() from None

    def execute(self, action):
        if action == "status":
            return self.ready()
        if self.invocation() is None:
            self.command("systemctl", "--user", "start", "t3-serve.service")
        while True:
            try:
                status = self.ready()
                break
            except PairError as error:
                if error.kind != "unavailable":
                    raise
                time.sleep(self.timeout(1))

        # Minting has its own bounded budget, separate from the readiness wait.
        self.deadline = time.monotonic() + 35
        self.same_invocation(status["invocationId"])
        issued_after = datetime.now(timezone.utc)
        output = self.command(
            "npx", "--offline", "--yes", self.package_spec, "auth", "pairing", "create",
            "--base-dir", str(self.home / ".t3"), "--ttl", "5m", "--label", "telegram",
            "--base-url", status["url"], "--json", limit=30,
        )
        self.same_invocation(status["invocationId"])
        try:
            issued = json.loads(output)
            pair_url = issued["pairUrl"]
            parsed = urllib.parse.urlsplit(pair_url)
            credential = issued["credential"]
            expires = datetime.fromisoformat(issued["expiresAt"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if (not isinstance(credential, str) or not credential.strip()
                    or any(ord(c) < 33 for c in pair_url)
                    or f"{parsed.scheme}://{parsed.netloc}" != status["url"]
                    or parsed.path != "/pair" or parsed.query or "?" in pair_url
                    or urllib.parse.parse_qsl(parsed.fragment, strict_parsing=True) != [("token", credential)]
                    or expires.tzinfo is None or not now < expires <= now + timedelta(minutes=5)
                    or expires < issued_after + timedelta(minutes=5, seconds=-5)):
                raise ValueError()
            return {**status, "pairUrl": pair_url, "expiresAt": issued["expiresAt"]}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PairError("invalid-response") from None


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PairError("invalid-arguments")


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--package-spec", required=True)
        parser.add_argument("--port", type=int, required=True)
        parser.add_argument("--home", default=str(Path.home()))
        parser.add_argument("action", choices=("status", "create"))
        args = parser.parse_args(argv)
        if not 1 <= args.port <= 65535 or not Path(args.home).is_absolute():
            raise PairError("invalid-arguments")
        result = PairHelper(args.package_spec, args.port, args.home).execute(args.action)
        print(json.dumps(result))
        return 0
    except PairError as error:
        print(f"t3-pair: {error.kind}", file=sys.stderr)
        return 75 if error.kind == "retryable" else 1
    except Exception:
        # Exceptions may contain subprocess output, tokens, or private paths.
        print("t3-pair: unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
