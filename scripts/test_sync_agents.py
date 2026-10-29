"""Black-box tests for shared agent rendering and ownership-safe installation.

Run with PyYAML and tomli_w available to this interpreter, for example:
    uv run --with pyyaml --with tomli-w python -m unittest scripts.test_sync_agents
"""

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

import tomli_w
import yaml


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "nixos-config/packages/ai-agent-sync/sync_agents.py"
)
HARNESSES = ("opencode", "claude-code", "codex")
INSTALLED_HARNESSES = ("opencode", "claude-code")
NATIVE_DIRS = {
    "opencode": Path(".config/opencode/agents"),
    "claude-code": Path(".claude/agents"),
    "codex": Path(".codex/agents"),
}
DESCRIPTION = 'Review "quoted" changes, C:\\work\\review, and café / 東京.'
BODY = (
    'Review "double", \'single\', and """triple quotes""" literally.\n'
    "Read C:\\work\\agent and /tmp/a/b without rewriting backslashes.\n"
    "\n"
    "Unicode: café, λ, 東京, 🦉.\n"
    "    Keep this indentation and these two spaces.  \n"
    "--- is prompt content, not another frontmatter block.\n"
)


def write_markdown(path, metadata, body=BODY):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n"
        + body,
        encoding="utf-8",
    )
    return path


def read_markdown(path):
    match = re.fullmatch(
        r"---\r?\n(.*?)^---[ \t]*\r?\n(.*)",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"No YAML frontmatter in {path}")
    return yaml.safe_load(match[1]), match[2]


def snapshot(root):
    """Record bytes and link targets, without following directory symlinks."""
    result = {}

    def visit(path):
        key = str(path.relative_to(root.parent))
        if path.is_symlink():
            result[key] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[key] = ("directory",)
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.exists():
            result[key] = ("file", path.read_bytes())
        else:
            result[key] = ("missing",)

    visit(root)
    return result


class Installation:
    def __init__(self, root):
        self.root = root
        self.source = root / "ai-tools"
        self.home = root / "home"
        self.home.mkdir(parents=True)
        (self.source / "shared/agents").mkdir(parents=True)
        # All native-only source namespaces are optional. Codex is export-only.

    def native_source(self, harness):
        return self.source / harness / "agents"

    def destination(self, harness):
        return self.home / NATIVE_DIRS[harness]

    def generated(self, harness):
        return self.home / ".local/share/ai-tools/agents" / harness

    def filename(self, harness, name):
        return name + (".toml" if harness == "codex" else ".md")

    def shared(self, relative="reviewer.md", *, name="reviewer", body=BODY, **extra):
        metadata = {"name": name, "description": DESCRIPTION}
        metadata.update(extra)
        return write_markdown(self.source / "shared/agents" / relative, metadata, body)

    def native(self, harness, relative=None, *, name="native-reviewer"):
        path = self.native_source(harness) / (relative or self.filename(harness, name))
        metadata = {"description": "A native-only agent."}
        if harness != "opencode":
            metadata["name"] = name
        if harness == "codex":
            metadata["developer_instructions"] = "Native-only instructions.\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tomli_w.dumps(metadata), encoding="utf-8")
            return path
        return write_markdown(path, metadata, "Native-only instructions.\n")

    def run(self, *, source_root=None):
        env = dict(os.environ)
        env.update(
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            XDG_STATE_HOME=str(self.home / ".local/state"),
        )
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-root",
                str(source_root if source_root is not None else self.source),
                "--home",
                str(self.home),
            ],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def outputs(self):
        return {
            harness: {
                "native": snapshot(self.destination(harness)),
                "generated": snapshot(self.generated(harness)),
            }
            for harness in HARNESSES
        }


class SyncAgentsTests(unittest.TestCase):
    def test_unreadable_shared_inventory_preserves_last_successful_installation(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses directory permission checks")
        case = self.installation()
        case.shared()
        self.assert_success(case.run())
        before = case.outputs()
        directory = case.source / "shared/agents"
        directory.chmod(0)
        try:
            self.assert_rejected(case.run())
            self.assertEqual(case.outputs(), before)
        finally:
            directory.chmod(0o755)

    def test_inaccessible_unowned_target_cannot_hide_a_native_identity_collision(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses directory permission checks")
        case = self.installation()
        case.shared()
        self.assert_success(case.run())
        private = case.root / "private"
        target = write_markdown(
            private / "agent.md",
            {
                "name": "reviewer",
                "description": "A host-local agent with the same identity.",
            },
        )
        (case.destination("claude-code") / "local.md").symlink_to(target)
        case.shared(body="This edit must wait for complete collision validation.\n")
        before = case.outputs()
        private.chmod(0)
        try:
            self.assert_rejected(case.run())
            self.assertEqual(case.outputs(), before)
        finally:
            private.chmod(0o755)
        # Once readable, this is a real name collision, not an unrelated note.
        self.assert_rejected(case.run(), mentions="reviewer")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="test-sync-agents-", dir="/tmp/opencode"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case_number = 0

    def installation(self):
        self.case_number += 1
        return Installation(self.root / f"case-{self.case_number}")

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_rejected(self, result, *, mentions=None):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        if mentions is not None:
            self.assertIn(mentions, result.stdout + result.stderr)

    def assert_link(self, path, target):
        self.assertTrue(path.is_symlink(), f"Not a symlink: {path}")
        self.assertEqual(path.resolve(), target.resolve())

    def assert_agent(self, case, harness, name="reviewer", body=BODY):
        filename = case.filename(harness, name)
        generated = case.generated(harness) / filename
        if harness in INSTALLED_HARNESSES:
            self.assert_link(case.destination(harness) / filename, generated)
        self.assertTrue(generated.is_file(), str(generated))
        self.assertFalse(generated.is_symlink(), str(generated))
        if harness == "codex":
            metadata = tomllib.loads(generated.read_text(encoding="utf-8"))
            prompt = metadata["developer_instructions"]
            self.assertEqual(metadata["name"], name)
        else:
            metadata, prompt = read_markdown(generated)
            if harness == "claude-code":
                self.assertEqual(metadata["name"], name)
            else:
                self.assertEqual(generated.stem, name)
        self.assertEqual(metadata["description"], DESCRIPTION)
        # Delimiter padding is presentation; prompt text, indentation and spaces
        # inside the prompt must survive native serialization unchanged.
        self.assertEqual(prompt.strip("\n"), body.strip("\n"))
        self.assertNotIn("harnesses", metadata)
        return metadata

    def test_recursive_shared_round_trip_and_optional_codex_native_directory(self):
        case = self.installation()
        case.shared("nested/filename-is-not-the-identity.md")
        self.assertFalse(case.native_source("codex").exists())
        self.assert_success(case.run())
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                metadata = self.assert_agent(case, harness)
                if harness == "opencode":
                    self.assertEqual(metadata["mode"], "subagent")

    def test_codex_export_does_not_create_a_native_destination(self):
        case = self.installation()
        case.shared()
        self.assertFalse((case.home / ".codex").exists())
        self.assert_success(case.run())
        self.assert_agent(case, "codex")
        self.assertFalse((case.home / ".codex").exists())

    def test_codex_personal_files_links_and_native_sources_are_untouched(self):
        case = self.installation()
        case.shared()
        destination = case.destination("codex")
        destination.mkdir(parents=True)
        personal = destination / "reviewer.toml"
        personal.write_text(
            tomli_w.dumps(
                {
                    "name": "reviewer",
                    "description": "Personal, not managed.",
                    "developer_instructions": "Do not replace this personal agent.\n",
                }
            ),
            encoding="utf-8",
        )
        external = case.root / "personal-agent.toml"
        external.write_bytes(personal.read_bytes())
        (destination / "unowned.toml").symlink_to(external)
        (destination / "dangling.toml").symlink_to(case.root / "missing-personal")
        # This collides with the shared identity, but is not an input anymore.
        case.native("codex", "different.toml", name="reviewer")
        (case.native_source("codex") / "broken.toml").write_text("not = [valid")
        before_personal = snapshot(case.home / ".codex")
        before_source = snapshot(case.source)
        self.assert_success(case.run())
        self.assert_agent(case, "codex")
        self.assertEqual(snapshot(case.home / ".codex"), before_personal)
        self.assertEqual(snapshot(case.source), before_source)
        self.assertEqual(external.read_bytes(), personal.read_bytes())
        case.shared(body="The exported prompt changes, not personal agents.\n")
        self.assert_success(case.run())
        self.assert_agent(
            case, "codex", body="The exported prompt changes, not personal agents.\n"
        )
        self.assertEqual(snapshot(case.home / ".codex"), before_personal)

    def test_codex_prompt_export_is_readable_multiline_toml(self):
        case = self.installation()
        case.shared()
        self.assert_success(case.run())
        self.assert_agent(case, "codex")
        text = (case.generated("codex") / "reviewer.toml").read_text(encoding="utf-8")
        self.assertRegex(text, r'developer_instructions = ("{3}|\'{3})\r?\n')
        self.assertIn("Unicode: café, λ, 東京, 🦉.", text)

    def test_native_overrides_are_independent_and_explicit_mode_wins(self):
        case = self.installation()
        overrides = {
            "opencode": {
                "model": "provider/opencode-model",
                "mode": "all",
                "permission": {"edit": "deny"},
            },
            "claude-code": {
                "model": "sonnet",
                "tools": ["Read", "Grep"],
                "permissionMode": "plan",
            },
            "codex": {"model": "gpt-5.5", "model_reasoning_effort": "high"},
        }
        case.shared(harnesses=overrides)
        self.assert_success(case.run())
        for harness in HARNESSES:
            metadata = self.assert_agent(case, harness)
            for key, value in overrides[harness].items():
                self.assertEqual(metadata[key], value)
            for other in HARNESSES:
                for key in overrides[other].keys() - overrides[harness].keys():
                    self.assertNotIn(key, metadata)

    def test_empty_override_blocks_still_default_opencode_to_subagent(self):
        case = self.installation()
        case.shared(harnesses={harness: {} for harness in HARNESSES})
        self.assert_success(case.run())
        self.assertEqual(self.assert_agent(case, "opencode")["mode"], "subagent")

    def test_unknown_native_options_pass_through_without_schema_validation(self):
        case = self.installation()
        value = {"enabled": True, "labels": ["future", "café"]}
        case.shared(
            harnesses={
                harness: {"future_native_option": value} for harness in HARNESSES
            }
        )
        self.assert_success(case.run())
        for harness in HARNESSES:
            self.assertEqual(
                self.assert_agent(case, harness)["future_native_option"], value
            )

    def test_ignored_codex_sandbox_and_builtin_override_are_warned(self):
        case = self.installation()
        case.shared(name="explorer", harnesses={"codex": {"sandbox_mode": "read-only"}})
        result = case.run()
        self.assert_success(result)
        self.assertIn("sandbox_mode", result.stderr)
        self.assertIn("parent", result.stderr)
        self.assertIn("built-in", result.stderr)

    def test_rejects_unknown_common_fields_and_namespaces(self):
        for extra in (
            {"model": "a-common-model-is-not-portable"},
            {"unexpected": True},
            {"harnesses": {"claude": {}}},
            {"harnesses": {"open-code": {}}},
        ):
            with self.subTest(extra=extra):
                case = self.installation()
                case.shared("bad.md", **extra)
                before = case.outputs()
                self.assert_rejected(case.run(), mentions="bad.md")
                self.assertEqual(case.outputs(), before)

    def test_rejects_invalid_shared_names(self):
        for name in (
            "",
            "Reviewer",
            "two words",
            "../escape",
            "nested/name",
            "under_score",
            "-leading",
            "trailing-",
            "café",
            "a" * 257,
            42,
        ):
            with self.subTest(name=name):
                case = self.installation()
                case.shared("bad.md", name=name)
                self.assert_rejected(case.run(), mentions="bad.md")

    def test_rejects_missing_or_wrongly_typed_common_fields(self):
        for metadata in (
            {"description": DESCRIPTION},
            {"name": "reviewer"},
            {"name": "reviewer", "description": ["not", "text"]},
            {"name": "reviewer", "description": DESCRIPTION, "harnesses": []},
            {
                "name": "reviewer",
                "description": DESCRIPTION,
                "harnesses": {"codex": ["not", "a", "mapping"]},
            },
        ):
            with self.subTest(metadata=metadata):
                case = self.installation()
                write_markdown(case.source / "shared/agents/bad.md", metadata)
                self.assert_rejected(case.run(), mentions="bad.md")

    def test_rejects_duplicate_shared_identities_across_nested_files(self):
        case = self.installation()
        case.shared("first.md")
        case.shared("nested/second.md")
        before = case.outputs()
        self.assert_rejected(case.run(), mentions="reviewer")
        self.assertEqual(case.outputs(), before)

    def test_rejects_overrides_of_canonical_identity_description_and_prompt(self):
        for harness in HARNESSES:
            prompt_key = "developer_instructions" if harness == "codex" else "prompt"
            for key in ("name", "description", prompt_key):
                with self.subTest(harness=harness, key=key):
                    case = self.installation()
                    case.shared("bad.md", harnesses={harness: {key: "replacement"}})
                    self.assert_rejected(case.run(), mentions="bad.md")

    def test_rejects_duplicate_yaml_keys_at_every_frontmatter_depth(self):
        frontmatters = (
            "name: reviewer\nname: different\ndescription: Review changes.\n",
            "name: reviewer\ndescription: Review changes.\n"
            "harnesses:\n  codex: {}\n  codex: {}\n",
            "name: reviewer\ndescription: Review changes.\n"
            "harnesses:\n  opencode:\n    model: first\n    model: second\n",
        )
        for frontmatter in frontmatters:
            with self.subTest(frontmatter=frontmatter):
                case = self.installation()
                path = case.source / "shared/agents/bad.md"
                path.write_text(
                    "---\n" + frontmatter + "---\n" + BODY, encoding="utf-8"
                )
                self.assert_rejected(case.run(), mentions="bad.md")

    def test_invalid_input_preserves_last_successful_outputs(self):
        case = self.installation()
        case.shared("a-valid.md")
        case.native("claude-code")
        self.assert_success(case.run())
        before = case.outputs()
        case.shared("a-valid.md", body="An otherwise valid edit.\n")
        bad = case.source / "shared/agents/z-bad.md"
        bad.write_text("---\nname: [broken\n---\nBad edit.\n", encoding="utf-8")
        self.assert_rejected(case.run(), mentions="z-bad.md")
        self.assertEqual(case.outputs(), before)

    def test_toml_serialization_failure_preserves_all_last_successful_outputs(self):
        case = self.installation()
        case.shared("a-valid.md")
        self.assert_success(case.run())
        before = case.outputs()
        case.shared("a-valid.md", body="This must not be installed early.\n")
        # YAML null has no TOML representation. Native options are passed
        # through, but an unrepresentable value cannot partially update files.
        case.shared(
            "z-bad.md",
            name="unserializable",
            harnesses={"codex": {"future_native_option": None}},
        )
        self.assert_rejected(case.run())
        self.assertEqual(case.outputs(), before)

    def test_native_identity_collision_is_detected_despite_different_filename(self):
        for harness in ("claude-code",):
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                source = case.native(
                    harness,
                    case.filename(harness, "different-filename"),
                    name="reviewer",
                )
                original = source.read_bytes()
                before = case.outputs()
                self.assert_rejected(case.run(), mentions="reviewer")
                self.assertEqual(case.outputs(), before)
                self.assertEqual(source.read_bytes(), original)

    def test_native_source_destination_path_collision_is_rejected(self):
        case = self.installation()
        case.shared()
        case.native("opencode", "reviewer.md")
        before = case.outputs()
        self.assert_rejected(case.run(), mentions="reviewer")
        self.assertEqual(case.outputs(), before)

    def test_case_folded_native_source_filename_collision_is_rejected(self):
        case = self.installation()
        case.native("opencode", "Review.md")
        case.native("opencode", "review.md")
        paths = list(case.native_source("opencode").glob("*.md"))
        if len(paths) != 2:
            self.skipTest(
                "filesystem cannot represent simultaneous case-fold collisions"
            )
        before = case.outputs()
        self.assert_rejected(case.run())
        self.assertEqual(case.outputs(), before)

    def test_regular_destination_files_are_never_adopted_or_overwritten(self):
        for harness in INSTALLED_HARNESSES:
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                path = case.destination(harness) / case.filename(harness, "reviewer")
                path.parent.mkdir(parents=True)
                path.write_text(
                    "Host-local content; do not overwrite.\n", encoding="utf-8"
                )
                before = case.outputs()
                self.assert_rejected(case.run(), mentions="reviewer")
                self.assertEqual(case.outputs(), before)

    def test_unowned_destination_symlinks_including_dangling_links_are_not_replaced(
        self,
    ):
        for harness in INSTALLED_HARNESSES:
            for dangling in (False, True):
                with self.subTest(harness=harness, dangling=dangling):
                    case = self.installation()
                    case.shared()
                    target = case.root / "unmanaged-agent"
                    if not dangling:
                        target.write_text("Unmanaged content.\n", encoding="utf-8")
                    path = case.destination(harness) / case.filename(
                        harness, "reviewer"
                    )
                    path.parent.mkdir(parents=True)
                    path.symlink_to(target)
                    before = case.outputs()
                    self.assert_rejected(case.run(), mentions="reviewer")
                    self.assertEqual(case.outputs(), before)
                    if not dangling:
                        self.assertEqual(target.read_text(), "Unmanaged content.\n")

    def test_case_folded_existing_destination_filename_collision_is_rejected(self):
        for harness in INSTALLED_HARNESSES:
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                path = case.destination(harness) / case.filename(harness, "REVIEWER")
                path.parent.mkdir(parents=True)
                path.write_text("Case-insensitive conflict.\n", encoding="utf-8")
                before = case.outputs()
                self.assert_rejected(case.run())
                self.assertEqual(case.outputs(), before)

    def test_existing_destination_identity_collision_is_rejected(self):
        for harness in ("claude-code",):
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                source = case.native(harness, name="reviewer")
                path = case.destination(harness) / case.filename(
                    harness, "local-custom"
                )
                path.parent.mkdir(parents=True)
                path.write_bytes(source.read_bytes())
                source.unlink()
                before = case.outputs()
                self.assert_rejected(case.run(), mentions="reviewer")
                self.assertEqual(case.outputs(), before)

    def test_known_whole_directory_links_migrate_without_modifying_sources(self):
        case = self.installation()
        case.shared()
        sources = {}
        for harness in INSTALLED_HARNESSES:
            sources[harness] = case.native(harness)
            destination = case.destination(harness)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(
                case.native_source(harness), target_is_directory=True
            )
        before = snapshot(case.source)
        self.assert_success(case.run())
        self.assertEqual(snapshot(case.source), before)
        for harness in INSTALLED_HARNESSES:
            self.assertTrue(case.destination(harness).is_dir())
            self.assertFalse(case.destination(harness).is_symlink())
            self.assert_link(
                case.destination(harness) / sources[harness].name, sources[harness]
            )
            self.assert_agent(case, harness)

    def test_unknown_whole_directory_links_are_refused_without_touching_target(self):
        for harness in INSTALLED_HARNESSES:
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                source = case.native(harness)
                unknown = case.root / "unmanaged-directory"
                unknown.mkdir()
                (unknown / "keep.md").write_text("Keep me.\n", encoding="utf-8")
                destination = case.destination(harness)
                destination.parent.mkdir(parents=True)
                destination.symlink_to(unknown, target_is_directory=True)
                before_outputs = case.outputs()
                before_source = snapshot(case.source)
                before_unknown = snapshot(unknown)
                self.assert_rejected(case.run())
                self.assertEqual(case.outputs(), before_outputs)
                self.assertEqual(snapshot(case.source), before_source)
                self.assertEqual(snapshot(unknown), before_unknown)
                self.assertTrue(source.is_file())

    def test_nested_native_sources_are_linked_and_preserved(self):
        case = self.installation()
        case.shared()
        sources = {
            harness: case.native(
                harness, "nested/deeper/" + case.filename(harness, "native-reviewer")
            )
            for harness in INSTALLED_HARNESSES
        }
        before = snapshot(case.source)
        self.assert_success(case.run())
        self.assertEqual(snapshot(case.source), before)
        for harness, source in sources.items():
            links = [
                path
                for path in case.destination(harness).rglob("*")
                if path.is_symlink() and path.resolve() == source.resolve()
            ]
            self.assertEqual(len(links), 1, f"Missing or duplicated link to {source}")
            self.assert_agent(case, harness)

    def test_stale_owned_links_and_generated_files_removed_but_unrelated_entries_remain(
        self,
    ):
        case = self.installation()
        shared_source = case.shared()
        native_sources = {
            harness: case.native(harness) for harness in INSTALLED_HARNESSES
        }
        self.assert_success(case.run())
        preserved = {}
        for harness in INSTALLED_HARNESSES:
            destination = case.destination(harness)
            filename = case.filename(harness, "local-agent")
            local_file = destination / filename
            write_markdown(
                local_file, {"name": "local-agent", "description": "Keep local."}
            )
            external = case.root / harness / case.filename(harness, "unmanaged-link")
            external.parent.mkdir(parents=True)
            external.write_bytes(local_file.read_bytes())
            # Give the unowned linked agent a different native identity.
            if harness == "claude-code":
                write_markdown(
                    external, {"name": "unmanaged-link", "description": "Keep link."}
                )
            unowned_link = destination / external.name
            unowned_link.symlink_to(external)
            dangling_unowned = destination / case.filename(harness, "local-dangling")
            dangling_unowned.symlink_to(case.root / "never-managed" / harness)
            dangling_owned = destination / case.filename(harness, "owned-dangling")
            dangling_owned.symlink_to(
                os.path.relpath(
                    case.native_source(harness)
                    / case.filename(harness, "already-deleted"),
                    destination,
                )
            )
            preserved[harness] = (
                (local_file, local_file.read_bytes()),
                (unowned_link, os.readlink(unowned_link)),
                (dangling_unowned, os.readlink(dangling_unowned)),
                (external, external.read_bytes()),
            )
        shared_source.unlink()
        for source in native_sources.values():
            source.unlink()
        # An already-missing generated file makes its installed link dangling.
        (case.generated("opencode") / "reviewer.md").unlink()
        case.shared("replacement.md", name="replacement")
        self.assert_success(case.run())
        for harness in INSTALLED_HARNESSES:
            for stale_name in ("reviewer", "native-reviewer", "owned-dangling"):
                path = case.destination(harness) / case.filename(harness, stale_name)
                self.assertFalse(path.is_symlink() or path.exists(), str(path))
            self.assertFalse(
                (case.generated(harness) / case.filename(harness, "reviewer")).exists()
            )
            for path, expected in preserved[harness]:
                if isinstance(expected, bytes):
                    self.assertEqual(path.read_bytes(), expected)
                else:
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(os.readlink(path), expected)
            self.assert_agent(case, harness, name="replacement")
        self.assertFalse((case.generated("codex") / "reviewer.toml").exists())
        self.assert_agent(case, "codex", name="replacement")

    def test_repeated_sync_preserves_the_same_observable_installation(self):
        case = self.installation()
        case.shared()
        case.native("opencode")
        self.assert_success(case.run())
        before = case.outputs()
        self.assert_success(case.run())
        self.assertEqual(case.outputs(), before)

    def test_unrelated_claude_markdown_document_is_preserved(self):
        case = self.installation()
        case.shared()
        document = case.destination("claude-code") / "README.md"
        document.parent.mkdir(parents=True)
        document.write_text("Personal notes, not an agent.\n")
        self.assert_success(case.run())
        self.assertEqual(document.read_text(), "Personal notes, not an agent.\n")

    def test_invalid_native_markdown_is_rejected_before_installation(self):
        case = self.installation()
        case.shared()
        native = case.native("opencode")
        native.write_text("---\ndescription: [broken\n---\nPrompt\n")
        before = case.outputs()
        self.assert_rejected(case.run(), mentions=native.name)
        self.assertEqual(case.outputs(), before)

    def test_case_folded_destination_directory_collision_is_rejected(self):
        case = self.installation()
        case.shared()
        (case.destination("opencode") / "REVIEWER.md").mkdir(parents=True)
        before = case.outputs()
        self.assert_rejected(case.run(), mentions="REVIEWER.md")
        self.assertEqual(case.outputs(), before)

    def test_native_case_only_filename_rename_keeps_only_the_replacement_link(self):
        for harness in INSTALLED_HARNESSES:
            with self.subTest(harness=harness):
                case = self.installation()
                case.shared()
                source = case.native(harness, "Review.md", name="native-reviewer")
                self.assert_success(case.run())
                self.assert_link(case.destination(harness) / "Review.md", source)
                renamed = source.with_name("review.md")
                source.rename(renamed)
                self.assert_success(case.run())
                self.assert_link(case.destination(harness) / "review.md", renamed)
                names = [
                    path.name
                    for path in case.destination(harness).iterdir()
                    if path.name.casefold() == "review.md"
                ]
                self.assertEqual(names, ["review.md"])

    def test_checkout_alias_migrates_legacy_links_through_that_alias(self):
        case = self.installation()
        case.shared()
        alias = case.root / "checkout-alias"
        alias.symlink_to(case.source, target_is_directory=True)
        sources = {}
        for harness in INSTALLED_HARNESSES:
            sources[harness] = case.native(harness)
            destination = case.destination(harness)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(alias / harness / "agents", target_is_directory=True)
        before = snapshot(case.source)
        self.assert_success(case.run(source_root=alias))
        self.assertEqual(snapshot(case.source), before)
        for harness, source in sources.items():
            self.assertFalse(case.destination(harness).is_symlink())
            self.assert_link(case.destination(harness) / source.name, source)
            self.assert_agent(case, harness)

    def test_unclosed_native_frontmatter_preserves_prior_exports_and_links(self):
        for harness in INSTALLED_HARNESSES:
            for location in ("source", "destination"):
                with self.subTest(harness=harness, location=location):
                    case = self.installation()
                    case.shared()
                    self.assert_success(case.run())
                    root = (
                        case.native_source(harness)
                        if location == "source"
                        else case.destination(harness)
                    )
                    root.mkdir(parents=True, exist_ok=True)
                    malformed = root / "unclosed.md"
                    malformed.write_text(
                        "---\nname: native-reviewer\ndescription: No closing delimiter\n",
                        encoding="utf-8",
                    )
                    before = case.outputs()
                    case.shared(
                        body="Do not publish this edit while a native file is invalid.\n"
                    )
                    self.assert_rejected(case.run(), mentions="unclosed.md")
                    self.assertEqual(case.outputs(), before)

    def test_headerless_claude_source_readme_is_accepted_and_preserved(self):
        case = self.installation()
        case.shared()
        readme = case.native_source("claude-code") / "README.md"
        readme.parent.mkdir(parents=True)
        readme.write_text(
            "Personal documentation, not frontmatter.\n", encoding="utf-8"
        )
        self.assert_success(case.run())
        self.assert_link(case.destination("claude-code") / "README.md", readme)
        self.assertEqual(
            readme.read_text(), "Personal documentation, not frontmatter.\n"
        )

    def test_symlinked_home_ancestors_are_rejected_without_touching_the_target(self):
        for relative in (Path(".claude"), Path(".local/share/ai-tools/agents")):
            with self.subTest(ancestor=relative):
                case = self.installation()
                case.shared()
                self.assert_success(case.run())
                ancestor = case.home / relative
                target = case.root / "unmanaged-target"
                ancestor.rename(target)
                ancestor.symlink_to(target, target_is_directory=True)
                before_target = snapshot(target)
                before_outputs = case.outputs()
                case.shared(body="This must not reach a symlinked directory.\n")
                self.assert_rejected(case.run())
                self.assertEqual(snapshot(target), before_target)
                self.assertEqual(case.outputs(), before_outputs)
                self.assertTrue(ancestor.is_symlink())
                self.assertEqual(Path(os.readlink(ancestor)), target)


if __name__ == "__main__":
    unittest.main()
