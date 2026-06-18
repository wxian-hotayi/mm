"""Phase 4 live E2E + integration + auth-adversarial harness (in-process ASGI).

Speaks real HTTP to the FastAPI app via httpx ASGITransport (same mechanism as
the test suite) with a live cookie jar, so cookie auth / refresh rotation /
logout / reset / rate-limit are exercised end-to-end against the real
middleware, DB and services. No network port, no background server.

Run:  python qa_e2e_phase4.py   (env: SECRET_KEY, DATABASE_URL, ADMIN_*, RATE_LIMIT_ENABLED=true)
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from httpx import ASGITransport, AsyncClient

API = "/api/v1"
ADMIN_USER = "admin"
ADMIN_EMAIL = "admin@wealthos.test"
ADMIN_PW = "Admin12345!"
NEW_PW = "NewAdmin12345!"

_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _results.append((bool(cond), label))
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail else ""))


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


class _ResetCapture(logging.Handler):
    """Capture the raw password-reset token the service logs (no SMTP yet)."""

    token: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        tok = getattr(record, "reset_token", None)
        if tok:
            _ResetCapture.token = tok


async def main() -> int:
    from app.db.init_db import init_db
    from app.main import app
    from app.models.cash import CashAccountType, CashMovementType

    await init_db()
    # The app's "wealthos" logger sets propagate=False, so attach the capture
    # handler to that namespace (not the real root) to see the reset-token log.
    logging.getLogger("wealthos").addHandler(_ResetCapture())

    transport = ASGITransport(app=app)
    base = "http://testserver"

    async with AsyncClient(transport=transport, base_url=base) as c:
        # ----- 1. LOGIN (cookie session) -------------------------------------
        print("\n== 1. LOGIN ==")
        r = await c.post(f"{API}/auth/login",
                         json={"identifier": ADMIN_USER, "password": ADMIN_PW, "remember": True})
        check(r.status_code == 200, "login returns 200", str(r.status_code))
        sc = r.headers.get_list("set-cookie")
        check(any("wos_access=" in h for h in sc), "login sets wos_access cookie")
        check(any("wos_refresh=" in h for h in sc), "login sets wos_refresh cookie")
        check(any("httponly" in h.lower() for h in sc), "session cookies are HttpOnly")
        body = r.json()
        check(body.get("user", {}).get("username") == ADMIN_USER, "login body has user")

        # ----- 2. /me VIA COOKIE ONLY (no bearer header) ---------------------
        print("\n== 2. SESSION (cookie /me) ==")
        r = await c.get(f"{API}/auth/me")
        check(r.status_code == 200, "/me via cookie returns 200", str(r.status_code))
        check(r.json().get("username") == ADMIN_USER, "/me identifies admin")
        check("authorization" not in {k.lower() for k in c.headers}, "no bearer header used (pure cookie)")

        # ----- 3. PROTECTED PAGE READS (dashboard / portfolio / exec / settings)
        print("\n== 3. PAGE READS ==")
        for path in ["/action-status", "/networth/summary", "/cash/summary",
                     "/cycle/state", "/execution/windows", "/ips",
                     "/analytics/behavior", "/cash/accounts", "/deployment/queue",
                     "/execution/plans", "/transactions", "/health"]:
            r = await c.get(f"{API}{path}")
            check(r.status_code == 200, f"GET {path} -> 200", str(r.status_code))
        r = await c.post(f"{API}/portfolio/valuation",
                         json={"prices": {"VOO": 500, "QQQ": 480}, "fx_rate": 4.45})
        check(r.status_code == 200, "POST /portfolio/valuation -> 200", str(r.status_code))
        check(num(r.json().get("nav_usd")) == 0.0, "empty ledger -> nav_usd 0 (pass-through)")

        # ----- 4. MUTATION + FINANCIAL CORRECTNESS (cash buffer -> net worth) -
        print("\n== 4. MUTATION + FINANCIAL ==")
        r = await c.post(f"{API}/cash/accounts",
                         json={"name": "GXBank QA", "account_type": CashAccountType.GXBANK.value,
                               "is_buffer_source": True, "target_buffer_myr": 0})
        check(r.status_code in (200, 201), "create cash account", str(r.status_code))
        acct_id = r.json().get("id")
        r = await c.post(f"{API}/cash/movements",
                         json={"account_id": acct_id, "movement_type": CashMovementType.INFLOW.value,
                               "amount_myr": "5000.00", "movement_date": "2026-06-01"})
        check(r.status_code in (200, 201), "record RM5,000 inflow", str(r.status_code))
        r = await c.get(f"{API}/cash/summary")
        s = r.json()
        check(num(s.get("total_cash_myr")) == 5000.0, "cash summary total = 5000",
              str(s.get("total_cash_myr")))
        check(num(s.get("deployable_surplus_myr")) == 5000.0,
              "deployable surplus = 5000 (target 0)", str(s.get("deployable_surplus_myr")))
        # raise the buffer target -> deployable must drop by exactly the target
        r = await c.patch(f"{API}/cash/accounts/{acct_id}", json={"target_buffer_myr": 2000})
        check(r.status_code == 200, "patch target buffer = 2000", str(r.status_code))
        r = await c.get(f"{API}/cash/summary")
        s = r.json()
        check(num(s.get("deployable_surplus_myr")) == 3000.0,
              "deployable surplus = 3000 after RM2,000 buffer", str(s.get("deployable_surplus_myr")))
        check(num(s.get("total_cash_myr")) == 5000.0, "total cash still 5000 (buffer != balance)")
        # net worth must aggregate the cash leg consistently
        r = await c.get(f"{API}/networth/summary")
        nw = r.json()
        check(num(nw.get("cash_myr")) == 5000.0, "net worth cash_myr = 5000", str(nw.get("cash_myr")))
        check(num(nw.get("total_net_worth_myr")) == 5000.0,
              "net worth total = 5000 (cash only)", str(nw.get("total_net_worth_myr")))
        check(num(nw.get("deployable_surplus_myr")) == 3000.0,
              "net worth deployable surplus matches cash summary (3000)",
              str(nw.get("deployable_surplus_myr")))
        check(nw.get("portfolio", {}).get("priced") is False,
              "portfolio subset reports priced=False when no prices given")

        # ----- 5. REFRESH ROTATION + REUSE REJECTION -------------------------
        print("\n== 5. REFRESH ROTATION ==")
        old_refresh = c.cookies.get("wos_refresh")
        r = await c.post(f"{API}/auth/refresh")
        check(r.status_code == 200, "refresh returns 200", str(r.status_code))
        new_refresh = c.cookies.get("wos_refresh")
        check(old_refresh and new_refresh and old_refresh != new_refresh,
              "refresh token rotated (value changed)")
        # reuse the OLD (rotated-away) token on a clean client -> must be 401
        async with AsyncClient(transport=transport, base_url=base) as c2:
            c2.cookies.set("wos_refresh", old_refresh, domain="testserver", path="/api/v1/auth")
            r = await c2.post(f"{API}/auth/refresh")
            check(r.status_code == 401, "reused old refresh token rejected (401)", str(r.status_code))
        rotated_refresh = c.cookies.get("wos_refresh")  # capture before logout

        # ----- 6. LOGOUT INVALIDATION ----------------------------------------
        print("\n== 6. LOGOUT ==")
        r = await c.post(f"{API}/auth/logout")
        check(r.status_code == 200, "logout returns 200", str(r.status_code))
        r = await c.get(f"{API}/auth/me")
        check(r.status_code == 401, "/me after logout -> 401 (access cookie cleared)", str(r.status_code))
        async with AsyncClient(transport=transport, base_url=base) as c3:
            c3.cookies.set("wos_refresh", rotated_refresh, domain="testserver", path="/api/v1/auth")
            r = await c3.post(f"{API}/auth/refresh")
            check(r.status_code == 401, "refresh after logout -> 401 (session revoked)", str(r.status_code))

        # ----- 7. PASSWORD RESET (single-use) + session invalidation ---------
        print("\n== 7. PASSWORD RESET ==")
        r = await c.post(f"{API}/auth/password-reset/request", json={"email": ADMIN_EMAIL})
        check(r.status_code == 200, "reset request -> 200 (no enumeration)", str(r.status_code))
        r = await c.post(f"{API}/auth/password-reset/request", json={"email": "nobody@nowhere.test"})
        check(r.status_code == 200, "reset request for unknown email also 200", str(r.status_code))
        token = _ResetCapture.token
        check(bool(token), "reset token issued (captured from server log)")
        r = await c.post(f"{API}/auth/password-reset/confirm",
                         json={"token": token, "new_password": NEW_PW})
        check(r.status_code == 200, "reset confirm -> 200", str(r.status_code))
        r = await c.post(f"{API}/auth/password-reset/confirm",
                         json={"token": token, "new_password": NEW_PW})
        check(r.status_code == 401, "reset token reuse rejected (401, single-use)", str(r.status_code))
        r = await c.post(f"{API}/auth/login", json={"identifier": ADMIN_USER, "password": NEW_PW})
        check(r.status_code == 200, "login with new password works", str(r.status_code))
        r = await c.post(f"{API}/auth/login", json={"identifier": ADMIN_USER, "password": ADMIN_PW})
        check(r.status_code == 401, "login with old password fails", str(r.status_code))

        # ----- 8. RATE LIMIT (sensitive budget on /auth/login) ---------------
        print("\n== 8. RATE LIMIT ==")
        codes = []
        async with AsyncClient(transport=transport, base_url=base) as c4:
            for _ in range(15):
                rr = await c4.post(f"{API}/auth/login",
                                   json={"identifier": ADMIN_USER, "password": "wrong-pw"})
                codes.append(rr.status_code)
                if rr.status_code == 429:
                    last = rr
                    break
        check(429 in codes, "burst of logins triggers 429", f"codes={codes}")
        if 429 in codes:
            check(last.headers.get("Retry-After") is not None, "429 carries Retry-After header",
                  last.headers.get("Retry-After"))
            check(last.json().get("code") == "rate_limited", "429 body uses rate_limited code")

    passed = sum(1 for ok, _ in _results if ok)
    total = len(_results)
    print(f"\n==== E2E RESULT: {passed}/{total} checks passed ====")
    fails = [lbl for ok, lbl in _results if not ok]
    if fails:
        print("FAILURES:")
        for f in fails:
            print(f"  - {f}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
