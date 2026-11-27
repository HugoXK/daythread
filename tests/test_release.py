import tempfile
import unittest
from pathlib import Path

from scripts.release import build_manifest, build_sbom, verify_manifest, write_release


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

    def test_write_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "daythread-1.2.3.whl").write_bytes(b"wheel")
            summary = write_release(dist, "1.2.3", key="release-key")
            self.assertEqual(summary["signed"], "true")
            self.assertTrue((dist / "sbom.json").exists())
            self.assertTrue((dist / "release-manifest.json").exists())
            self.assertTrue((dist / "SHA256SUMS").exists())


if __name__ == "__main__":
    unittest.main()
