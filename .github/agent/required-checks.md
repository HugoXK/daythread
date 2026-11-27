# Required checks

`main` requires a pull request and these contexts:

| Context | Workflow | What it proves |
|---|---|---|
| `ci-ok` | `ci.yml` | lint, tests, coverage gate and bench smoke passed at the tip |
| `review-gate` | `review-gate.yml` | `HugoXK-bot` posted a PASS at the pull request head SHA |

The review gate reads the policy from the **base** branch. A pull request cannot
weaken its own gate. The reviewer is separate from the author; `HugoXK-bot` may
never PASS a tip authored by `HugoXK-bot`.

Branch protection: require these contexts, `enforce_admins: true`, no direct
pushes, no force pushes, no deletions, conversation resolution required.
Documents-only (T0) pull requests may be auto-passed by policy.
