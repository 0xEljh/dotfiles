---
name: cleanup-basic
description: >-
  The lowest hanging fruit of any code/codebase cleanup:
  to strip out redundant doc and test surfaces.
---

This is to help identify files and surfaces for deletion.

- Delete all tautological tests
- Delete all doc surfaces that explain the code. Code should explain itself;
  every doc that attempts to explain the code only surfaces that needs to be
  maintained. Docs should contain what the code cannot itself explain. Docs that
  serve plainly as indexes that point to the code (and let's the code explain
  itself) are okay.
- Remove also all plans from git. These should not live in git.
