"""Smoke test: the fifty-file script (#24) still runs and explains everything."""

import contextlib
import io
import unittest

from scripts import run_fifty_files


class FiftyFilesScriptTest(unittest.TestCase):
    def test_every_outcome_is_explained(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            run_fifty_files.main()
        report = output.getvalue()
        self.assertIn("Files: 50", report)
        self.assertIn("Every outcome explained.", report)
        self.assertIn("timings are a floor", report)

    def test_percentile_is_nearest_rank(self) -> None:
        values = [float(v) for v in range(1, 21)]  # 1..20
        self.assertEqual(run_fifty_files.percentile(values, 0.95), 19.0)
        self.assertEqual(run_fifty_files.percentile(values, 0.5), 10.0)


if __name__ == "__main__":
    unittest.main()
