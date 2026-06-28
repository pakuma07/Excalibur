#!/usr/bin/env python3
"""
cert_expiry_check.py - check how many days until a TLS certificate expires, the
single highest-value TLS alert (an expired cert is a total, self-inflicted outage).

See: comp_networking/06_http_tls.md (TLS, PKI); enterprise_scenarios/
04_network_incidents.md (4.7 TLS incidents). The fix in prod is automated rotation
(ACME/cert-manager) PLUS alerting weeks ahead — this script is the alert.

Live mode connects, reads the peer cert's notAfter, and reports days remaining.
Self-test mode validates the date math offline (deterministic, no network).

Run (live, any OS w/ network):  python3 cert_expiry_check.py example.com [port]
Run (offline, any OS):          python3 cert_expiry_check.py --selftest
"""
from __future__ import annotations
import sys
import ssl
import socket
import time

WARN_DAYS = 30   # warn when fewer than this many days remain
CRIT_DAYS = 7    # critical


def cert_seconds(notafter: str) -> float:
    """Convert an X.509 notAfter string ('Jun  1 12:00:00 2026 GMT') to epoch."""
    return ssl.cert_time_to_seconds(notafter)


def days_until(notafter: str, now: float | None = None) -> float:
    now = time.time() if now is None else now
    return (cert_seconds(notafter) - now) / 86400.0


def severity(days: float) -> str:
    if days < 0:
        return "EXPIRED"
    if days < CRIT_DAYS:
        return "CRITICAL"
    if days < WARN_DAYS:
        return "WARNING"
    return "OK"


def fetch_notafter(host: str, port: int, timeout: float = 5.0) -> str:
    """Connect with TLS and return the peer certificate's notAfter string."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    return cert["notAfter"]


def check_live(host: str, port: int) -> None:
    print(f"Checking TLS certificate for {host}:{port} ...\n")
    try:
        notafter = fetch_notafter(host, port)
    except Exception as e:  # noqa: BLE001 - report any connection/TLS failure plainly
        print(f"  ERROR: could not retrieve certificate: {e}")
        print("  (offline or blocked? try: python3 cert_expiry_check.py --selftest)")
        return
    d = days_until(notafter)
    sev = severity(d)
    flag = "" if sev == "OK" else f"  <== {sev}"
    print(f"  notAfter      : {notafter}")
    print(f"  days remaining: {d:.1f}{flag}")
    if sev != "OK":
        print("\n  ACTION: rotate the cert; automate with ACME/cert-manager and "
              "alert >{0} days ahead.".format(WARN_DAYS))


def selftest() -> None:
    print("=== cert_expiry_check self-test (offline date math) ===")
    # Fixed reference 'now' so the test is deterministic.
    now = cert_seconds("Jan  1 00:00:00 2026 GMT")

    far = "Jun  1 00:00:00 2026 GMT"          # ~151 days out
    soon = "Jan 20 00:00:00 2026 GMT"          # 19 days out -> WARNING
    crit = "Jan  5 00:00:00 2026 GMT"          # 4 days out  -> CRITICAL
    past = "Dec 25 00:00:00 2025 GMT"          # already expired

    d_far = days_until(far, now)
    d_soon = days_until(soon, now)
    d_crit = days_until(crit, now)
    d_past = days_until(past, now)

    assert abs(d_far - 151) < 1.0, d_far
    assert abs(d_soon - 19) < 1.0, d_soon
    assert severity(d_far) == "OK"
    assert severity(d_soon) == "WARNING", d_soon
    assert severity(d_crit) == "CRITICAL", d_crit
    assert severity(d_past) == "EXPIRED", d_past

    for label, na in (("far", far), ("soon", soon), ("crit", crit), ("past", past)):
        d = days_until(na, now)
        print(f"  {label:<5} {na:<26} {d:7.1f} days -> {severity(d)}")
    print("\nAll assertions passed. OK")


def main() -> None:
    args = sys.argv[1:]
    if "--selftest" in args or not args:
        if not args:
            print("(no host given) running self-test; pass a hostname to check a "
                  "live cert.\n")
        selftest()
        return
    host = args[0]
    port = int(args[1]) if len(args) > 1 and args[1].isdigit() else 443
    check_live(host, port)


if __name__ == "__main__":
    main()
