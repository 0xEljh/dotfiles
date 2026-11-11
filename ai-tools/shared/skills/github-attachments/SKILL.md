---
name: github-attachments
description: Upload local screenshots as native GitHub PR or issue attachments with gh, without committing images.
---

# Publish GitHub screenshot attachments

Use `gh >= 2.99.0` with `--attach`. This workflow covers push-access
`github.com` repositories with user OAuth or PAT credentials, not App or
Actions tokens. Default to a new conversation comment.

## Prepare

Check the account and push access in the environment that will publish.
`GH_TOKEN` and `GITHUB_TOKEN` can override the stored login:

```sh
gh api --hostname github.com user --jq .login
gh api --hostname github.com repos/OWNER/REPO --jq .permissions.push
```

Preview the selected images on the publishing host; transfer remote files there
first. Supported images are PNG, JPEG, GIF, WebP, and SVG, up to 10 MiB each.

## Publish

Put the caption in a body file. Quote the complete `path#alt text` argument:

```sh
GH_HOST=github.com gh pr comment 123 --repo github.com/OWNER/REPO \
	--body-file '/absolute/evidence.md' \
	--attach '/absolute/screenshot.png#The corrected login error state'
```

Use `gh issue comment` for issues. Issue and PR `create` and `edit` also accept
`--attach`; line-specific review comments do not. Repeat the flag for multiple
images, up to 50 files. Unreferenced images are appended to the body.

Use an explicit repository. Edit an existing body or owned comment only when
that edit is requested; freshly read it and preserve unrelated content. With
`--edit-last`, confirm the latest owned comment is the intended target.

There is no upload-only or attachment dry-run mode. `--attach` conflicts with
`--web`, comment `--delete-last`, and `gh pr create --dry-run`.

## Verify and recover

Keep the command output, exit status, and post URL. Read back the published
body and check the caption and attachment references. PR conversation comments
use the REST endpoint `repos/OWNER/REPO/issues/comments/COMMENT_ID`, just like
issue comments. Retain canonical attachment URLs, not signed redirects.

Uploads and publication are not atomic: **a nonzero exit can still publish
some images**, and a failed publication can leave orphaned uploads. After an
error or timeout, reconcile the target before considering another write. Do
not automatically retry; report partial or unknown outcomes and get a decision
before retrying or posting missing images.

Deleting a comment does not guarantee attachment deletion. Public attachments
are unauthenticated; new private and internal attachments require repository
access. Do not assume expiry or revocation guarantees.

See the [GitHub CLI attachment guide](https://docs.github.com/en/github-cli/github-cli/attaching-files-with-github-cli)
for further details.
