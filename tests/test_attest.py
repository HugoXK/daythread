import tempfile
import unittest
from pathlib import Path

from scripts.attest import (
    HAVE_ED25519,
    build_statement,
    sign_statement,
    verify_envelope,
    write_attestation,
)
from scripts.release import generate_signing_key


class AttestTest(unittest.TestCase):
    def test_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread.whl").write_bytes(b"wheel")
            (dist / "sbom.json").write_bytes(b"{}")
            statement = build_statement(dist, "abc")
            self.assertEqual(len(statement["subject"]), 2)
            self.assertEqual(statement["predicateType"], "https://slsa.dev/provenance/v1")

    @unittest.skipUnless(HAVE_ED25519, "cryptography is not installed")
    def test_sign_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread.whl").write_bytes(b"wheel")
            key = generate_signing_key(dist / "release.pem")
            statement = build_statement(dist, "abc")
            envelope = sign_statement(statement, key)
            self.assertTrue(verify_envelope(envelope))
            envelope["payload"] = envelope["payload"][:-4] + "AAAA"
            self.assertFalse(verify_envelope(envelope))

    @unittest.skipUnless(HAVE_ED25519, "cryptography is not installed")
    def test_write_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread.whl").write_bytes(b"wheel")
            key = generate_signing_key(dist / "release.pem")
            path = write_attestation(dist, key, "abc")
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
