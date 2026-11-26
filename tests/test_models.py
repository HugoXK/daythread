import json
import tempfile
import unittest
from pathlib import Path

from daythread import crypto, models
from daythread.db import connect, init_db, set_setting
from daythread.errors import BudgetExceededError, ProviderError, ValidationError


class FailingProvider:
    name = "test.fail"

    def complete(self, model, messages, temperature, max_tokens):
        raise RuntimeError("boom")


class ModelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_echo_provider(self):
        gateway = models.ModelGateway(self.conn)
        response = gateway.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(response.text, "echo: hello")
        self.assertEqual(response.provider, "local.echo")
        self.assertFalse(response.cached)

    @unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
    def test_cache_hit_is_encrypted(self):
        gateway = models.ModelGateway(self.conn, home=Path(self.tmp.name))
        messages = [{"role": "user", "content": "cached-secret"}]
        first = gateway.chat(messages)
        second = gateway.chat(messages)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        rows = self.conn.execute("SELECT cached FROM model_usage ORDER BY ts").fetchall()
        self.assertEqual([row["cached"] for row in rows], [0, 1])
        cached = self.conn.execute("SELECT response FROM model_cache").fetchone()["response"]
        self.assertNotIn("cached-secret", cached)

    def test_budget(self):
        set_setting(self.conn, "model.daily_token_budget", "1")
        gateway = models.ModelGateway(self.conn)
        gateway.chat([{"role": "user", "content": "one"}], use_cache=False)
        with self.assertRaises(BudgetExceededError):
            gateway.chat([{"role": "user", "content": "two"}], use_cache=False)

    def test_fallback(self):
        set_setting(self.conn, "model.provider", "test.fail")
        set_setting(self.conn, "model.fallback", "local.echo")
        gateway = models.ModelGateway(self.conn, {"test.fail": FailingProvider()})
        response = gateway.chat([{"role": "user", "content": "fallback"}])
        self.assertEqual(response.text, "echo: fallback")
        self.assertEqual(response.provider, "local.echo")

    def test_openai_provider_with_fake_transport(self):
        def opener(request):
            return json.dumps(
                {
                    "model": "fake-1",
                    "choices": [{"message": {"content": "hello from fake"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                }
            ).encode("utf-8")

        provider = models.OpenAICompatibleProvider(
            "https://example.invalid/v1", "test-key", opener=opener
        )
        response = provider.complete("fake-1", [{"role": "user", "content": "hi"}], 0.0, 16)
        self.assertEqual(response.text, "hello from fake")
        self.assertEqual(response.prompt_tokens, 3)
        self.assertEqual(response.completion_tokens, 4)

    def test_invalid_messages(self):
        gateway = models.ModelGateway(self.conn)
        with self.assertRaises(ValidationError):
            gateway.chat([])
        with self.assertRaises(ValidationError):
            gateway.chat([{"role": "invalid", "content": "x"}])

    def test_unknown_provider(self):
        set_setting(self.conn, "model.provider", "nope")
        gateway = models.ModelGateway(self.conn)
        with self.assertRaises(ProviderError):
            gateway.chat([{"role": "user", "content": "x"}])
        rows = self.conn.execute("SELECT ok FROM model_usage").fetchall()
        self.assertEqual([row["ok"] for row in rows], [0])

    def test_list_models(self):
        data = models.list_models(self.conn)
        self.assertEqual(data["config"]["provider"], "local.echo")
        self.assertIn("local.echo", data["providers"])


if __name__ == "__main__":
    unittest.main()
