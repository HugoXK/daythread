import stat
import tempfile
import unittest
from pathlib import Path

from daythread import crypto, mcp
from daythread.crypto import SecretVault
from daythread.db import connect, init_db
from daythread.errors import DaythreadError


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class CryptoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = connect(self.home / "db.sqlite")
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_encrypt_roundtrip(self):
        envelope = crypto.encrypt("passphrase", b"hello")
        self.assertEqual(crypto.decrypt("passphrase", envelope), b"hello")
        self.assertNotIn("hello", envelope["ciphertext"])

    def test_wrong_passphrase(self):
        envelope = crypto.encrypt("right", b"hello")
        with self.assertRaises(DaythreadError):
            crypto.decrypt("wrong", envelope)

    def test_master_key_permissions(self):
        crypto.load_or_create_master_key(self.home)
        mode = stat.S_IMODE((self.home / "master.key").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_secret_vault(self):
        vault = SecretVault(self.conn, self.home)
        vault.set("token", "super-secret")
        self.assertEqual(vault.get("token"), "super-secret")
        self.assertEqual(vault.names(), ["token"])
        row = self.conn.execute("SELECT ciphertext FROM secrets WHERE name = 'token'").fetchone()
        self.assertNotIn("super-secret", row["ciphertext"])
        vault.delete("token")
        self.assertEqual(vault.names(), [])

    def test_secret_env_resolution(self):
        vault = SecretVault(self.conn, self.home)
        vault.set("token", "from-vault")
        env = mcp._resolve_env(self.conn, {"TOKEN": "secret:token"}, self.home)
        self.assertEqual(env["TOKEN"], "from-vault")


if __name__ == "__main__":
    unittest.main()
