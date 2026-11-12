"""Maintenance tasks and the worker handoff, using normal OpenCode behavior."""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from .common import Blocked, canonical, check_secrets


SKILLS = {"deep_modules": "designing-deep-modules", "cleanup": "cleanup-basic"}
CONTRACTS = {
    "deep_modules": "Improve PR-related module interfaces where doing so makes callers simpler.",
    "cleanup": "Remove redundant PR-related tests, documentation, and comments.",
    "feedback": "Address the supplied review feedback, explaining the disposition of each item.",
    "conflicts": "Resolve the prepared merge conflicts, preserving the intent of both branches.",
}


def load_skills(stage: str, skills_dir: Path) -> tuple[dict, str]:
    name = SKILLS.get(stage)
    if not name:
        return {}, ""
    try:
        raw = (skills_dir / name / "SKILL.md").read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise Blocked("onboarding: required skill unavailable: " + name) from error
    return {name: hashlib.sha256(raw).hexdigest()}, text


def coding_prompt(spec: dict, diff: str, skill: str, artifacts: Path, helper: list[str]) -> str:
    feedback = canonical(spec.get("feedback"))
    context = canonical({key: spec["pr"].get(key) for key in ("title", "body")})
    check_secrets(diff)
    check_secrets(feedback)
    check_secrets(context)
    source = spec.get("dependency_source")
    dependency_hint = "Optional existing dependencies: " + canonical(source) + "\n" if source else ""
    check_secrets(dependency_hint)
    boundary = "CONTEXT_" + secrets.token_hex(16)
    return f"""Complete the {spec['stage']} stage for this PR: {CONTRACTS[spec['stage']]}
Use your normal OpenCode instructions, permissions, tools, and judgment.
Verify the work with appropriate checks.
Write the result file using the handoff below, including any real blockers.
For changes, commit and run the candidate command; the worker handles review and publication.

Handoff:
Result file: {artifacts / 'result.json'}
Result JSON: {{"outcome":"changed | no_change | blocked","summary":"brief outcome and verification","blockers":[],"verification_profile":[["command","arg"]],"verification":"checks and evidence","feedback_dispositions":[]}}
Blocked results need blockers with reason and next_action; completed results use an empty list.
Feedback dispositions cover every supplied item with id, url, disposition (fixed, already addressed, not applicable, or needs decision), and reason.
Configured verification commands: {canonical(spec.get('verification_profile'))}
When none are configured, changed results supply verification_profile as command argv arrays runnable from this checkout by the worker.
Candidate command (JSON argv, records the commit without pushing): {canonical(helper + ['publish', spec['attempt_id']])}

Attempt: {canonical({key: spec[key] for key in ('attempt_id', 'pr_id', 'input_head_sha', 'base_sha', 'stage')})}
PR refs: {canonical({side: {'repository_id': spec['pr'][side]['repo']['id'], 'ref': spec['pr'][side]['ref'], 'sha': spec['pr'][side]['sha']} for side in ('head', 'base')})}
{dependency_hint}
Stage skill:
{skill}

PR context (reference data):
<{boundary}>
PR TITLE AND DESCRIPTION:
{context}
FULL PR DIFF:
{diff}
FEEDBACK SNAPSHOT:
{feedback}
</{boundary}>
"""


def review_prompt(spec: dict, candidate: str, manifest_hash: str, manifest: dict,
                  diff: str, verification: dict, result: dict) -> str:
    check_secrets(diff)
    check_secrets(canonical(result))
    boundary = "CONTEXT_" + secrets.token_hex(16)
    return f"""Review candidate {candidate} without editing it.
Assess correctness, stage scope, deleted tests or documentation, and verification evidence.
Use your normal OpenCode instructions, permissions, tools, and judgment.
Return the JSON response below, approving only when no actionable findings remain and verification is adequate.

Stage: {spec['stage']}; task: {CONTRACTS[spec['stage']]}
Response: {{"decision":"approve or block","candidate_sha":"{candidate}","manifest_hash":"{manifest_hash}","findings":[]}}
Review evidence (reference data):
<{boundary}>
Candidate manifest: {canonical(manifest)}
Verification commands and results: {canonical(verification)}
Candidate diff (input head to candidate; + additions, - deletions):
{diff}
Agent result: {canonical(result)}
</{boundary}>
"""
