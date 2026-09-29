"""A bug that ends the worker (PRD 1, B5): one critical log line with the error's type
and nothing else, then exit code 1. Never the message or the traceback: a database
error's message can quote row values. No database or queue is needed.
"""

import unittest
from unittest import mock

from sqlalchemy.exc import IntegrityError

from kaagaz import worker_main
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

ROW_VALUE = "ABCDE1234F"  # made up; stands in for a value read from a document


class WorkerCrashTest(unittest.TestCase):
    def test_a_crash_is_logged_by_its_type_only_and_exits_with_1(self) -> None:
        error = IntegrityError(
            "INSERT INTO extracted_values (value_text) VALUES (%(v)s)",
            {"v": ROW_VALUE},
            Exception(f"violates check constraint\nDETAIL:  Failing row contains ({ROW_VALUE})."),
        )
        self.assertIn(ROW_VALUE, str(error))  # the row value is in the error itself

        with (
            mock.patch.object(Settings, "from_env", return_value=make_settings()),
            mock.patch.object(worker_main, "configure_logging"),
            mock.patch.object(worker_main, "make_engine", side_effect=error),
            self.assertLogs("kaagaz.worker", "DEBUG") as logs,
            self.assertRaises(SystemExit) as ended,
        ):
            worker_main.main()

        self.assertEqual(ended.exception.code, 1)
        (record,) = logs.records
        self.assertEqual(
            (record.levelname, record.getMessage(), record.__dict__["code"]),
            ("CRITICAL", "worker crashed", "IntegrityError"),
        )
        self.assertIsNone(record.exc_info)
        line = worker_main.JsonFormatter().format(record)
        self.assertNotIn(ROW_VALUE, line)
        self.assertNotIn("Traceback", line)


if __name__ == "__main__":
    unittest.main()
