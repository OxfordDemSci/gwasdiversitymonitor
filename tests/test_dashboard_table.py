from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for chart table unit tests')
class DashboardTableTests(unittest.TestCase):
    def test_lazy_pagination_scope_invalidation_and_keyboard_dialog_controls(self):
        result = subprocess.run(['node', 'tests/js/dashboard-table.test.cjs'],
                                cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
