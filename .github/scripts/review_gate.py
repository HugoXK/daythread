"""Require a PASS from the configured reviewer at the pull request tip.

The reviewer identity is separate from the author identity. The reviewer may
post a verdict in any of three places:

  - a pull request review (APPROVED, or a body with `Verdict: PASS`);
  - a pull request issue comment containing the head SHA and a PASS verdict;
  - a commit comment on the head SHA (the current HugoXK-bot behaviour).

The newest verdict at the head SHA is the one that counts. A new commit moves the
head, so an older PASS no longer applies. A REQUEST_CHANGES is a blocking
verdict even if a PASS was posted earlier. A verdict in a pull request issue
comment only counts when the SHA immediately after the verdict phrase is the
full 40-character head SHA (a short prefix or a missing SHA does not count).
A `COMMENTED` review or commit comment binds the SHA after the phrase the same
way when one is present; a verdict with no SHA counts only on its first
non-empty line.
Fenced code blocks and `>`-quoted lines are stripped before any path parses a
verdict.

A **T0 docs-only** pull request may be waived by policy when
`t0DocsAutoPass` is true and the reviewer has posted no verdict at all. An
explicit REQUEST_CHANGES still blocks; an explicit PASS still counts. Code, CI,
schema and vertical changes never qualify.

This script is read-only with respect to the pull request; it executes no pull
request code. It creates the required `review-gate` check run on the head SHA so
the gate also works for commit comments, which do not trigger a workflow.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
REVIEWER_FALLBACK = os.environ.get("REVIEWER_LOGIN", "HugoXK-bot")
CHECK_NAME = "review-gate"
VERDICT_RE = re.compile(
    r"(?:^|\n)\s*(?:Verdict\s*:\s*|##\s*Review\s*[—-]\s*)(PASS|REQUEST_CHANGES)\b",
    re.IGNORECASE,
)
SHA_AFTER_VERDICT_RE = re.compile(
    r"^\s*(?:at\s+)?`?([0-9a-fA-F]{7,})`?", re.IGNORECASE
)
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

DOC_FILES = {
    "README.md",
    "README.zh.md",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "AGENTS.md",
    "LESSONS.md",
}
DOC_PREFIXES = ("docs/", "changelog.d/")


def api(path: str, method: str = "GET", data: dict[str, Any] | None = None) -> Any:
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is required")
    request = urllib.request.Request(
        f"{API}/repos/{REPO}{path}",
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "latent-review-gate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"GitHub API {exc.code} {path}: {body}") from exc


def load_policy() -> dict[str, Any]:
    """Read the policy from the base branch checkout."""
    try:
        with open(".github/agent/review-policy.json", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def reviewer_login(policy: dict[str, Any] | None = None) -> str:
    policy = policy if policy is not None else load_policy()
    value = policy.get("requiredPass")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return REVIEWER_FALLBACK


def strip_noise(body: str) -> str:
    """Drop fenced code blocks and `>`-quoted lines before parsing verdicts."""
    kept = []
    fence = None
    for line in (body or "").splitlines():
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)[0]
                continue
            if line.lstrip().startswith(">"):
                continue
            kept.append(line)
        else:
            if match and match.group(1)[0] == fence:
                fence = None
            continue
    return "\n".join(kept)


def parse_verdict(body: str) -> str | None:
    match = VERDICT_RE.search(strip_noise(body))
    return match.group(1).upper() if match else None


def _verdict_context(stripped: str, match):
    line_end = stripped.find("\n", match.end())
    if line_end == -1:
        line_end = len(stripped)
    line_start = stripped.rfind("\n", 0, match.end()) + 1
    return stripped[line_start:line_end], stripped[match.end():line_end]


def _sha_after(after: str) -> str | None:
    sha = SHA_AFTER_VERDICT_RE.match(after)
    return sha.group(1) if sha else None


def _first_non_empty_line(stripped: str) -> str:
    for line in stripped.splitlines():
        if line.strip():
            return line
    return ""


def bound_verdict(body: str, head: str, *, require_sha: bool) -> str | None:
    """First verdict with an optional full-head SHA binding.

    A SHA right after the phrase must be the full 40-char head. A verdict with
    no SHA is accepted only when ``require_sha`` is false and the verdict line
    is the first non-empty line of the stripped body.
    """
    stripped = strip_noise(body)
    match = VERDICT_RE.search(stripped)
    if not match:
        return None
    verdict = match.group(1).upper()
    line, after = _verdict_context(stripped, match)
    sha = _sha_after(after)
    if sha is not None:
        if len(sha) == 40 and sha.lower() == head.lower():
            return verdict
        return None
    if require_sha:
        return None
    return verdict if line == _first_non_empty_line(stripped) else None


def is_docs_only(paths: list[str]) -> bool:
    return bool(paths) and all(
        path in DOC_FILES or path.startswith(DOC_PREFIXES) for path in paths
    )


def pull_files(number: int, expected: int) -> list[str]:
    files: list[dict[str, Any]] = []
    page = 1
    while len(files) < expected and page <= 30:
        batch = api(f"/pulls/{number}/files?per_page=100&page={page}")
        if not batch:
            break
        files.extend(batch)
        page += 1
    return [str(file.get("filename", "")) for file in files]


def event() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def pull_numbers(payload: dict[str, Any]) -> list[int]:
    name = os.environ.get("GITHUB_EVENT_NAME", "")
    if name in ("pull_request", "pull_request_review", "pull_request_target"):
        number = payload.get("pull_request", {}).get("number")
        return [int(number)] if number else []
    if name == "issue_comment":
        issue = payload.get("issue", {})
        if issue.get("pull_request") and issue.get("number"):
            return [int(issue["number"])]
        return []
    explicit = os.environ.get("PR") or payload.get("inputs", {}).get("pr")
    if explicit:
        return [int(explicit)]
    open_pulls = api("/pulls?state=open&per_page=100")
    return [int(pull["number"]) for pull in open_pulls]


def newest_verdict(pull: dict[str, Any], reviewer: str) -> tuple[bool | None, str]:
    number = int(pull["number"])
    head = str(pull["head"]["sha"])
    author = str(pull.get("user", {}).get("login", ""))
    if author == reviewer:
        return (
            False,
            f"{reviewer} is the pull request author; identities must be separate",
        )

    verdicts: list[tuple[str, str, str]] = []

    reviews = api(f"/pulls/{number}/reviews?per_page=100")
    for review in reviews:
        if str(review.get("user", {}).get("login", "")) != reviewer:
            continue
        if str(review.get("commit_id", "")) != head:
            continue
        state = str(review.get("state", "")).upper()
        stamp = str(review.get("submitted_at") or "")
        if state == "APPROVED":
            verdicts.append((stamp, "PASS", "approving review"))
        elif state == "CHANGES_REQUESTED":
            verdicts.append((stamp, "REQUEST_CHANGES", "changes-requested review"))
        elif state == "COMMENTED":
            verdict = bound_verdict(
                str(review.get("body") or ""), head, require_sha=False
            )
            if verdict:
                verdicts.append((stamp, verdict, "review comment"))

    comments = api(f"/issues/{number}/comments?per_page=100")
    for comment in comments:
        if str(comment.get("user", {}).get("login", "")) != reviewer:
            continue
        body = str(comment.get("body") or "")
        verdict = bound_verdict(body, head, require_sha=True)
        if verdict:
            verdicts.append(
                (str(comment.get("created_at") or ""), verdict, "pull request comment")
            )

    commit_comments = api(f"/commits/{head}/comments?per_page=100")
    for comment in commit_comments:
        if str(comment.get("user", {}).get("login", "")) != reviewer:
            continue
        verdict = bound_verdict(
            str(comment.get("body") or ""), head, require_sha=False
        )
        if verdict:
            verdicts.append(
                (str(comment.get("created_at") or ""), verdict, "commit comment")
            )

    if not verdicts:
        return None, f"no verdict from {reviewer} at {head[:7]}"

    verdicts.sort(key=lambda item: item[0])
    stamp, verdict, source = verdicts[-1]
    if verdict == "PASS":
        return True, f"{reviewer} PASS at {head[:7]} ({source}, {stamp})"
    return False, f"{reviewer} REQUEST_CHANGES at {head[:7]} ({source}, {stamp})"


def evaluate(
    pull: dict[str, Any], reviewer: str, auto_pass_t0_docs: bool
) -> tuple[bool, str]:
    head = str(pull["head"]["sha"])
    verdict, detail = newest_verdict(pull, reviewer)
    if verdict is not None:
        return verdict, detail
    if auto_pass_t0_docs:
        files = pull_files(int(pull["number"]), int(pull.get("changed_files") or 0))
        if is_docs_only(files):
            return True, f"T0 docs-only at {head[:7]}: reviewer waived by policy"
    return False, detail


def latest_check(head: str) -> dict[str, Any] | None:
    data = api(
        f"/commits/{head}/check-runs?check_name={CHECK_NAME}&filter=latest&per_page=100"
    )
    runs = data.get("check_runs", [])
    return runs[0] if runs else None


def publish_check(pull: dict[str, Any], ok: bool, detail: str) -> None:
    head = str(pull["head"]["sha"])
    conclusion = "success" if ok else "failure"
    current = latest_check(head)
    if (
        current
        and current.get("status") == "completed"
        and current.get("conclusion") == conclusion
    ):
        print(f"review-gate: {head[:7]} already {conclusion}")
        return
    api(
        "/check-runs",
        method="POST",
        data={
            "name": CHECK_NAME,
            "head_sha": head,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": f"review-gate: {'PASS' if ok else 'BLOCKED'}",
                "summary": detail,
            },
        },
    )
    print(f"review-gate: {head[:7]} {conclusion} — {detail}")


def main() -> None:
    policy = load_policy()
    reviewer = reviewer_login(policy)
    auto_pass_t0_docs = policy.get("t0DocsAutoPass") is True
    payload = event()
    numbers = pull_numbers(payload)
    if not numbers:
        print("review-gate: no open pull requests")
        return
    failures = 0
    for number in numbers:
        try:
            pull = api(f"/pulls/{number}")
            if pull.get("draft") is True:
                print(f"review-gate: #{number} is a draft; skipped")
                continue
            ok, detail = evaluate(pull, reviewer, auto_pass_t0_docs)
            publish_check(pull, ok, detail)
        except RuntimeError as error:
            failures += 1
            print(f"review-gate: #{number} ERROR — {error}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
