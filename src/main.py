import sys
import json
import datetime
import pathlib
import traceback

sys.stdout.reconfigure(encoding="utf-8")

from upbit_api import fetch_balances as upbit_fetch
from okx_api import get_balance as okx_fetch
from kis_api import fetch_all_balances as kis_fetch
from market_data import get_prices
from notion_sync import sync_holdings, add_snapshot
from alerts import send_daily_report, send_manual_input_reminder


# ── 자산군 분류 ────────────────────────────────────────────────────────────────
CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC",
                  "LINK", "DOT", "USDT", "USDC", "BNB", "GAS"}
SAFE_TICKERS = {"IEF", "SGOV", "SHY", "BIL", "VMFXX"}  # 채권/MMF
COMMODITY_TICKERS = {"IAU", "GLD", "SLV", "USO", "UNG"}


def classify(ticker: str) -> str:
    if ticker in CRYPTO_TICKERS:
        return "crypto"
    if ticker in SAFE_TICKERS:
        return "bond"
    if ticker in COMMODITY_TICKERS:
        return "commodity"
    return "us_stock"


def is_risky(asset_class: str) -> bool:
    return asset_class in ("crypto", "us_stock", "commodity")


HOLDING_COLOR = {
    "QQQM": "#7F77DD", "AGIX": "#534AB7", "IAU": "#EF9F27",
    "IEF": "#378ADD",  "SHY": "#378ADD",  "SGOV": "#378ADD",
    "USO": "#D85A30",  "UNG": "#D85A30",
    "BTC": "#EF9F27",  "ETH": "#7F77DD",  "XRP": "#D4537E",
    "SOL": "#9945FF",
}
ACCOUNT_META = {
    "KIS_JONGHAP": {"id": "kis_jonghap", "name": "한투 종합 (직투ETF)",  "sub": "미국 직투 · 양도세 22%",           "color": "#534AB7"},
    "KIS_ISA":     {"id": "kis_isa",     "name": "ISA 중개형 (한투)",     "sub": "비과세 200만 · 초과 9.9%",          "color": "#7F77DD"},
    "KIS_YEON":    {"id": "kis_yeon",    "name": "연금저축펀드 (한투)",   "sub": "세액공제 16.5% · 과세이연",         "color": "#AFA9EC"},
    "UPBIT":       {"id": "upbit",       "name": "업비트",                "sub": "크립토 (BTC + ETH)",               "color": "#EF9F27"},
    "OKX":         {"id": "okx",         "name": "OKX",                   "sub": "크립토 해외",                      "color": "#D4853A"},
}
MANUAL_ACCOUNT_META = {
    "toss":      {"name": "토스증권 직투",  "sub": "NVDA 관련 · 양도세 22%",      "color": "#D4537E"},
    "emergency": {"name": "비상금",         "sub": "파킹통장 · 장기 유동자금",    "color": "#1D9E75"},
    "biz":       {"name": "사업 운영자금",  "sub": "월 단기 유동성",              "color": "#5DCAA5"},
}
CLS_COLORS = {
    "us_stock": "#7F77DD", "crypto": "#EF9F27",
    "bond": "#378ADD", "commodity": "#EF9F27", "cash": "#1D9E75",
}


def _holding_color(ticker: str, asset_class: str) -> str:
    return HOLDING_COLOR.get(ticker, CLS_COLORS.get(asset_class, "#888888"))


def write_data_json(
    all_holdings: list[dict],
    upbit_krw: float,
    okx_krw: float,
    class_totals: dict[str, float],
    usdkrw: float,
) -> None:
    """Build docs/data.json from pipeline results, preserving manual account values."""
    docs_dir = pathlib.Path(__file__).parent.parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    data_path = docs_dir / "data.json"

    # Load existing for manual accounts (toss, emergency, biz)
    existing = {}
    if data_path.exists():
        try:
            existing = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    manual_krw = {
        acc_id: next(
            (a["krw"] for a in existing.get("accounts", []) if a["id"] == acc_id), 0
        )
        for acc_id in MANUAL_ACCOUNT_META
    }

    # Per-account totals from holdings
    acct_totals: dict[str, float] = {}
    for h in all_holdings:
        acct_totals[h["account"]] = acct_totals.get(h["account"], 0) + h["eval_krw"]

    # KIS cash not in holdings — distribute remainder to jonghap as cash
    kis_holdings_sum = sum(v for k, v in acct_totals.items() if k.startswith("KIS_"))
    total_kis_from_api = sum(
        h["eval_krw"] for h in all_holdings if h["account"].startswith("KIS_")
    )

    # Build accounts list
    accounts = []
    for api_key, meta in ACCOUNT_META.items():
        krw = acct_totals.get(api_key, 0)
        if api_key == "UPBIT":
            krw = upbit_krw
        elif api_key == "OKX":
            krw = okx_krw
        accounts.append({"id": meta["id"], "name": meta["name"], "subtitle": meta["sub"],
                         "krw": round(krw), "color": meta["color"]})

    for acc_id, meta in MANUAL_ACCOUNT_META.items():
        accounts.append({"id": acc_id, "name": meta["name"], "subtitle": meta["sub"],
                         "krw": round(manual_krw.get(acc_id, 0)), "color": meta["color"]})

    total_krw = sum(a["krw"] for a in accounts)

    # Asset class totals — map commodity → gold/oil split preserved from existing if available
    cls_raw = {
        "us_stock": round(class_totals.get("us_stock", 0)),
        "crypto":   round(class_totals.get("crypto", 0)),
        "bond":     round(class_totals.get("bond", 0)),
        "gold":     round(class_totals.get("commodity", 0)),  # combined for now
        "oil":      0,
        "cash":     round(manual_krw.get("emergency", 0) + manual_krw.get("biz", 0)),
    }
    cls_raw["us_stock"] += round(manual_krw.get("toss", 0))

    # Build core holdings (KIS accounts)
    kis_account_map: dict[str, list] = {}
    for h in all_holdings:
        if not h["account"].startswith("KIS_"):
            continue
        kis_account_map.setdefault(h["account"], []).append(h)

    core_holdings = []
    for api_key in ["KIS_JONGHAP", "KIS_ISA", "KIS_YEON"]:
        meta = ACCOUNT_META[api_key]
        items_raw = kis_account_map.get(api_key, [])
        acct_total = acct_totals.get(api_key, 0)
        items = []
        for h in items_raw:
            p = round(h["eval_krw"] / acct_total * 100, 1) if acct_total else 0
            items.append({
                "ticker": h["ticker"], "name": h["name"],
                "cat": h.get("asset_class", "").replace("_", " "),
                "pct": p, "krw": round(h["eval_krw"]),
                "color": _holding_color(h["ticker"], h.get("asset_class", "")),
            })
        core_holdings.append({
            "account_id": meta["id"], "account_name": meta["name"],
            "account_sub": meta["sub"], "account_color": meta["color"],
            "total_krw": round(acct_total), "items": items,
        })

    # Build satellite holdings (upbit, okx)
    sat_map: dict[str, list] = {}
    for h in all_holdings:
        if h["account"] in ("UPBIT", "OKX"):
            sat_map.setdefault(h["account"], []).append(h)

    satellite_holdings = []
    for api_key in ["UPBIT", "OKX"]:
        meta = ACCOUNT_META[api_key]
        items_raw = sat_map.get(api_key, [])
        acct_total = upbit_krw if api_key == "UPBIT" else okx_krw
        items = []
        for h in items_raw:
            items.append({
                "ticker": h["ticker"], "name": h["name"],
                "cat": h.get("asset_class", ""),
                "krw": round(h["eval_krw"]),
                "color": _holding_color(h["ticker"], h.get("asset_class", "")),
            })
        satellite_holdings.append({
            "account_id": meta["id"], "account_name": meta["name"],
            "account_sub": meta["sub"], "account_color": meta["color"],
            "total_krw": round(acct_total), "items": items,
        })

    # Preserve toss satellite from existing
    existing_sat = existing.get("satellite_holdings", [])
    toss_sat = next((s for s in existing_sat if s.get("account_id") == "toss"), None)
    if toss_sat:
        toss_sat["total_krw"] = manual_krw.get("toss", toss_sat["total_krw"])
        satellite_holdings.append(toss_sat)

    # Liquidity metrics
    upbit_okx_toss = (upbit_krw + okx_krw + manual_krw.get("toss", 0)
                      + acct_totals.get("KIS_JONGHAP", 0))
    instant_cash = manual_krw.get("emergency", 0) + manual_krw.get("biz", 0)
    existing_metrics = existing.get("metrics", {})
    existing_tax = existing_metrics.get("tax", {})

    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds"),
        "usdkrw": round(usdkrw),
        "total_krw": total_krw,
        "accounts": accounts,
        "asset_class_totals": cls_raw,
        "core_holdings": core_holdings,
        "satellite_holdings": satellite_holdings,
        "metrics": {
            "usd_exposure_krw": round(acct_totals.get("KIS_JONGHAP", 0) + manual_krw.get("toss", 0)),
            "usd_exposure_note": "한투 종합 직투 + 토스",
            "liquidity": {
                "instant": round(instant_cash),
                "instant_note": "비상금 + 사업자금",
                "week": round(upbit_okx_toss),
                "week_note": "업비트 + OKX + 종합 + 토스",
                "mid": round(acct_totals.get("KIS_ISA", 0)),
                "mid_note": "ISA 만기 권장",
                "long": round(acct_totals.get("KIS_YEON", 0)),
                "long_note": "연금저축 (55세 락업)",
            },
            "tax": {
                "yeon_used": round(acct_totals.get("KIS_YEON", existing_tax.get("yeon_used", 0))),
                "yeon_limit": existing_tax.get("yeon_limit", 6000000),
                "isa_remaining": existing_tax.get("isa_remaining", 0),
                "isa_limit": existing_tax.get("isa_limit", 20000000),
            },
        },
    }

    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  docs/data.json 저장 완료 (총 자산: {total_krw:,.0f} KRW)")


def run_pipeline() -> None:
    print("=" * 60)
    print("포트폴리오 파이프라인 시작")
    print("=" * 60)

    errors = []
    all_holdings = []

    # ── 1. 업비트 잔고 ──────────────────────────────────────────
    upbit_krw = 0.0
    print("\n[1/7] 업비트 잔고 조회...")
    try:
        krw_cash, upbit_holdings = upbit_fetch()
        upbit_krw += krw_cash
        for h in upbit_holdings:
            upbit_krw += h["eval_krw"]
            all_holdings.append({
                "ticker": h["currency"],
                "name": h["currency"],
                "quantity": h["quantity"],
                "avg_price": h["avg_buy_price"],
                "current_price": h["current_price"],
                "eval_krw": h["eval_krw"],
                "profit_rate": h["profit_rate"],
                "account": "UPBIT",
                "asset_class": classify(h["currency"]),
            })
        print(f"  업비트 총액: {upbit_krw:,.0f} KRW")
    except Exception as e:
        errors.append(f"업비트: {e}")
        print(f"  [오류] {e}")

    # ── 2. 시세 조회 (환율 먼저 필요) ───────────────────────────
    print("\n[2/7] 시장 데이터 조회...")
    prices = {}
    usdkrw = 1380.0
    try:
        prices = get_prices()
        usdkrw = prices.get("USDKRW") or 1380.0
        print(f"  USD/KRW: {usdkrw:,.0f}")
    except Exception as e:
        errors.append(f"시세: {e}")
        print(f"  [오류] {e}")

    # ── 3. OKX 잔고 ─────────────────────────────────────────────
    okx_krw = 0.0
    print("\n[3/7] OKX 잔고 조회...")
    try:
        okx_holdings = okx_fetch()
        for h in okx_holdings:
            krw_val = h["usd_value"] * usdkrw
            okx_krw += krw_val
            all_holdings.append({
                "ticker": h["currency"],
                "name": h["currency"],
                "quantity": h["quantity"],
                "avg_price": 0,
                "current_price": h["usd_value"] / h["quantity"] if h["quantity"] else 0,
                "eval_krw": krw_val,
                "profit_rate": 0,
                "account": "OKX",
                "asset_class": classify(h["currency"]),
            })
        print(f"  OKX 총액: {okx_krw:,.0f} KRW")
    except Exception as e:
        errors.append(f"OKX: {e}")
        print(f"  [오류] {e}")

    # ── 4. 한투 잔고 ─────────────────────────────────────────────
    kis_krw = 0.0
    print("\n[4/7] 한투 KIS 잔고 조회...")
    try:
        kis_data = kis_fetch()
        for h in kis_data.get("overseas", []):
            krw_val = h["eval_usd"] * usdkrw
            kis_krw += krw_val
            all_holdings.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "quantity": h["quantity"],
                "avg_price": h["avg_price"] * usdkrw,
                "current_price": (h["eval_usd"] / h["quantity"] * usdkrw) if h["quantity"] else 0,
                "eval_krw": krw_val,
                "profit_rate": h["profit_rate"],
                "account": f"KIS_{h['account']}",
                "asset_class": classify(h["ticker"]),
            })
        for h in kis_data.get("domestic", []):
            kis_krw += h["eval_krw"]
            all_holdings.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "quantity": h["quantity"],
                "avg_price": h["avg_price"],
                "current_price": h["eval_krw"] / h["quantity"] if h["quantity"] else 0,
                "eval_krw": h["eval_krw"],
                "profit_rate": h["profit_rate"],
                "account": f"KIS_{h['account']}",
                "asset_class": classify(h["ticker"]),
            })
        print(f"  한투 총액: {kis_krw:,.0f} KRW")
    except Exception as e:
        errors.append(f"KIS: {e}")
        print(f"  [오류] {e}")

    # ── 5. 포트폴리오 지표 계산 ───────────────────────────────────
    print("\n[5/7] 포트폴리오 집계...")
    total_krw = upbit_krw + okx_krw + kis_krw

    class_totals: dict[str, float] = {}
    for h in all_holdings:
        cls = h["asset_class"]
        class_totals[cls] = class_totals.get(cls, 0) + h["eval_krw"]

    risky_krw = sum(v for cls, v in class_totals.items() if is_risky(cls))
    safe_krw = total_krw - risky_krw
    risky_pct = risky_krw / total_krw * 100 if total_krw else 0
    safe_pct = 100 - risky_pct

    asset_class_pct = {cls: v / total_krw * 100 for cls, v in class_totals.items()} if total_krw else {}

    print(f"  총 자산: {total_krw:,.0f} KRW")
    print(f"  위험자산: {risky_pct:.1f}% | 안전자산: {safe_pct:.1f}%")

    portfolio = {
        "total_krw": total_krw,
        "upbit_krw": upbit_krw,
        "okx_krw": okx_krw,
        "kis_krw": kis_krw,
        "risky_pct": risky_pct,
        "safe_pct": safe_pct,
        "usdkrw": usdkrw,
        "mdd": 0.0,
        "asset_classes": asset_class_pct,
        "asset_class_targets": {"crypto": 35.0, "us_stock": 25.0, "bond": 20.0, "commodity": 10.0},
    }

    # 업비트 BTC/ETH 현재가 (노션 스냅샷용)
    btc_holding = next((h for h in all_holdings if h["ticker"] == "BTC" and h["account"] == "UPBIT"), None)
    eth_holding = next((h for h in all_holdings if h["ticker"] == "ETH" and h["account"] == "UPBIT"), None)

    snapshot = {
        "total_krw": total_krw,
        "crypto_krw": class_totals.get("crypto", 0),
        "stock_krw": class_totals.get("us_stock", 0),
        "gold_krw": class_totals.get("commodity", 0),
        "oil_krw": 0,
        "bond_krw": class_totals.get("bond", 0),
        "cash_krw": upbit_krw - sum(h["eval_krw"] for h in all_holdings if h["account"] == "UPBIT"),
        "risky_pct": risky_pct,
        "safe_pct": safe_pct,
        "usdkrw": usdkrw,
        "mdd": 0.0,
        "daily_change_pct": 0.0,
        "btc_price": btc_holding["current_price"] if btc_holding else None,
        "eth_price": eth_holding["current_price"] if eth_holding else None,
    }

    # ── 6. docs/data.json 생성 ──────────────────────────────────
    print("\n[6/7] GitHub Pages 데이터 생성...")
    try:
        write_data_json(all_holdings, upbit_krw, okx_krw, class_totals, usdkrw)
    except Exception as e:
        errors.append(f"data.json: {e}")
        print(f"  [오류] {e}")

    # ── 7. 노션 업데이트 ─────────────────────────────────────────
    print("\n[7/7] 노션 동기화...")
    try:
        sync_holdings(all_holdings)
        add_snapshot(snapshot)
    except Exception as e:
        errors.append(f"노션: {e}")
        print(f"  [오류] {e}")

    # ── Slack 알림 ───────────────────────────────────────────────
    print("\n[알림] Slack 발송...")
    try:
        send_daily_report(portfolio)
        if datetime.date.today().weekday() == 0:  # 0 = 월요일
            send_manual_input_reminder()
    except Exception as e:
        errors.append(f"Slack: {e}")
        print(f"  [오류] {e}")

    # ── 최종 요약 ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("파이프라인 완료")
    print(f"  총 자산: {total_krw:,.0f} KRW")
    print(f"  종목 수: {len(all_holdings)}개")
    if errors:
        print(f"  오류 ({len(errors)}건):")
        for e in errors:
            print(f"    - {e}")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline()
