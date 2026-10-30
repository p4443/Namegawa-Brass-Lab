import http.client
import io
import ssl
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import privacy_check


def safe_headers():
    return {
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "Expires": "0",
        "Age": None,
        "CF-Cache-Status": "DYNAMIC",
    }


class PrivacyCheckTest(unittest.TestCase):
    def test_fixed_scope_has_only_canonical_paths(self):
        self.assertEqual(privacy_check.HOST, "namegawa-brass-lab.com")
        self.assertEqual(len(privacy_check.PROBES), 12)
        for probe in privacy_check.PROBES:
            self.assertTrue(probe.path.startswith("/"))
            self.assertNotIn("?", probe.path)
            self.assertNotIn("://", probe.path)
        self.assertEqual(sum(p.private_api for p in privacy_check.PROBES), 5)

    def test_transport_uses_head_tls_timeout_and_no_credentials_or_body_reads(self):
        with patch("privacy_check.http.client.HTTPSConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 302
            response.getheader.side_effect = safe_headers().get
            status, headers = privacy_check.fetch_headers("/api/contracts")
        factory.assert_called_once_with("namegawa-brass-lab.com", timeout=10)
        connection.request.assert_called_once_with(
            "HEAD", "/api/contracts",
            headers={"User-Agent": "NamegawaPrivacyCheck/1.0", "Accept": "*/*"},
        )
        self.assertEqual(status, 302)
        self.assertEqual(headers, safe_headers())
        response.read.assert_not_called()
        response.close.assert_called_once()
        connection.close.assert_called_once()
        connection.getresponse.assert_called_once()

    def test_network_failure_still_closes_connection(self):
        with patch("privacy_check.http.client.HTTPSConnection") as factory:
            connection = factory.return_value
            connection.request.side_effect = TimeoutError("synthetic private detail")
            with self.assertRaises(TimeoutError):
                privacy_check.fetch_headers("/health")
        connection.close.assert_called_once()

    def test_all_expected_statuses_and_headers_pass(self):
        for probe in privacy_check.PROBES:
            with self.subTest(path=probe.path), patch(
                "privacy_check.fetch_headers",
                return_value=(probe.expected_status, safe_headers()),
            ):
                self.assertEqual(privacy_check.check_probe(probe), ())

    def test_redirects_rate_limits_denials_and_server_errors_are_failures(self):
        probe = privacy_check.Probe("/api/contracts", 401, True, True)
        for status in (200, 204, 301, 302, 307, 308, 403, 404, 429, 500, 503):
            with self.subTest(status=status), patch(
                "privacy_check.fetch_headers", return_value=(status, safe_headers()),
            ):
                self.assertIn("UNEXPECTED_HTTP_STATUS", privacy_check.check_probe(probe))

    def test_nonpublic_file_must_remain_not_found(self):
        for probe in privacy_check.PROBES:
            if probe.expected_status != 404:
                continue
            with self.subTest(path=probe.path), patch(
                "privacy_check.fetch_headers", return_value=(200, safe_headers()),
            ):
                self.assertIn("UNEXPECTED_HTTP_STATUS", privacy_check.check_probe(probe))

    def test_private_api_requires_each_cache_header(self):
        probe = privacy_check.Probe("/api/contracts", 401, True, True)
        for header, code in (
            ("Cache-Control", "NO_STORE_MISSING"),
            ("Pragma", "PRAGMA_NO_CACHE_MISSING"),
            ("Expires", "EXPIRES_ZERO_MISSING"),
        ):
            headers = safe_headers()
            headers[header] = None
            with self.subTest(header=header), patch(
                "privacy_check.fetch_headers", return_value=(401, headers),
            ):
                self.assertIn(code, privacy_check.check_probe(probe))

    def test_similar_directive_names_do_not_count_as_no_store(self):
        headers = safe_headers()
        headers["Cache-Control"] = "x-no-store, no-store-fake"
        with patch("privacy_check.fetch_headers", return_value=(401, headers)):
            self.assertIn(
                "NO_STORE_MISSING",
                privacy_check.check_probe(privacy_check.PROBES[1]),
            )

    def test_case_insensitive_directives_and_zero_age_pass(self):
        headers = safe_headers()
        headers.update({"Cache-Control": "PRIVATE, NO-STORE", "Pragma": "NO-CACHE", "Age": "0"})
        with patch("privacy_check.fetch_headers", return_value=(401, headers)):
            self.assertEqual(privacy_check.check_probe(privacy_check.PROBES[1]), ())

    def test_sensitive_cache_hits_and_age_are_failures(self):
        probe = privacy_check.PROBES[1]
        for age in ("1", "300", "-1", "invalid"):
            headers = safe_headers()
            headers["Age"] = age
            with self.subTest(age=age), patch(
                "privacy_check.fetch_headers", return_value=(401, headers),
            ):
                self.assertIn("CACHE_AGE_UNEXPECTED", privacy_check.check_probe(probe))
        for status in ("HIT", "STALE", "UPDATING", "REVALIDATED"):
            headers = safe_headers()
            headers["CF-Cache-Status"] = status
            with self.subTest(cache=status), patch(
                "privacy_check.fetch_headers", return_value=(401, headers),
            ):
                self.assertIn("CACHED_SENSITIVE_RESPONSE", privacy_check.check_probe(probe))

    def test_transport_errors_are_explicit_and_never_log_provider_details(self):
        for error in (
            TimeoutError("private-student@example.com"),
            ssl.SSLError("synthetic-secret"),
            http.client.BadStatusLine("synthetic-private-body"),
        ):
            with self.subTest(error=type(error).__name__), patch(
                "privacy_check.fetch_headers", side_effect=error,
            ):
                self.assertEqual(
                    privacy_check.check_probe(privacy_check.PROBES[0]),
                    ("NETWORK_OR_TLS_CHECK_FAILED",),
                )

    def test_main_checks_every_path_and_exits_nonzero_on_any_failure(self):
        output = io.StringIO()
        results = [("NETWORK_OR_TLS_CHECK_FAILED",)] + [()] * 11
        with patch("privacy_check.check_probe", side_effect=results) as check, redirect_stdout(output):
            self.assertEqual(privacy_check.main(), 1)
        self.assertEqual(check.call_count, 12)
        self.assertIn("Checks: 12; failed: 1", output.getvalue())

    def test_main_returns_success_only_when_all_checks_pass(self):
        with patch("privacy_check.check_probe", return_value=()), redirect_stdout(io.StringIO()):
            self.assertEqual(privacy_check.main(), 0)

    def test_untrusted_headers_are_not_printed(self):
        headers = safe_headers()
        headers.update({
            "Cache-Control": "private-student@example.com",
            "Pragma": "synthetic-secret",
            "Age": "synthetic-private-body",
        })
        output = io.StringIO()
        with patch("privacy_check.fetch_headers", return_value=(401, headers)), redirect_stdout(output):
            self.assertEqual(privacy_check.main(), 1)
        for sensitive in ("private-student@example.com", "synthetic-secret", "synthetic-private-body"):
            self.assertNotIn(sensitive, output.getvalue())


if __name__ == "__main__":
    unittest.main()
