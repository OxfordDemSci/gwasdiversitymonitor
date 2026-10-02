"""Exercise browser provenance and stale-export behavior without browser dependencies."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DashboardProvenanceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for browser provenance tests")
    def test_browser_provenance_contract(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).parent / "js" / "dashboard-provenance.test.cjs")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
