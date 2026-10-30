"""Check fixed public privacy boundaries without credentials or response bodies."""

import http.client
import sys
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


HOST = "namegawa-brass-lab.com"
TIMEOUT_SECONDS = 10
HEADER_NAMES = ("Cache-Control", "Pragma", "Expires", "Age", "CF-Cache-Status")


@dataclass(frozen=True)
class Probe:
    path: str
    expected_status: int
    no_store: bool = False
    private_api: bool = False


PROBES = (
    Probe("/health", 200, no_store=True),
    Probe("/api/editor", 401, no_store=True, private_api=True),
    Probe("/api/contracts", 401, no_store=True, private_api=True),
    Probe("/api/lesson-reservations", 401, no_store=True, private_api=True),
    Probe("/api/lesson-admin-health", 401, no_store=True, private_api=True),
    Probe("/api/store/health", 401, no_store=True, private_api=True),
    Probe("/data/contracts/__privacy_probe__.json", 404),
    Probe("/data/store.json", 404),
    Probe("/private/products/trumpet-metronome.zip", 404),
    Probe("/.env", 404),
    Probe("/.git/config", 404),
    Probe("/app.py", 404),
)


def fetch_headers(path: str) -> Tuple[int, Dict[str, Optional[str]]]:
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(
            "HEAD", path,
            headers={"User-Agent": "NamegawaPrivacyCheck/1.0", "Accept": "*/*"},
        )
        response = connection.getresponse()
        try:
            return response.status, {
                name: response.getheader(name) for name in HEADER_NAMES
            }
        finally:
            response.close()
    finally:
        connection.close()


def check_probe(probe: Probe) -> Tuple[str, ...]:
    try:
        status, headers = fetch_headers(probe.path)
    except (OSError, http.client.HTTPException):
        return ("NETWORK_OR_TLS_CHECK_FAILED",)

    failures = []
    if status != probe.expected_status:
        failures.append("UNEXPECTED_HTTP_STATUS")
    cache_control = {
        part.strip().lower() for part in (headers["Cache-Control"] or "").split(",")
    }
    if probe.no_store and "no-store" not in cache_control:
        failures.append("NO_STORE_MISSING")
    if probe.private_api:
        pragma = {
            part.strip().lower() for part in (headers["Pragma"] or "").split(",")
        }
        if "no-cache" not in pragma:
            failures.append("PRAGMA_NO_CACHE_MISSING")
        if (headers["Expires"] or "").strip() != "0":
            failures.append("EXPIRES_ZERO_MISSING")
    if probe.no_store:
        age = headers["Age"]
        if age is not None and age.strip() != "0":
            failures.append("CACHE_AGE_UNEXPECTED")
        if (headers["CF-Cache-Status"] or "").strip().upper() in {
            "HIT", "STALE", "UPDATING", "REVALIDATED",
        }:
            failures.append("CACHED_SENSITIVE_RESPONSE")
    return tuple(failures)


def main() -> int:
    print("Fixed HEAD-only privacy boundary checks: https://" + HOST)
    failed = 0
    for probe in PROBES:
        failures = check_probe(probe)
        if failures:
            failed += 1
            print("[FAIL] " + probe.path + ": " + ", ".join(failures))
        else:
            print("[PASS] " + probe.path)
    print("Checks: {}; failed: {}".format(len(PROBES), failed))
    print("Failures require investigation; passing does not prove absence of leaks.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
