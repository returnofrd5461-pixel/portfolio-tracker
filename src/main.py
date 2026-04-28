import sys
import datetime
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


def run_pipeline() -> None:
    print("=" * 60)
    print("포트폴리오 파이프라인 시작")
    print("=" * 60)

    errors = []
    all_holdings = []

    # ── 1. 업비트 잔고 ──────────────────────────────────────────
    upbit_krw = 0.0
    print("\n[1/6] 업비트 잔고 조회...")
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
    print("\n[2/6] 시장 데이터 조회...")
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
    print("\n[3/6] OKX 잔고 조회...")
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
    print("\n[4/6] 한투 KIS 잔고 조회...")
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
    print("\n[5/6] 포트폴리오 집계...")
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

    # ── 6. 노션 업데이트 ─────────────────────────────────────────
    print("\n[6/6] 노션 동기화...")
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
