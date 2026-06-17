"""Live Phase-2 smoke test against a running WealthOS API on :8022."""
import sys
import time
import httpx

BASE = "http://127.0.0.1:8022/api/v1"


def wait_for_server(timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.5)
    raise SystemExit("server did not come up")


def main():
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")

    wait_for_server()
    r = httpx.post(f"{BASE}/auth/login", json={"identifier": "admin", "password": "Admin12345!"})
    token = r.json().get("token", {}).get("access_token") or r.json().get("access_token")
    hdr = {"Authorization": f"Bearer {token}"}
    check("login", r.status_code == 200 and bool(token))

    # --- Cash Buffer System ---
    acc = httpx.post(f"{BASE}/cash/accounts", headers=hdr, json={
        "name": "GXBank", "account_type": "GXBANK", "currency": "MYR",
        "is_buffer_source": True, "target_buffer_myr": 1000})
    check("create cash account", acc.status_code in (200, 201), f"{acc.status_code} {acc.text[:150]}")
    acc_id = acc.json().get("id")

    mv = httpx.post(f"{BASE}/cash/movements", headers=hdr, json={
        "account_id": acc_id, "movement_date": "2026-02-01",
        "movement_type": "INFLOW", "amount_myr": 6000})
    check("create inflow movement", mv.status_code in (200, 201), f"{mv.status_code} {mv.text[:150]}")

    cs = httpx.get(f"{BASE}/cash/summary", headers=hdr)
    csj = cs.json()
    check("cash summary 200", cs.status_code == 200, f"{cs.status_code}")
    # balance 6000, target buffer 1000 => deployable 5000
    dep = csj.get("deployable_surplus_myr")
    check("deployable surplus = 5000 (6000 - 1000 buffer)", str(dep) in ("5000.0", "5000", "5000.00", "5000.0000"),
          f"deployable={dep} total_cash={csj.get('total_cash_myr')}")

    # --- Net Worth aggregate (portfolio subset) ---
    # add a deposit + buy so portfolio has value
    httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "DEPOSIT", "transaction_date": "2026-02-02",
        "total_amount_myr": 4450, "fx_rate_recorded": 4.45})
    httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "BUY", "transaction_date": "2026-02-03", "asset_symbol": "VOO",
        "quantity": 1, "unit_price_usd": 470, "fee_usd": 1, "fx_rate_recorded": 4.45})
    nw = httpx.get(f"{BASE}/networth/summary", headers=hdr,
                   params={"voo_price": 500, "qqq_price": 450, "fx_rate": 4.45})
    # some impls take prices via body; tolerate either by also trying no-params
    if nw.status_code != 200:
        nw = httpx.get(f"{BASE}/networth/summary", headers=hdr)
    check("networth summary 200", nw.status_code == 200, f"{nw.status_code} {nw.text[:150]}")
    nwj = nw.json() if nw.status_code == 200 else {}
    check("networth has total + breakdown", "total_net_worth_myr" in nwj and "breakdown" in nwj,
          f"total={nwj.get('total_net_worth_myr')}")

    # --- IPS three-tier enforcement: forbidden symbol BLOCK ---
    block = httpx.post(f"{BASE}/transactions", headers=hdr, json={
        "transaction_type": "BUY", "transaction_date": "2026-02-04", "asset_symbol": "TSLA",
        "quantity": 1, "unit_price_usd": 200, "fee_usd": 1, "fx_rate_recorded": 4.45})
    check("forbidden-asset BUY blocked (422)", block.status_code == 422, f"{block.status_code} {block.text[:150]}")

    # --- IPS validate endpoint ---
    val = httpx.post(f"{BASE}/ips/validate", headers=hdr, json={
        "action": {"kind": "TRANSACTION", "transaction_type": "BUY", "asset_symbol": "TSLA",
                   "quantity": 1}})
    if val.status_code == 404:
        val = httpx.post(f"{BASE}/ips/validate", headers=hdr, json={
            "kind": "TRANSACTION", "transaction_type": "BUY", "asset_symbol": "TSLA", "quantity": 1})
    check("ips validate endpoint", val.status_code == 200, f"{val.status_code} {val.text[:150]}")

    comp = httpx.get(f"{BASE}/ips/compliance", headers=hdr)
    check("ips compliance score", comp.status_code == 200 and "score" in comp.json(),
          f"score={comp.json().get('score') if comp.status_code==200 else comp.status_code}")

    # --- Cycle state ---
    cy = httpx.get(f"{BASE}/cycle/state", headers=hdr)
    check("cycle state 200", cy.status_code == 200, f"{cy.status_code} state={cy.json().get('state') if cy.status_code==200 else ''}")

    # --- Action Status (THE output) ---
    ast = httpx.get(f"{BASE}/action-status", headers=hdr)
    astj = ast.json() if ast.status_code == 200 else {}
    check("action-status 200", ast.status_code == 200, f"{ast.status_code}")
    check("action-status in {DO_NOTHING,REVIEW_REQUIRED,REBALANCE_NOW}",
          astj.get("status") in ("DO_NOTHING", "REVIEW_REQUIRED", "REBALANCE_NOW"),
          f"status={astj.get('status')} reasons={len(astj.get('reasons', []))}")

    # --- Execution windows + plan ---
    win = httpx.get(f"{BASE}/execution/windows", headers=hdr)
    check("execution windows 200", win.status_code == 200,
          f"{win.status_code} next={win.json().get('next_window_date') if win.status_code==200 else ''}")
    plan = httpx.post(f"{BASE}/execution/plan", headers=hdr,
                      json={"prices": {"VOO": 500, "QQQ": 450}, "fx_rate": 4.45})
    planj = plan.json() if plan.status_code in (200, 201) else {}
    check("execution plan generated", plan.status_code in (200, 201), f"{plan.status_code} {plan.text[:150]}")
    check("plan has kind + orders", "plan_kind" in planj and "orders" in planj,
          f"kind={planj.get('plan_kind')} orders={len(planj.get('orders', []))}")

    # --- Deployment queue ---
    q = httpx.get(f"{BASE}/deployment/queue", headers=hdr)
    check("deployment queue 200", q.status_code == 200, f"{q.status_code}")

    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n=== {len(results) - n_fail}/{len(results)} checks passed ===")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
