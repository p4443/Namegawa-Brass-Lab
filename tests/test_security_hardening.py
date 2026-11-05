import os
import unittest
from pathlib import Path
from unittest.mock import patch

import app


ROOT_DIR = Path(__file__).resolve().parents[1]


class SecurityHardeningTests(unittest.TestCase):
    def test_flask_debug_requires_explicit_true_value(self):
        false_values = ("", "0", "false", "no", "off", "unexpected")
        for value in false_values:
            with self.subTest(value=value), patch.dict(
                os.environ, {"FLASK_DEBUG": value}, clear=False
            ):
                self.assertFalse(app.environment_flag("FLASK_DEBUG"))

        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"FLASK_DEBUG": value}, clear=False
            ):
                self.assertTrue(app.environment_flag("FLASK_DEBUG"))

    def test_portal_security_headers_prevent_framing(self):
        config = (ROOT_DIR / "lesson-platform" / "next.config.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("frame-ancestors 'none'", config)
        self.assertIn('"X-Frame-Options", value: "DENY"', config)
        self.assertIn('"Strict-Transport-Security"', config)

    def test_portal_session_secret_has_minimum_strength(self):
        env_source = (
            ROOT_DIR / "lesson-platform" / "src" / "lib" / "env.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("minimumSessionSecretBytes = 32", env_source)
        self.assertIn("byteLength < minimumSessionSecretBytes", env_source)

    def test_webhook_auth_logs_no_secret_derived_metadata(self):
        route = (
            ROOT_DIR
            / "lesson-platform"
            / "src"
            / "app"
            / "api"
            / "webhooks"
            / "official-booking"
            / "reconcile"
            / "route.ts"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Fingerprint", route)
        self.assertNotIn("expectedLength", route)
        self.assertNotIn("actualLength", route)


if __name__ == "__main__":
    unittest.main()
