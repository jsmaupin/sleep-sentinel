import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sleep_sentinel as app


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "local" / "credentials.json"
        location = patch.object(app, "credentials_path", return_value=self.path)
        location.start()
        self.addCleanup(location.stop)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def save(self, username="user@example.test", password="test password"):
        with (
            patch("builtins.input", return_value=username),
            patch.object(app.getpass, "getpass", return_value=password),
            patch("builtins.print"),
        ):
            app.save_credentials()

    def test_roundtrip_without_prompt_and_private_permissions(self):
        self.save(password='spaces " unicode é')
        with (
            patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
            patch.object(
                app.getpass, "getpass", side_effect=AssertionError("unexpected prompt")
            ),
        ):
            self.assertEqual(
                app.get_credentials(), ("user@example.test", 'spaces " unicode é')
            )
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_environment_overrides_saved_credentials(self):
        self.save()
        with patch.dict(
            os.environ, KASA_USERNAME="override", KASA_PASSWORD="env password"
        ):
            self.assertEqual(app.get_credentials(), ("override", "env password"))

    def test_missing_file_prompts(self):
        with (
            patch("builtins.input", return_value="new user"),
            patch.object(app.getpass, "getpass", return_value="new password"),
        ):
            self.assertEqual(app.get_credentials(), ("new user", "new password"))
        self.assertFalse(self.path.exists())

    def test_corrupt_file_does_not_expose_contents(self):
        self.save()
        self.path.write_text("{SECRET invalid json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--save-credentials") as caught:
            app.load_credentials()
        self.assertNotIn("SECRET", str(caught.exception))

    def test_blank_input_preserves_previous_credentials(self):
        self.save()
        with self.assertRaises(ValueError):
            self.save(password="")
        self.assertEqual(app.load_credentials()["password"], "test password")

    def test_failed_replace_preserves_file_and_cleans_temporary(self):
        self.save()
        with patch.object(app.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.save(password="replacement")
        self.assertEqual(app.load_credentials()["password"], "test password")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_and_forget(self):
        self.save()
        self.save(password="replacement")
        self.assertEqual(app.load_credentials()["password"], "replacement")
        with (
            patch("sys.argv", ["sleep_sentinel.py", "--forget-credentials"]),
            patch("builtins.print"),
        ):
            app.main()
            app.main()
        self.assertFalse(self.path.exists())

    def test_save_command_does_not_connect_or_read_config(self):
        with (
            patch("sys.argv", ["sleep_sentinel.py", "--save-credentials"]),
            patch.object(app, "save_credentials") as save,
            patch.object(app, "monitor") as monitor,
        ):
            app.main()
        save.assert_called_once()
        monitor.assert_not_called()
