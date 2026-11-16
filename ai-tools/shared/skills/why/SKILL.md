---
name: why
description: "Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Returns a cited read on decisions and tradeoffs. Use how for runtime behavior."
disable-model-invocation: true
---

# Why

Answer from the record, not the code: read the commits and PR discussions that introduced the target and each later change, and other sources if the trail is thin. Use subagents to look up relevant design docs and conversation histories (usually just need to check user inputs). Cite what the authors wrote, mark your own inferences as inferences, and be clear (without excessively hedging or rambling) about what the record could not account for.
