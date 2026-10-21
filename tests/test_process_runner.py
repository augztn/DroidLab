import subprocess
import sys
import threading
import unittest

from src.process_runner import ProcessCancelledError, run_streaming


class ProcessRunnerTests(unittest.TestCase):
    def test_streams_output_and_returns_exit_code(self):
        result = run_streaming(
            [sys.executable, "-c", "print('hello'); print('world')"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertIn("world", result.stdout)

    def test_timeout_terminates_quiet_process(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_streaming(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=0.2,
            )

    def test_cancel_before_launch_does_not_spawn_process(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ProcessCancelledError):
            run_streaming(
                [sys.executable, "-c", "print('should not run')"],
                cancel_event=cancelled,
            )

    def test_cancellation_terminates_quiet_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        try:
            with self.assertRaises(ProcessCancelledError):
                run_streaming(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    timeout=5,
                    cancel_event=cancelled,
                )
        finally:
            timer.cancel()

    def test_retained_output_is_bounded(self):
        result = run_streaming(
            [sys.executable, "-c", "print('x' * 2000)"],
            timeout=5,
            max_output_chars=100,
        )
        self.assertLess(len(result.stdout), 250)
        self.assertIn("additional output line(s) omitted", result.stdout)


if __name__ == "__main__":
    unittest.main()
