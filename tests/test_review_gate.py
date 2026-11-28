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

    def _evaluate_issue_comment(self, body):
        fake = FakeApi(
            issue_comments=[
                {
                    "user": {"login": "HugoXK-bot"},
                    "body": body,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
        with patch.object(review_gate, "api", fake):
            return review_gate.evaluate(pull(), "HugoXK-bot", False)

    def test_issue_comment_old_sha_with_new_head_in_prose_is_red(self):
        old = "b" * 40
        body = "head moved to {head}; my earlier verdict is superseded:\n\nVerdict: PASS at {old}".format(
            head=HEAD, old=old
        )
        ok, detail = self._evaluate_issue_comment(body)
        self.assertFalse(ok, detail)

    def test_issue_comment_genuine_head_verdict_is_green(self):
        body = "Verdict: PASS at {head}\n\nhead is {head}".format(head=HEAD)
        ok, detail = self._evaluate_issue_comment(body)
        self.assertTrue(ok, detail)

    def test_issue_comment_fenced_old_verdict_is_red(self):
        old = "b" * 40
        body = "head moved to {head}\n```\nVerdict: PASS at {old}\n```".format(
            head=HEAD, old=old
        )
        ok, detail = self._evaluate_issue_comment(body)
        self.assertFalse(ok, detail)

    def test_issue_comment_seven_char_prefix_is_rejected(self):
        body = "Verdict: PASS at {short}\nhead is {head}".format(
            short=HEAD[:7], head=HEAD
        )
        ok, detail = self._evaluate_issue_comment(body)
        self.assertFalse(ok, detail)

    def test_issue_comment_without_sha_is_red(self):
        ok, detail = self._evaluate_issue_comment("Verdict: PASS\nhead is " + HEAD)
        self.assertFalse(ok, detail)

    def test_issue_comment_first_verdict_decides(self):
        old = "b" * 40
        first_old = "Verdict: PASS at {old}\n\nhead moved to {head}".format(
            old=old, head=HEAD
        )
        ok, detail = self._evaluate_issue_comment(first_old)
        self.assertFalse(ok, detail)

        first_head = "Verdict: PASS at {head}\n\nReplaces: earlier Verdict: PASS at {old}".format(
            head=HEAD, old=old
        )
        ok, detail = self._evaluate_issue_comment(first_head)
        self.assertTrue(ok, detail)

    def test_commented_review_unfenced_old_sha_is_red(self):
        old = "b" * 40
        review = {
            "user": {"login": "HugoXK-bot"},
            "commit_id": HEAD,
            "state": "COMMENTED",
            "body": "Verdict: PASS at {}\nhead is {}".format(old, HEAD),
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        fake = FakeApi(reviews=[review])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commented_review_earlier_old_sha_quote_blocks(self):
        old = "b" * 40
        review = {
            "user": {"login": "HugoXK-bot"},
            "commit_id": HEAD,
            "state": "COMMENTED",
            "body": "Earlier:\nVerdict: PASS at {}".format(old),
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        fake = FakeApi(reviews=[review])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commit_comment_earlier_old_sha_quote_blocks(self):
        old = "b" * 40
        fake = FakeApi(
            commit_comments=[
                {
                    "user": {"login": "HugoXK-bot"},
                    "body": "Earlier:\nVerdict: PASS at {}".format(old),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commented_review_no_sha_verdict_first_line_only(self):
        def review(body):
            return {
                "user": {"login": "HugoXK-bot"},
                "commit_id": HEAD,
                "state": "COMMENTED",
                "body": body,
                "submitted_at": "2026-01-01T00:00:00Z",
            }

        fake = FakeApi(reviews=[review("Verdict: PASS\nhead is " + HEAD)])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertTrue(ok, detail)

        fake = FakeApi(reviews=[review("head is {}\nVerdict: PASS".format(HEAD))])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commented_review_fenced_old_verdict_is_red(self):
        old = "b" * 40
        review = {
            "user": {"login": "HugoXK-bot"},
            "commit_id": HEAD,
            "state": "COMMENTED",
            "body": "```\nVerdict: PASS at {}\n```\nhead is {}".format(old, HEAD),
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        fake = FakeApi(reviews=[review])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commit_comment_fenced_old_verdict_is_red(self):
        old = "b" * 40
        fake = FakeApi(
            commit_comments=[
                {
                    "user": {"login": "HugoXK-bot"},
                    "body": "```\nVerdict: PASS at {}\n```\nhead is {}".format(
                        old, HEAD
                    ),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commit_comment_old_sha_is_red(self):
        old = "b" * 40
        fake = FakeApi(
            commit_comments=[
                {
                    "user": {"login": "HugoXK-bot"},
                    "body": "Verdict: PASS at {}\nhead is {}".format(old, HEAD),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commit_comment_no_sha_verdict_first_line_only(self):
        def comment(body):
            return {
                "user": {"login": "HugoXK-bot"},
                "body": body,
                "created_at": "2026-01-01T00:00:00Z",
            }

        fake = FakeApi(commit_comments=[comment("Verdict: PASS\nhead is " + HEAD)])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertTrue(ok, detail)

        fake = FakeApi(commit_comments=[comment("looks fine\n\nVerdict: PASS")])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_commented_review_no_sha_after_blank_line_is_red(self):
        review = {
            "user": {"login": "HugoXK-bot"},
            "commit_id": HEAD,
            "state": "COMMENTED",
            "body": "looks fine\n\nVerdict: PASS",
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        fake = FakeApi(reviews=[review])
        with patch.object(review_gate, "api", fake):
            ok, detail = review_gate.evaluate(pull(), "HugoXK-bot", False)
        self.assertFalse(ok, detail)

    def test_parse_verdict_ignores_fences_and_quotes(self):
        self.assertIsNone(
            review_gate.parse_verdict("```\nVerdict: PASS at {}\n```".format(HEAD))
        )
        self.assertIsNone(
            review_gate.parse_verdict("> Verdict: PASS at {}".format(HEAD))
        )
        self.assertEqual(review_gate.parse_verdict("Verdict: PASS"), "PASS")

    def test_inline_and_quoted_verdict_forms_are_already_non_parsing(self):
        old = "b" * 40
        self.assertIsNone(review_gate.parse_verdict("Earlier: Verdict: PASS at " + old))
        self.assertIsNone(review_gate.parse_verdict("> Verdict: PASS at " + old))


if __name__ == "__main__":
    unittest.main()
