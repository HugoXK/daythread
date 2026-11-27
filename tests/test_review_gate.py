from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

import review_gate

HEAD = "a" * 40


class FakeApi:
    def __init__(self, reviews=None, issue_comments=None, commit_comments=None, files=None):
        self.reviews = reviews or []
        self.issue_comments = issue_comments or []
        self.commit_comments = commit_comments or []
        self.files = files or []

    def __call__(self, path, method="GET", data=None):
        if "/files" in path:
            return self.files
        if "/pulls/" in path and "/reviews" in path:
            return self.reviews
        if "/issues/" in path and "/comments" in path:
            return self.issue_comments
        if "/commits/" in path and "/comments" in path:
            return self.commit_comments
        raise AssertionError("unexpected API path: {}".format(path))


def pull(author="HugoXK", changed_files=1):
    return {
        "number": 7,
        "user": {"login": author},
        "head": {"sha": HEAD},
        "changed_files": changed_files,
    }


class ReviewGateTest(unittest.TestCase):
    def test_verdict_lines(self):
        self.assertEqual(review_gate.parse_verdict("Verdict: PASS at " + HEAD), "PASS")
        self.assertEqual(
            review_gate.parse_verdict("Verdict: REQUEST_CHANGES at " + HEAD),
            "REQUEST_CHANGES",
        )
        self.assertEqual(review_gate.parse_verdict("## Review — PASS"), "PASS")
        self.assertIsNone(review_gate.parse_verdict("looks good"))

    def test_docs_only(self):
        self.assertTrue(
            review_gate.is_docs_only(["docs/review.md", "README.md", "CHANGELOG.md"])
        )
        self.assertFalse(review_gate.is_docs_only(["daythread/runtime.py"]))
        self.assertFalse(review_gate.is_docs_only([]))

    def test_t0_docs_auto_pass(self):
        fake = FakeApi(files=[{"filename": "docs/review.md"}])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", True)
        self.assertTrue(ok)
        self.assertIn("T0 docs-only", detail)

    def test_code_without_verdict_blocks(self):
        fake = FakeApi(files=[{"filename": "daythread/runtime.py"}])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", True)
        self.assertFalse(ok)

    def test_bot_cannot_pass_its_own_tip(self):
        fake = FakeApi()
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(author="HugoXK-bot"), "HugoXK-bot", False)
        self.assertFalse(ok)
        self.assertIn("identities must be separate", detail)

    def test_commit_comment_pass(self):
        fake = FakeApi(
            commit_comments=[
                {
                    "user": {"login": "HugoXK-bot"},
                    "body": "Verdict: PASS at " + HEAD,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertTrue(ok)
        self.assertIn("commit comment", detail)


if __name__ == "__main__":
    unittest.main()
