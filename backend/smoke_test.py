"""Live smoke test against a running WealthOS API on :8011. Standalone, deps-free (httpx)."""
import sys
import time
import httpx

BASE = "http://127.0.0.1:8011/api/v1"


def wait_for_server(timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=2.0)
            if r.status_code == 200:
                return r
        except Exception:
            time.sleep(0.5)
    raise SystemExit("server did not come up")


def main():
    results = []

    def check(name, cond, detail=""):
        results.append((name, cond, detail))
        print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")

    h = wait_for_server()
    check("health 200", h.status_code == 200, str(h.json()))

    # login
    r = httpx.post(f"{BASE}/auth/login", json={"identifier": "admin", "password": "Admin12345!"})
    check("login 200", r.status_code == 200, f"status={r.status_code}")
    body = r.json()
    token = body.get("token", {}).get("access_token") or body.get("access_token")
    check("login returns token", bool(token))
    hdr = {"Authorization": f"Bearer {token}"}

    # wrong password rejected
    rbad = httpx.post(f"{BASE}/auth/login", json={"identifier": "admin", "password": "wrong"})
    check("wrong password 401", rbad.status_code == 401, f"status={rbad.status_code}")

    # me requires auth
    rno = httpx.get(f"{BASE}/auth/me")
    check("me without token 401/403", rno.status_code in (401, 403), f"status={rno.status_code}")
    rme = httpx.get(f"{BASE}/auth/me", headers=hdr)
    check("me with token 200", rme.status_code == 200, rme.json().get("username", ""))

    # deposit RM13,350 @ 4.45 => $3,000
    dep = httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "DEPOSIT", "transaction_date": "2026-01-05",
        "total_amount_myr": 13350, "fx_rate_recorded": 4.45})
    check("deposit created", dep.status_code in (200, 201), f"status={dep.status_code} {dep.text[:200]}")

    # buy 2 VOO @ 470 fee 1
    buy = httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "BUY", "transaction_date": "2026-01-06", "asset_symbol": "voo",
        "quantity": 2, "unit_price_usd": 470, "fee_usd": 1, "fx_rate_recorded": 4.45})
    check("buy VOO created", buy.status_code in (200, 201), f"status={buy.status_code} {buy.text[:200]}")
    check("buy symbol uppercased", buy.json().get("asset_symbol") == "VOO", buy.json().get("asset_symbol", ""))

    # buy 1 QQQ @ 400 fee 1
    httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "BUY", "transaction_date": "2026-01-06", "asset_symbol": "QQQ",
        "quantity": 1, "unit_price_usd": 400, "fee_usd": 1, "fx_rate_recorded": 4.45})

    # oversell rejected
    oversell = httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "SELL", "transaction_date": "2026-01-07", "asset_symbol": "VOO",
        "quantity": 99, "unit_price_usd": 500, "fee_usd": 1, "fx_rate_recorded": 4.45})
    check("oversell 422", oversell.status_code == 422, f"status={oversell.status_code}")

    # future date rejected
    future = httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "DEPOSIT", "transaction_date": "2099-01-01",
        "total_amount_myr": 100, "fx_rate_recorded": 4.45})
    check("future date 422", future.status_code == 422, f"status={future.status_code}")

    # list transactions
    lst = httpx.get(f"{BASE}/transactions", headers=hdr)
    check("list 200", lst.status_code == 200)
    check("list paginated envelope", "items" in lst.json() and "total" in lst.json(),
          f"total={lst.json().get('total')}")

    # valuation
    val = httpx.post(f"{BASE}/portfolio/valuation", headers=hdr,
                     json={"prices": {"VOO": 520, "QQQ": 480}, "fx_rate": 4.40})
    check("valuation 200", val.status_code == 200, f"status={val.status_code} {val.text[:200]}")
    vj = val.json()
    check("valuation has nav_usd & nav_myr", "nav_usd" in vj and "nav_myr" in vj,
          f"nav_usd={vj.get('nav_usd')} nav_myr={vj.get('nav_myr')}")

    # valuation missing price -> 422
    valmiss = httpx.post(f"{BASE}/portfolio/valuation", headers=hdr,
                         json={"prices": {"VOO": 520}, "fx_rate": 4.40})
    check("valuation missing price 422", valmiss.status_code == 422, f"status={valmiss.status_code}")

    # rebalance
    reb = httpx.post(f"{BASE}/portfolio/rebalance", headers=hdr,
                     json={"prices": {"VOO": 520, "QQQ": 480}, "fx_rate": 4.40})
    check("rebalance 200", reb.status_code == 200, f"status={reb.status_code} {reb.text[:200]}")
    check("rebalance has status field", reb.json().get("status") in ("NO_ACTION", "CASH_ONLY", "SELL_REQUIRED"),
          reb.json().get("status", ""))

    # behavior
    beh = httpx.get(f"{BASE}/analytics/behavior", headers=hdr)
    check("behavior 200", beh.status_code == 200, f"status={beh.status_code} {beh.text[:200]}")
    check("behavior has flags", "flags" in beh.json())

    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n=== {len(results) - n_fail}/{len(results)} checks passed ===")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
