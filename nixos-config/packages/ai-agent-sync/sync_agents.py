"""Install shared OpenCode/Claude agents and export standalone Codex TOML."""

import argparse
from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import tomllib

import tomli_w
import yaml


HARNESSES = {
    "opencode": Path(".config/opencode/agents"),
    "claude-code": Path(".claude/agents"),
    "codex": None,  # Export only: the qualified Codex build ignores file symlinks.
}
RESERVED = {"name", "description", "prompt", "system", "developer_instructions"}
BUILTINS = {
    "opencode": {
        "build",
        "plan",
        "general",
        "explore",
        "compaction",
        "title",
        "summary",
    },
    "claude-code": {
        "explore",
        "plan",
        "general-purpose",
        "claude",
        "statusline-setup",
        "claude-code-guide",
    },
    "codex": {"default", "worker", "explorer"},
}
CODEX_PARENT_ONLY = {
    "sandbox_mode",
    "approval_policy",
    "model_provider",
    "mcp_servers",
    "service_tier",
}


class SyncError(Exception):
    pass


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently selecting the last value."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise SyncError("YAML mapping keys must be strings")
            if key in result:
                raise SyncError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def markdown(path, *, required=True):
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(
        r"---\r?\n(.*?)^---[ \t]*(?:\r?\n|$)(.*)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        if re.match(r"\A---\r?\n", text):
            raise SyncError(f"{path}: unterminated YAML frontmatter")
        if not required:
            return {}, text
        raise SyncError(f"{path}: expected YAML frontmatter on the first line")
    try:
        metadata = yaml.load(match[1], Loader=UniqueLoader)
    except (yaml.YAMLError, SyncError) as error:
        raise SyncError(f"{path}: {error}") from error
    if not isinstance(metadata, dict):
        raise SyncError(f"{path}: frontmatter must be a mapping")
    return metadata, match[2].lstrip("\r\n")


def files(root, suffix):
    # Do not follow directory links, which can escape the authored tree or loop.
    return sorted(
        path
        for path in entries(root)
        if path.suffix == suffix and stat.S_ISREG(path.stat().st_mode)
    )


def text_field(metadata, key, path):
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f"{path}: {key} must be a nonempty string")
    return value


def render_shared(source):
    if not source.is_dir():
        raise SyncError(
            f"{source}: shared source directory is missing; refusing cleanup"
        )
    rendered = {harness: {} for harness in HARNESSES}
    for path in files(source, ".md"):
        metadata, body = markdown(path)
        unknown = metadata.keys() - {"name", "description", "harnesses"}
        if unknown:
            raise SyncError(f"{path}: unknown common fields: {sorted(unknown)}")
        name = text_field(metadata, "name", path)
        if len(name) > 256 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise SyncError(
                f"{path}: invalid shared name {name!r}; use lowercase kebab-case, at most 256 characters"
            )
        description = text_field(metadata, "description", path)
        print(f"Source: {path} -> {name}")
        if not body.strip():
            raise SyncError(f"{path}: instructions must not be empty")
        overrides = metadata.get("harnesses", {})
        if not isinstance(overrides, dict) or overrides.keys() - HARNESSES.keys():
            raise SyncError(
                f"{path}: harnesses must be a mapping with only {', '.join(HARNESSES)}"
            )
        for harness in HARNESSES:
            options = overrides.get(harness, {})
            if not isinstance(options, dict):
                raise SyncError(f"{path}: {harness} options must be a mapping")
            if options.keys() & RESERVED:
                raise SyncError(
                    f"{path}: {harness} cannot override canonical fields: {sorted(options.keys() & RESERVED)}"
                )
            if options:
                print(
                    f"Warning: {path}: {harness} options pass through without schema validation; unknown native keys may be ignored",
                    file=sys.stderr,
                )
            if harness == "codex":
                for key in options.keys() & CODEX_PARENT_ONLY:
                    print(
                        f"Warning: {path}: Codex ignores per-agent {key}; configure it on the parent, not as a safety boundary",
                        file=sys.stderr,
                    )
            if name in BUILTINS[harness]:
                print(
                    f"Warning: {path}: {name} overrides a {harness} built-in agent",
                    file=sys.stderr,
                )
            native = {"description": description, **options}
            if harness == "opencode":
                native.setdefault("mode", "subagent")
            else:
                native["name"] = name
            try:
                if harness == "codex":
                    content = tomli_w.dumps(
                        {**native, "developer_instructions": body},
                        multiline_strings=True,
                    )
                    tomllib.loads(content)
                    filename = name + ".toml"
                else:
                    content = (
                        "---\n"
                        + yaml.safe_dump(native, allow_unicode=True, sort_keys=False)
                        + "---\n\n"
                        + body
                    )
                    filename = name + ".md"
                # All outputs use flat names; check the filesystem limit before installation.
                if len(filename.encode("utf-8")) > 255:
                    raise ValueError(
                        "generated filename exceeds the 255-byte filesystem limit"
                    )
            except (TypeError, ValueError, yaml.YAMLError, RecursionError) as error:
                raise SyncError(
                    f"{path}: cannot serialize {harness}: {error}"
                ) from error
            if filename in rendered[harness]:
                raise SyncError(f"{path}: duplicate shared identity: {name}")
            rendered[harness][filename] = (name, content, path)
    return rendered


def link_target(path):
    """Normalize the link lexically so ownership survives deleted targets."""
    return Path(os.path.abspath(path.parent / os.readlink(path)))


def owned(path, source, generated):
    return path.is_symlink() and any(
        link_target(path).is_relative_to(root) and link_target(path) != root
        for root in (source, generated)
    )


def real_parents(path, home):
    """Never write through a directory symlink inside the selected home."""
    for parent in reversed([path, *path.parents]):
        if parent == home or not parent.is_relative_to(home):
            continue
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise SyncError(
                f"{parent}: expected a real directory, not a file or symlink"
            )


def native_identity(path, relative, harness):
    if harness == "opencode":
        markdown(path, required=False)
        return relative.with_suffix("").as_posix()
    if harness == "codex":
        try:
            metadata = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise SyncError(f"{path}: {error}") from error
    else:
        metadata, _ = markdown(path, required=False)
        if "name" not in metadata:
            return None  # Claude ignores documentation files without a name.
    return text_field(metadata, "name", path)


def entries(root):
    """Inventory a directory completely or fail, without following child directories."""
    try:
        mode = root.stat().st_mode
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(mode):
        raise SyncError(f"{root}: expected a directory")

    def scan_error(error):
        raise error

    result = []
    for directory, subdirs, filenames in os.walk(
        root, followlinks=False, onerror=scan_error
    ):
        for name in subdirs + filenames:
            path = Path(directory) / name
            path.lstat()  # Permission errors are not missing or unowned entries.
            result.append(path)
    return sorted(result)


@dataclass
class Installation:
    harness: str
    source: Path
    destination: Path | None
    generated: Path
    migrate: bool = False
    links: dict = field(default_factory=dict)
    contents: dict = field(default_factory=dict)
    stale_links: list = field(default_factory=list)
    stale_generated: list = field(default_factory=list)


def plan_installation(source_root, home, rendered):
    plans = []
    for harness, location in HARNESSES.items():
        source = source_root / harness / "agents"
        destination = home / location if location is not None else None
        generated = home / ".local/share/ai-tools/agents" / harness
        plan = Installation(harness, source, destination, generated)
        real_parents(generated, home)
        if destination is not None:
            real_parents(destination.parent, home)
        if destination is not None and destination.is_symlink():
            if link_target(destination) != source:
                raise SyncError(
                    f"{destination}: refusing unknown whole-directory symlink"
                )
            plan.migrate = True
        elif (
            destination is not None
            and destination.exists()
            and not destination.is_dir()
        ):
            raise SyncError(f"{destination}: refusing unrelated destination file")

        identities = {}
        paths = {}

        def register(relative, identity, origin):
            # Apply the same conservative path comparison on Linux and macOS.
            key = relative.as_posix().casefold()
            if key in paths:
                raise SyncError(
                    f"{origin}: destination path collision: {relative} with {paths[key]}"
                )
            paths[key] = origin
            if identity is not None:
                key = identity.casefold()
                if key in identities:
                    raise SyncError(
                        f"{origin}: identity collision: {identity} with {identities[key]}"
                    )
                identities[key] = origin

        for filename, (name, content, origin) in rendered[harness].items():
            relative = Path(filename)
            register(relative, name, origin)
            plan.contents[relative] = content
            if destination is not None:
                plan.links[relative] = generated / relative
        suffix = ".toml" if harness == "codex" else ".md"
        for path in files(source, suffix) if destination is not None else []:
            relative = path.relative_to(source)
            register(relative, native_identity(path, relative, harness), path)
            plan.links[relative] = path

        if destination is not None and not plan.migrate:
            for path in entries(destination):
                relative = path.relative_to(destination)
                if owned(path, source, generated):
                    if relative not in plan.links:
                        plan.stale_links.append(path)
                    continue
                if path.is_dir() and not path.is_symlink():
                    if relative.as_posix().casefold() in paths:
                        raise SyncError(
                            f"{path}: directory collides with planned agent"
                        )
                    continue
                identity = None
                if path.suffix == suffix:
                    try:
                        is_native_file = stat.S_ISREG(path.stat().st_mode)
                    except FileNotFoundError:
                        is_native_file = False  # Preserve unrelated dangling links.
                    if is_native_file:
                        identity = native_identity(path, relative, harness)
                register(relative, identity, path)
        for relative in plan.links:
            if not plan.migrate:
                real_parents(destination / relative.parent, home)
            # Check directory casing and file/directory prefix conflicts as well.
            for other in paths:
                rel = relative.as_posix().casefold()
                if other != rel and (
                    rel.startswith(other + "/") or other.startswith(rel + "/")
                ):
                    raise SyncError(
                        f"{destination / relative}: file/directory path collision with {paths[other]}"
                    )
        for path in entries(generated):
            if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                raise SyncError(
                    f"{path}: generated tree must contain only real files and directories"
                )
            if (
                path.is_file()
                and path.suffix in {".md", ".toml"}
                and path.relative_to(generated) not in plan.contents
            ):
                plan.stale_generated.append(path)
        plans.append(plan)
    return plans


def atomic_file(path, content):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    fd, temporary = tempfile.mkstemp(
        prefix=".agent-sync-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def atomic_link(path, target):
    if path.is_symlink() and link_target(path) == target:
        return False
    fd, temporary = tempfile.mkstemp(
        prefix=".agent-sync-", suffix=".tmp", dir=path.parent
    )
    os.close(fd)
    os.unlink(temporary)
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    return True


def install(plans):
    for plan in plans:
        if plan.migrate:
            # Only remove the link, never its source directory.
            if (
                not plan.destination.is_symlink()
                or link_target(plan.destination) != plan.source
            ):
                raise SyncError(
                    f"{plan.destination}: directory link changed during sync"
                )
            plan.destination.unlink()
            print(f"Migrated directory link: {plan.destination}")
        if plan.destination is not None:
            if plan.destination.is_symlink():
                raise SyncError(
                    f"{plan.destination}: destination changed to a directory symlink during sync"
                )
            plan.destination.mkdir(parents=True, exist_ok=True)
        plan.generated.mkdir(parents=True, exist_ok=True)
        for relative, content in plan.contents.items():
            output = plan.generated / relative
            atomic_file(output, content)
            action = (
                "Rendered"
                if plan.destination is not None
                else "Exported (not installed)"
            )
            print(f"{action}: {relative.stem} -> {output}")
        # Remove stale names first: on macOS a case-only rename can address the
        # same directory entry as the replacement, even with different spelling.
        for path in plan.stale_links:
            if owned(path, plan.source, plan.generated):
                path.unlink()
                print(f"Removed owned link: {path}")
        for relative, target in plan.links.items():
            destination = plan.destination / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(destination) and not owned(
                destination, plan.source, plan.generated
            ):
                raise SyncError(
                    f"{destination}: refusing destination changed during sync"
                )
            if atomic_link(destination, target):
                print(f"Added link: {destination} -> {target}")
        for path in plan.stale_generated:
            path.unlink()
            print(f"Removed generated file: {path}")


def seed_native(source_root, home):
    """Seed legacy links when sync is disabled, without adopting existing entries."""
    for harness, location in HARNESSES.items():
        if location is None:
            continue
        source = source_root / harness / "agents"
        destination = home / location
        if os.path.lexists(destination):
            print(
                f"Preserved existing agents entry: {destination} (shared sync disabled)"
            )
        elif source.is_dir():
            real_parents(destination.parent, home)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=True)
            print(f"Linked native agents: {source} -> {destination}")
        else:
            print(f"Warning: agent source not found: {source}", file=sys.stderr)


def sync(source_root, home, *, seed_only=False):
    lock_directory = home / ".local/share/ai-tools/agents"
    real_parents(lock_directory, home)
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / ".sync.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if seed_only:
            seed_native(source_root, home)
        else:
            rendered = render_shared(source_root / "shared/agents")
            plans = plan_installation(source_root, home, rendered)
            install(plans)
    if seed_only:
        print("Native agent seeding complete; shared sync disabled")
        return
    print("Agent sync complete: OpenCode/Claude installed; Codex exported only")
    print(
        "Note: files are synced, not running catalogs; OpenCode may need a fresh runtime"
    )


def watch(source_root, home):
    executable = shutil.which("watchexec")
    if not executable:
        raise SyncError(
            "watchexec is required for --watch; use the packaged command or manual sync"
        )
    command = [
        executable,
        "--debounce",
        "300ms",
        "--on-busy-update",
        "queue",
        "--no-discover-ignore",
        "--shell=none",
        "--project-origin",
        str(source_root),
        "--ignore",
        "/codex/**",
        "--ignore",
        "/codex",
    ]
    watched = set()
    for namespace in (
        "shared",
        *(name for name, location in HARNESSES.items() if location is not None),
    ):
        directory = source_root / namespace / "agents"
        # Watch the nearest existing ancestor to catch optional namespace creation.
        while not directory.is_dir() and directory != source_root:
            directory = directory.parent
        if not directory.is_dir():
            raise SyncError(f"{directory}: watcher source directory is missing")
        if directory not in watched:
            command.extend(["--watch", str(directory)])
            watched.add(directory)
    command.extend(
        [
            "--",
            sys.executable,
            str(Path(__file__).resolve()),
            "--source-root",
            str(source_root),
            "--home",
            str(home),
        ]
    )
    # Watchexec runs once at startup and survives child errors by default.
    os.execv(executable, command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        help="authored ai-tools tree (default: ~/dotfiles/ai-tools)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="installation home (default: current user's home)",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--watch",
        action="store_true",
        help="sync at startup and after source edits using watchexec",
    )
    action.add_argument(
        "--seed-native",
        action="store_true",
        help="seed missing legacy directory links under the sync lock; preserve existing entries",
    )
    args = parser.parse_args()
    # Legacy activation uses the authored checkout spelling, including aliases.
    # Keep ownership and exact directory-link migration in that same namespace.
    source = Path(
        os.path.abspath(
            (args.source_root or args.home / "dotfiles/ai-tools").expanduser()
        )
    )
    home = args.home.expanduser().resolve()
    try:
        if args.watch:
            return watch(source, home)
        sync(source, home, seed_only=args.seed_native)
    except (SyncError, OSError, UnicodeError, RecursionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
