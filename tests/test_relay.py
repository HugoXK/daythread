import tempfile
import unittest
from pathlib import Path

from daythread import crypto, memory, relay, sync
from daythread.db import connect, init_db
from daythread.errors import ProviderError


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home_a = self.root / "a"
        self.home_b = self.root / "b"
        self.conn_a = connect(self.home_a / "db.sqlite")
        init_db(self.conn_a)
        self.conn_b = connect(self.home_b / "db.sqlite")
        init_db(self.conn_b)
        memory.add_fact(self.conn_a, "person", "maya", "role", "co-founder")
        self.server = relay.RelayServer(
            self.root / "relay", token="relay-token", port=0
        ).start()

    def tearDown(self):
        self.server.stop()
        self.conn_a.close()
        self.conn_b.close()
        self.tmp.cleanup()

    def test_push_pull_roundtrip(self):
        pushed = sync.push(
            self.conn_a,
            self.home_a,
            "correct horse",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        self.assertEqual(pushed["device_id"], "device-a")
        remote = sync.list_remote(self.server.url, "relay-token")
        self.assertEqual(remote["bundles"][0]["device_id"], "device-a")

        counts = sync.pull(
            self.conn_b,
            self.home_b,
            "correct horse",
            self.server.url,
            "relay-token",
            remote_device="device-a",
        )
        self.assertEqual(counts["facts"], 1)
        self.assertEqual(len(memory.list_facts(self.conn_b)), 1)

        raw = (self.root / "relay" / "device-a.json").read_text(encoding="utf-8")
        self.assertNotIn("co-founder", raw)

    def test_unauthorized(self):
        with self.assertRaises(ProviderError):
            sync.push(
                self.conn_a,
                self.home_a,
                "pw",
                self.server.url,
                "wrong-token",
                remote_device="device-a",
            )

    def test_missing_bundle(self):
        with self.assertRaises(ProviderError):
            sync.pull(
                self.conn_b,
                self.home_b,
                "pw",
                self.server.url,
                "relay-token",
                remote_device="missing",
            )


if __name__ == "__main__":
    unittest.main()
