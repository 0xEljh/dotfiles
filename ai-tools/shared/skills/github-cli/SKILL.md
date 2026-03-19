---
name: github-cli
description: Browse GitHub issues, pull requests, repositories, forks, code, and CI runs with the gh CLI using read-only workflows.
---

# GitHub CLI (Read-Only)

Use this skill when you need strong GitHub exploration from a terminal without MCP.

## Scope

- Read-only workflows only.
- Do not create, edit, comment, approve, merge, close, reopen, or delete anything.
- Prefer structured output (`--json` and `--jq`) so results are easier to summarize.

## Preconditions

- `gh` is installed and authenticated.
- Use `-R OWNER/REPO` to query any repository without changing directories.

## Output and Retrieval Patterns

- Use `--limit` to control result volume (`30` default in many commands).
- Use `gh api ... --paginate` when exhaustive retrieval is required.
- Prefer machine-readable fields:
  - Issues/PRs: `number,title,state,author,labels,createdAt,updatedAt,url`
  - Repositories: `nameWithOwner,description,url,isFork,parent,stargazerCount,forkCount,updatedAt`
  - Checks/Runs: `name,state,conclusion,workflow,link,url`

## Issues

```bash
# List issues
gh issue list -R OWNER/REPO --state open --limit 50
gh issue list -R OWNER/REPO --state all --json number,title,state,author,labels,createdAt,updatedAt,url

# Search issues within a repo
gh issue list -R OWNER/REPO --search "is:issue label:bug sort:updated-desc" --limit 50

# View one issue
gh issue view 123 -R OWNER/REPO
gh issue view 123 -R OWNER/REPO --comments
gh issue view 123 -R OWNER/REPO --json number,title,body,state,author,labels,comments,createdAt,updatedAt,url

# Cross-repo issue search
gh search issues "memory leak" --owner ORG --state open --limit 50
gh search issues "repo:OWNER/REPO is:issue is:open label:bug" --json number,title,repository,state,createdAt,updatedAt,url

# Personal issue dashboard
gh issue status -R OWNER/REPO
```

## Pull Requests

```bash
# List pull requests
gh pr list -R OWNER/REPO --state open --limit 50
gh pr list -R OWNER/REPO --state all --json number,title,state,isDraft,author,labels,baseRefName,headRefName,reviewDecision,createdAt,updatedAt,url

# Search pull requests within a repo
gh pr list -R OWNER/REPO --search "is:pr review:required status:success" --limit 50

# View one pull request
gh pr view 123 -R OWNER/REPO
gh pr view 123 -R OWNER/REPO --comments
gh pr view 123 -R OWNER/REPO --json number,title,body,state,isDraft,author,labels,baseRefName,headRefName,additions,deletions,changedFiles,reviewDecision,mergeStateStatus,statusCheckRollup,createdAt,updatedAt,url

# Diff and changed files
gh pr diff 123 -R OWNER/REPO
gh pr diff 123 -R OWNER/REPO --name-only

# CI checks tied to PR
gh pr checks 123 -R OWNER/REPO
gh pr checks 123 -R OWNER/REPO --json name,state,conclusion,workflow,link

# Personal PR dashboard
gh pr status -R OWNER/REPO --conflict-status

# Cross-repo PR search
gh search prs "repo:OWNER/REPO is:open label:needs-review"
gh search prs "fix race condition" --owner ORG --state open --limit 50 --json number,title,repository,state,createdAt,updatedAt,url
```

## Repository and Fork Discovery

```bash
# View repository metadata
gh repo view OWNER/REPO
gh repo view OWNER/REPO --json name,nameWithOwner,description,url,defaultBranchRef,isFork,parent,stargazerCount,forkCount,primaryLanguage,languages,licenseInfo,isArchived,createdAt,updatedAt,pushedAt

# List repositories for owner/org
gh repo list OWNER --limit 100
gh repo list OWNER --source --no-archived --limit 100
gh repo list OWNER --fork --limit 100
gh repo list OWNER --json nameWithOwner,description,url,isFork,parent,stargazerCount,forkCount,updatedAt

# Search repositories globally
gh search repos "terminal UI" --language rust --stars ">100" --limit 50
gh search repos "topic:agent topic:cli" --limit 50 --json fullName,description,url,language,stargazersCount,forksCount,updatedAt

# Enumerate forks explicitly
gh api repos/OWNER/REPO/forks --paginate --jq '.[] | {full_name, html_url, stargazers_count, forks_count, updated_at}'
```

## Code and Commit Discovery

```bash
# Search code
gh search code "NewClient(" --repo OWNER/REPO --limit 50
gh search code "TODO" --owner ORG --extension ts --limit 50
gh search code "context cancellation" --language go --limit 50 --json path,repository,sha,url

# Search commits
gh search commits "fix flaky test" --repo OWNER/REPO --limit 50
gh search commits "repo:OWNER/REPO author:USERNAME" --limit 50 --json sha,author,repository,subject,updatedAt,url
```

## CI / Workflow Runs

```bash
# List workflow runs
gh run list -R OWNER/REPO --limit 30
gh run list -R OWNER/REPO --status failure --limit 30
gh run list -R OWNER/REPO --workflow CI --limit 30 --json databaseId,displayTitle,event,headBranch,status,conclusion,createdAt,updatedAt,url

# View one run (summary/logs)
gh run view RUN_ID -R OWNER/REPO
gh run view RUN_ID -R OWNER/REPO --json jobs,conclusion,createdAt,updatedAt,url
gh run view RUN_ID -R OWNER/REPO --log-failed
```

## Raw API Escape Hatch

Use `gh api` when top-level `gh` commands do not expose what you need.

```bash
# REST examples (read-only)
gh api repos/OWNER/REPO/issues --paginate
gh api repos/OWNER/REPO/issues/123/comments --paginate
gh api repos/OWNER/REPO/pulls/123/reviews --paginate
gh api repos/OWNER/REPO/pulls/123/comments --paginate
gh api repos/OWNER/REPO/compare/base...head
gh api repos/OWNER/REPO/contents/path/to/file

# GraphQL example
gh api graphql -F owner='OWNER' -F name='REPO' -f query='query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(first: 20, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { number title url updatedAt } } } }'
```

## Read-Only Guardrails

- Avoid mutating commands such as:
  - `gh issue create`, `gh issue edit`, `gh issue close`, `gh issue reopen`, `gh issue comment`
  - `gh pr create`, `gh pr edit`, `gh pr merge`, `gh pr close`, `gh pr reopen`, `gh pr comment`, `gh pr review`
  - `gh repo create`, `gh repo edit`, `gh repo fork` (fork creation is write)
  - `gh api -X POST`, `gh api -X PATCH`, `gh api -X PUT`, `gh api -X DELETE`

When uncertain, default to listing, viewing, diffing, searching, or API GET requests only.
