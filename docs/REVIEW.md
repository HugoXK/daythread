# Review workflow

`main` is protected. A pull request merges only when:

- `ci-ok` is green (lint, tests, coverage, bench smoke);
- `review-gate` is green, meaning **`HugoXK-bot` posted a PASS at the current
  head SHA**.

The reviewer identity is separate from the author. `HugoXK-bot` may not PASS a
tip authored by `HugoXK-bot`. A new commit moves the head and voids an older PASS.

## Where a PASS can be posted

1. A PR review by `HugoXK-bot` with `APPROVED`.
2. A `COMMENTED` review whose body contains `Verdict: PASS`.
3. A PR issue comment containing the head SHA and `Verdict: PASS`.
4. A commit comment on the head SHA containing `Verdict: PASS`.
5. T0 docs-only auto-pass when every changed file is documentation and the
   reviewer posted no verdict. An explicit `REQUEST_CHANGES` still blocks.

`REQUEST_CHANGES` always blocks, even if an earlier PASS exists. The newest
verdict wins.

## What triggers the gate

`review-gate.yml` runs on PR open/update/reopen/review/comment, every 10 minutes
(commit comments do not trigger workflows), and manual dispatch. The script
checks out the **base** branch, reads `.github/agent/review-policy.json`, and
executes no PR code.

## Requesting review

`review-request.yml` assigns `HugoXK-bot` and posts a mention when an issue or PR
is opened. The bot accepts the invitation with write access and posts its review
from the bot account.

## Branch protection

Required contexts: `ci-ok`, `review-gate`. Enforce admins, no direct pushes, no
force pushes, no deletions, conversation resolution required.
