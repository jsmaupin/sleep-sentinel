import errno
import unittest
from unittest.mock import patch

from sleep_sentinel import connection_error_detail


class NetworkDiagnosticTests(unittest.TestCase):
    def test_reports_wrapped_errno_without_sensitive_messages(self):
        error = RuntimeError("SECRET password or request")
        error.__cause__ = OSError(errno.EHOSTUNREACH, "SECRET host info")
        with patch("sleep_sentinel.sys.platform", "darwin"):
            detail = connection_error_detail(error)
        self.assertIn("EHOSTUNREACH", detail)
        self.assertIn("Local Network", detail)
        self.assertNotIn("SECRET", detail)
        self.assertNotIn("password", detail)

    def test_handles_exception_cycles(self):
        error = RuntimeError("SECRET")
        cause = OSError(errno.ECONNREFUSED, "SECRET")
        error.__cause__ = cause
        cause.__context__ = error
        detail = connection_error_detail(error)
        self.assertIn("ECONNREFUSED", detail)
        self.assertEqual(detail.count("RuntimeError"), 1)

    def test_no_mac_hint_on_windows(self):
        with patch("sleep_sentinel.sys.platform", "win32"):
            detail = connection_error_detail(OSError(errno.EACCES, "SECRET"))
        self.assertNotIn("macOS", detail)
        self.assertIn("EACCES", detail)

    def test_non_os_errors_report_type_only(self):
        self.assertEqual(
            connection_error_detail(RuntimeError("SECRET")), "RuntimeError"
        )
