"""Run the URL codec/navigation contract in Node without a browser dependency."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DashboardStateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for dashboard state tests")
    def test_dashboard_url_and_navigation_contract(self):
        result = subprocess.run(
            ["node", "--test", str(Path(__file__).parent / "js" / "dashboard-state.test.cjs")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
