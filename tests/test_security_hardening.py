import os
import unittest
from unittest.mock import patch

import app


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


if __name__ == "__main__":
    unittest.main()
