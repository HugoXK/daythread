import tempfile
import unittest
from pathlib import Path

from scripts.release import (
    HAVE_ED25519,
    build_manifest,
    build_sbom,
    generate_signing_key,
    sign_manifest_ed25519,
    verify_manifest,
    verify_manifest_ed25519,
    write_release,
)


class ReleaseTest(unittest.TestCase):
    def test_sbom(self):
        sbom = build_sbom("1.2.3")
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["metadata"]["component"]["version"], "1.2.3")

    def test_manifest_and_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread-1.2.3.whl").write_bytes(b"wheel")
            (dist / "daythread-1.2.3.tar.gz").write_bytes(b"source")
            manifest = build_manifest(dist, "1.2.3", "abc123", key="release-key")
            self.assertEqual(len(manifest["files"]), 2)
            self.assertTrue(verify_manifest(manifest, "release-key"))
            self.assertFalse(verify_manifest(manifest, "wrong"))

    @unittest.skipUnless(HAVE_ED25519, "cryptography is not installed")
    def test_ed25519_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread-1.2.3.whl").write_bytes(b"wheel")
            key_path = generate_signing_key(dist / "release.pem")
            manifest = build_manifest(dist, "1.2.3", "abc123")
            signed = sign_manifest_ed25519(manifest, key_path)
            self.assertEqual(signed["signature_alg"], "ed25519")
            self.assertTrue(verify_manifest_ed25519(signed))
            signed["files"][0]["size"] = 999
            self.assertFalse(verify_manifest_ed25519(signed))

    def test_write_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread-1.2.3.whl").write_bytes(b"wheel")
            summary = write_release(dist, "1.2.3", key="release-key")
            self.assertEqual(summary["signed"], "true")
            import json

            manifest = json.loads(
                (dist / "release-manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["sbom_sha256"])
            self.assertTrue((dist / "sbom.json").exists())
            self.assertTrue((dist / "release-manifest.json").exists())
            self.assertTrue((dist / "SHA256SUMS").exists())


if __name__ == "__main__":
    unittest.main()
