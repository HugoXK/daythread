import unittest

from scripts.build_sidecar import target_triple


class SidecarTest(unittest.TestCase):
    def test_target_triple(self):
        triple = target_triple()
        self.assertGreaterEqual(triple.count("-"), 2)
        self.assertNotIn(" ", triple)


if __name__ == "__main__":
    unittest.main()
