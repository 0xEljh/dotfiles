# Install and update shared agents

## Qualify a host before enabling installation

1. Check that OpenCode is V2 and that OpenCode and Claude Code discover personal
   agents through per-file symlinks. On the qualified Linux host, both passed
   native delegation checks. macOS still needs its own qualification.
2. In the host's Home Manager configuration, enable installation:

   ```nix
   aiTools.sharedAgents.enable = true;
   ```

3. Rebuild and activate Home Manager. Activation runs `ai-agents-sync` and
   replaces only known repository-managed whole-directory agent links.
4. Start fresh native runtimes and delegate to `shared-reviewer`. Use
   `opencode --standalone` for an isolated fresh OpenCode runtime. A running
   shared OpenCode service can retain its catalog across sessions. Restarting
   that service is a separate, explicit action. Check prompt receipt and the
   selected model separately. An instruction not to edit files does not create
   a read-only sandbox.

Codex is export-only: this installed release discovers regular agent TOML but
ignores per-file symlinks. Sync never creates, changes, or cleans up
`~/.codex/agents/`. No Codex native-only source is installed.

Do not move an existing native agent into the shared directory just to resolve
a collision. First decide whether the definitions have the same role.

## Add a shared definition

Create a Markdown file in `ai-tools/shared/agents/`:

```markdown
---
name: example-reviewer
description: Reviews supplied changes for correctness.
harnesses:
  opencode:
    permissions:
      - action: edit
        resource: "*"
        effect: deny
  claude-code:
    disallowedTools: Write, Edit
  codex:
    features:
      shell_tool: false
---

Review the supplied changes. Report actionable defects with supporting evidence.
```

Use a lowercase kebab-case `name` of at most 250 characters, allowing for the
255-byte filesystem filename limit and `.toml` suffix. Every shared definition
produces three exports; OpenCode and Claude Code also receive native registration.
Omit `harnesses` or any native block to inherit
native defaults. OpenCode defaults to `mode: subagent`. Put model and effort
settings in the corresponding native block, using that harness's own syntax.

Do not override `name`, `description`, or prompt fields inside native blocks.
Native options pass through without translation or complete schema validation.
Warnings identify known ignored Codex settings, but do not prove that every
other setting takes effect. Set Codex permissions, providers, and MCP servers
on the parent session if you later install an export manually.

## Inspect a Codex export

After sync, open `~/.local/share/ai-tools/agents/codex/shared-reviewer.toml`.
The export is standalone TOML:

```toml
description = "Reviews supplied changes."
name = "shared-reviewer"
developer_instructions = """
Review the supplied changes.
Report actionable defects.
"""
```

The serializer preserves prompt line breaks, indentation, quotes, and
backslashes. It escapes TOML delimiters rather than rewriting the prompt.
Edits, renames, and deletions in shared sources update the exported files.
Treat the generated export directory as managed output, not as an authoring
directory. Export does not make the agent available to Codex automatically.

## Sync manually or enable automatic updates

Run `ai-agents-sync` after source edits. For a checkout without Home Manager,
build and run the packaged command:

```sh
nix build --no-link --print-out-paths path:./nixos-config#ai-agent-sync
```

Run `bin/ai-agents-sync` under the printed store path. Use `--source-root` to
select a different `ai-tools` tree and `--home` to select an isolated home for
testing. The default tree is `~/dotfiles/ai-tools`.

To opt into automatic updates, set this option and activate Home Manager:

```nix
aiTools.sharedAgents.watch.enable = true;
```

The watcher syncs on startup and queues another sync when sources change during
a render. It does not cancel running syncs. Manual sync and activation take the
same per-home lock. To run the watcher in a terminal, use `ai-agents-sync --watch`.

On Linux, inspect `journalctl --user -u ai-agent-sync`. On macOS, inspect
`~/.local/state/ai-tools/agent-sync.log`. Fix the reported source error and save
again. The watcher continues after failed syncs. If the watcher is unhealthy,
disable it and use manual sync.

Disabling installation stops synchronization but does not uninstall existing
agents. Legacy activation preserves real agent directories, including host-local
files. Remove unwanted owned links yourself if you also want to uninstall them.
Disabled activation uses the same command's `--seed-native` mode and takes the
same lock, so it cannot race a manual sync while creating legacy directory links.

## Resolve a failed sync

- For a name or path collision, rename one definition or explicitly move the old
  native definition out of the installation inputs. Sync does not pick a winner.
- For an unknown directory symlink or unrelated destination file, inspect and
  move the conflicting entry yourself. Sync does not adopt or overwrite it.
- For malformed YAML or a TOML serialization error, fix the source. Validation
  errors leave generated shared files and the installed link layout unchanged.
  Native-only prompts are live links, so their source edits are already visible.
- For an unreadable directory, restore its permissions and rerun sync. A failed
  inventory is an error, not an instruction to delete installed agents.
- For an I/O error, fix the reported filesystem problem and rerun sync. Files
  update atomically one at a time, not as a transaction across all harnesses.

Inspect generated shared files under `~/.local/share/ai-tools/agents/` and links
under `~/.config/opencode/agents/` and `~/.claude/agents/`.
Native-only links point to their authored files. Sync removes stale owned links,
but never deletes unrelated regular files in native agent directories. Successful
sync reports filesystem updates, not catalog reload. OpenCode may need a fresh
runtime before delegation sees an update.
