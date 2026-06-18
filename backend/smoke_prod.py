"""Non-destructive PRODUCTION smoke test against the LIVE public URL.

Hits the real deployed site over HTTPS with a real cookie jar to confirm the
end-to-end stack (TLS, same-origin /api proxy, cookie auth, refresh rotation,
logout, protected reads, health). It is **read-only**: it does NOT create data,
reset the password, or run the rate-limit burst (which could lock the admin out).

Usage:
    WOS_ADMIN_USER=admin WOS_ADMIN_PASSWORD=... \
        python smoke_prod.py https://your-app.vercel.app

Exit code 0 = all checks passed. Run from the frontend origin (Vercel) so the
/api rewrite + first-party cookies are exercised exactly as a browser would.
"""

from __future__ import annotations

import os
import sys

import httpx

_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WOS_BASE_URL", "")).rstrip("/")
    user = os.environ.get("WOS_ADMIN_USER", "admin")
    pw = os.environ.get("WOS_ADMIN_PASSWORD", "")
    if not base or not pw:
        print("Set the live base URL (argv[1] or WOS_BASE_URL) and WOS_ADMIN_PASSWORD.")
        return 2
    api = f"{base}/api/v1"
    print(f"== Smoke testing {base} (read-only) ==")

    # follow_redirects so an http->https redirect doesn't fail the call; verify TLS.
    with httpx.Client(base_url=base, timeout=20.0, follow_redirects=True) as c:
        r = c.get(f"{api}/health")
        check(r.status_code == 200, "GET /health -> 200", str(r.status_code))

        r = c.post(f"{api}/auth/login", json={"identifier": user, "password": pw})
        check(r.status_code == 200, "login -> 200", str(r.status_code))
        sc = " ".join(r.headers.get_list("set-cookie")).lower()
        check("wos_access=" in sc and "wos_refresh=" in sc, "session cookies set")
        check("httponly" in sc, "cookies HttpOnly")
        # Secure flag is expected when served over HTTPS in production.
        check(base.startswith("https") and "secure" in sc, "cookies Secure (HTTPS)")

        r = c.get(f"{api}/auth/me")
        check(r.status_code == 200, "/me via cookie -> 200", str(r.status_code))
        check(r.json().get("username") == user, "/me identifies admin")

        for path in ["/action-status", "/networth/summary", "/cash/summary",
                     "/cycle/state", "/execution/windows", "/ips", "/transactions"]:
            rr = c.get(f"{api}{path}")
            check(rr.status_code == 200, f"GET {path} -> 200", str(rr.status_code))

        old = c.cookies.get("wos_refresh")
        r = c.post(f"{api}/auth/refresh")
        check(r.status_code == 200, "refresh -> 200", str(r.status_code))
        check(old and c.cookies.get("wos_refresh") != old, "refresh token rotated")

        r = c.post(f"{api}/auth/logout")
        check(r.status_code == 200, "logout -> 200", str(r.status_code))
        r = c.get(f"{api}/auth/me")
        check(r.status_code == 401, "/me after logout -> 401", str(r.status_code))

    passed = sum(1 for ok, _ in _results if ok)
    total = len(_results)
    print(f"\n==== PROD SMOKE: {passed}/{total} passed ====")
    for ok, lbl in _results:
        if not ok:
            print(f"  - FAIL: {lbl}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
