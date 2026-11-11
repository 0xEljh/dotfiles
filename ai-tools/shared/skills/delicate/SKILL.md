---
name: delicate
description: Present proposed diffs for review before applying changes.
disable-model-invocation: true
---

The user wants you to be precise with the proposed changes. This is likely a
task where we don't want to accidentally regress surfaces unrelated to this
change or where deep focus on the intent is required. Exercise restraint in the
proposed change where applicable. This does not mean to skim on exploration or
to fail to be exhaustive about the surfaces that **should** be covered.

Propose changes as diffs in chat. Briefly explain anything that isn't obvious
from the diff.

The user will follow up with which diffs to apply or with further necessary
updates.
