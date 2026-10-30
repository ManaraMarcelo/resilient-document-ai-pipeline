import unittest
from uuid import uuid4

from models import TERMINAL_STATUSES, ExecutionResult, ExecutionStatus


class ExecutionResultTests(unittest.TestCase):
    def test_running_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionResult(execution_id=uuid4(), status=ExecutionStatus.RUNNING, attempts=1, duration_ms=10)

    def test_negative_counters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionResult(execution_id=uuid4(), status=ExecutionStatus.FAILED, attempts=-1, duration_ms=0)
        with self.assertRaises(ValueError):
            ExecutionResult(execution_id=uuid4(), status=ExecutionStatus.FAILED, attempts=0, duration_ms=-1)

    def test_terminal_statuses_exclude_running(self) -> None:
        self.assertNotIn(ExecutionStatus.RUNNING, TERMINAL_STATUSES)
        self.assertEqual(len(TERMINAL_STATUSES), len(ExecutionStatus) - 1)


if __name__ == "__main__":
    unittest.main()

