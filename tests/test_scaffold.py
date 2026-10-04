import contextlib
import io
import subprocess
import sys
import unittest

import atlas_signal
from atlas_signal import cli


class ScaffoldTests(unittest.TestCase):
    def test_version_is_defined(self):
        self.assertIsInstance(atlas_signal.__version__, str)
        self.assertTrue(atlas_signal.__version__)

    def test_help_exits_cleanly(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("atlas_signal", out.getvalue())

    def test_module_entry_point_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "atlas_signal", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: atlas_signal", result.stdout)


if __name__ == "__main__":
    unittest.main()
