from __future__ import annotations

import fcntl
import os
import secrets
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Dev3000Credentials:
    username: str
    password: str


@contextmanager
def _credential_lock(auth_dir: Path, *, exclusive: bool) -> Iterator[None]:
    with (auth_dir / "lock").open("r+", encoding="utf-8") as lock_file:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_file, operation)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_current(auth_dir: Path, username: str) -> Dev3000Credentials:
    password = (auth_dir / "current" / "password").read_text(encoding="utf-8").strip()
    if not password or "\n" in password:
        raise ValueError("invalid dev-3000 password state")
    return Dev3000Credentials(username=username, password=password)


def read_credentials(auth_dir: Path, username: str) -> Dev3000Credentials:
    with _credential_lock(auth_dir, exclusive=False):
        return _read_current(auth_dir, username)


def rotate_credentials(
    auth_dir: Path,
    username: str,
    htpasswd_command: str,
) -> Dev3000Credentials:
    with _credential_lock(auth_dir, exclusive=True):
        password = secrets.token_urlsafe(24)
        result = subprocess.run(
            [htpasswd_command, "-niB", username],
            input=password + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        htpasswd = result.stdout.strip()
        if not htpasswd.startswith(f"{username}:") or "\n" in htpasswd:
            raise ValueError("htpasswd produced invalid output")

        generations = auth_dir / "generations"
        generation = Path(tempfile.mkdtemp(prefix="rotation-", dir=generations))
        current_link = auth_dir / "current"
        next_link = auth_dir / f".current-{secrets.token_hex(8)}"
        try:
            auth_group = auth_dir.stat().st_gid
            os.chown(generation, -1, auth_group)
            generation.chmod(0o2750)
            password_file = generation / "password"
            password_file.write_text(password + "\n", encoding="utf-8")
            password_file.chmod(0o600)
            htpasswd_file = generation / "htpasswd"
            htpasswd_file.write_text(htpasswd + "\n", encoding="utf-8")
            os.chown(htpasswd_file, -1, auth_group)
            htpasswd_file.chmod(0o640)

            next_link.symlink_to(Path("generations") / generation.name)
            os.replace(next_link, current_link)

            for old_generation in generations.iterdir():
                if old_generation != generation:
                    shutil.rmtree(old_generation)
            return _read_current(auth_dir, username)
        except Exception:
            next_link.unlink(missing_ok=True)
            try:
                generation_is_current = current_link.resolve() == generation
            except FileNotFoundError:
                generation_is_current = False
            if not generation_is_current:
                shutil.rmtree(generation, ignore_errors=True)
            raise


def format_credentials(url: str, credentials: Dev3000Credentials) -> str:
    return (
        "dev-3000 sharing credentials\n"
        f"URL: {url}\n"
        f"Username: {credentials.username}\n"
        f"Password: {credentials.password}"
    )
