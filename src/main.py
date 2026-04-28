import sys
import json
import datetime
import pathlib
import traceback

sys.stdout.reconfigure(encoding="utf-8")

from upbit_api import fetch_balances as upbit_fetch
from okx_api import get_balance as okx_fetch
from kis_api import fetch_all_balances as kis_fetch
from market_data import get_prices, get_market_data
from notion_sync import (
    sync_holdings, add_snapshot,
    pull_manual_holdings, get_yesterday_snapshot,
    get_capital_gains_ytd, get_sparkline_history, get_nvda_avg_price,
)
from alerts import send_daily_report, send_manual_input_reminder


# ── 자산군 분류 ────────────────────────────────────────────────────────────────
CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX", "MATIC",
                  "LINK", "DOT", "USDT", "USDC", "BNB", "GAS"}
SAFE_TICKERS = {"IEF", "SGOV", "SHY", "BIL", "VMFXX"}
GOLD_TICKERS  = {"IAU", "GLD", "SLV"}
OIL_TICKERS   = {"USO", "UNG"}
KOREAN_ETF_CLASS = {
    "426030": "us_stock",  # TIME 미국나스닥100액티브
    "456600": "us_stock",  # TIMEFOLIO 글로벌AI인공지능액티브
    "411060": "gold",      # ACE KRX금현물
    "308620": "bond",      # KODEX 미국채10년선물(H)
    "130680": "oil",       # TIGER 원유선물Enhanced(H)
    "0091C0": "bond",      # KODEX 미국10년국채액티브(H)
}


def classify(ticker: str) -> str:
    if ticker in CRYPTO_TICKERS:      return "crypto"
    if ticker in KOREAN_ETF_CLASS:    return KOREAN_ETF_CLASS[ticker]
    if ticker in SAFE_TICKERS:        return "bond"
    if ticker in GOLD_TICKERS:        return "gold"
    if ticker in OIL_TICKERS:         return "oil"
    return "us_stock"


def is_risky(asset_class: str) -> bool:
    return asset_class in ("crypto", "us_stock", "oil")


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
    "bond": "#378ADD", "gold": "#EF9F27", "oil": "#D85A30", "cash": "#1D9E75",
}
_CLS_NAMES = {
    "us_stock": "주식", "crypto": "크립토", "bond": "채권",
    "gold": "금", "oil": "원유", "cash": "현금",
}

GLOBAL_REBAL_TARGETS = {
    "us_stock": 30.0, "crypto": 35.0, "bond": 15.0,
    "gold": 10.0, "oil": 5.0, "cash": 5.0,
}
ACCOUNT_REBAL_TARGETS = {
    "kis_jonghap": {"us_stock": 50.0, "bond": 23.0, "gold": 22.0, "oil": 5.0},
    "kis_isa":     {"us_stock": 55.0, "bond": 20.0, "gold": 20.0, "oil": 5.0},
    "kis_yeon":    {"us_stock": 65.0, "bond": 15.0, "gold": 15.0, "oil": 5.0},
}


# 분할매수 기본값 (data.json에 없을 경우 초기화)
DEFAULT_SPLIT_BUYING = {
    "target_total": 30000000,
    "completed": 10300000,
    "next_date": "2026-06-01",
    "schedule": [
        {"round": 1, "date": "2026-04-27", "amount": 10300000, "status": "done"},
        {"round": 2, "date": "2026-06-01", "amount": 9850000,  "status": "pending"},
        {"round": 3, "date": "2026-08-01", "amount": 9850000,  "status": "pending"},
    ],
}


def _holding_color(ticker: str, asset_class: str) -> str:
    return HOLDING_COLOR.get(ticker, CLS_COLORS.get(asset_class, "#888888"))


def write_data_json(
    all_holdings: list[dict],
    upbit_krw: float,
    okx_krw: float,
    class_totals: dict[str, float],
    usdkrw: float,
    extras: dict | None = None,
) -> None:
    """Build docs/data.json from pipeline results."""
    extras = extras or {}
    docs_dir = pathlib.Path(__file__).parent.parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    data_path = docs_dir / "data.json"

    # Load existing for fallback values
    existing: dict = {}
    if data_path.exists():
        try:
            existing = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 수동 잔고: 노션 우선, 없으면 기존 data.json 값
    # notion_manual 구조: {"toss": {"eval": ..., "principal": ...}, "emergency": {"eval": ...}, ...}
    notion_manual = extras.get("notion_manual", {})
    manual_krw: dict[str, float] = {}
    for acc_id in MANUAL_ACCOUNT_META:
        acc_data = notion_manual.get(acc_id, {})
        notion_eval = acc_data.get("eval", 0) if isinstance(acc_data, dict) else 0
        if notion_eval > 0:
            manual_krw[acc_id] = notion_eval
        else:
            manual_krw[acc_id] = next(
                (a["krw"] for a in existing.get("accounts", []) if a["id"] == acc_id), 0
            )

    # Per-account totals from holdings
    acct_totals: dict[str, float] = {}
    for h in all_holdings:
        acct_totals[h["account"]] = acct_totals.get(h["account"], 0) + h["eval_krw"]

    # Add KIS cash to account totals
    kis_cash = extras.get("kis_cash", {})
    for api_key, cash in kis_cash.items():
        if cash > 0:
            acct_totals[api_key] = acct_totals.get(api_key, 0) + cash

    # Build accounts list
    accounts = []
    for api_key, meta in ACCOUNT_META.items():
        krw = acct_totals.get(api_key, 0)
        if api_key == "UPBIT":
            krw = upbit_krw
        elif api_key == "OKX":
            krw = okx_krw
        entry: dict = {"id": meta["id"], "name": meta["name"], "subtitle": meta["sub"],
                       "krw": round(krw), "color": meta["color"]}
        if api_key in ("KIS_JONGHAP", "OKX") and usdkrw:
            entry["usd_amount"] = round(krw / usdkrw)
        accounts.append(entry)

    for acc_id, meta in MANUAL_ACCOUNT_META.items():
        accounts.append({"id": acc_id, "name": meta["name"], "subtitle": meta["sub"],
                         "krw": round(manual_krw.get(acc_id, 0)), "color": meta["color"]})

    total_krw = sum(a["krw"] for a in accounts)

    # 업비트 KRW 현금 (코인 외 원화 잔고) — all_holdings에는 없어서 별도 계산
    upbit_crypto_sum = sum(h["eval_krw"] for h in all_holdings if h["account"] == "UPBIT")
    upbit_cash_krw = max(0.0, upbit_krw - upbit_crypto_sum)

    # Asset class totals
    kis_cash_total = sum(kis_cash.values())
    cls_raw = {
        "us_stock": round(class_totals.get("us_stock", 0) + manual_krw.get("toss", 0)),
        "crypto":   round(class_totals.get("crypto", 0)),
        "bond":     round(class_totals.get("bond", 0)),
        "gold":     round(class_totals.get("gold", 0)),
        "oil":      round(class_totals.get("oil", 0)),
        "cash":     round(manual_krw.get("emergency", 0) + manual_krw.get("biz", 0) + upbit_cash_krw + kis_cash_total),
    }

    # ── 일일 변동 계산 ────────────────────────────────────────────────
    risky_total = cls_raw["us_stock"] + cls_raw["crypto"] + cls_raw["oil"]
    safe_total = cls_raw["bond"] + cls_raw["gold"] + cls_raw["cash"]
    risky_pct = extras.get("risky_pct", risky_total / total_krw * 100 if total_krw else 0)
    safe_pct = extras.get("safe_pct", 100 - risky_pct)

    yesterday = extras.get("yesterday") or {}
    daily_change: dict = {}
    if yesterday:
        yest_total = yesterday.get("total_krw", 0)
        daily_change = {
            "total": {
                "today": total_krw,
                "yesterday": round(yest_total),
                "diff": round(total_krw - yest_total),
                "pct": round((total_krw - yest_total) / yest_total * 100, 2) if yest_total else 0,
            },
            "risky_pct": {
                "today": round(risky_pct, 1),
                "yesterday": round(yesterday.get("risky_pct", 0), 1),
                "diff": round(risky_pct - yesterday.get("risky_pct", 0), 1),
            },
            "safe_pct": {
                "today": round(safe_pct, 1),
                "yesterday": round(yesterday.get("safe_pct", 0), 1),
                "diff": round(safe_pct - yesterday.get("safe_pct", 0), 1),
            },
            "classes": {
                "us_stock": _cls_delta(cls_raw["us_stock"], yesterday.get("stock_krw", 0)),
                "crypto":   _cls_delta(cls_raw["crypto"],   yesterday.get("crypto_krw", 0)),
                "bond":     _cls_delta(cls_raw["bond"],     yesterday.get("bond_krw", 0)),
                "gold":     _cls_delta(cls_raw["gold"],     yesterday.get("gold_krw", 0)),
                "oil":      _cls_delta(cls_raw["oil"],      yesterday.get("oil_krw", 0)),
                "cash":     _cls_delta(cls_raw["cash"],     yesterday.get("cash_krw", 0)),
            },
        }

    # ── NVDA MDD ─────────────────────────────────────────────────────
    nvda_avg_krw = extras.get("nvda_avg_krw", 0)
    nvda_current_krw = extras.get("nvda_current_krw", 0)
    nvda_mdd: dict = {}
    if nvda_avg_krw > 0:
        mdd_pct = (nvda_current_krw - nvda_avg_krw) / nvda_avg_krw * 100
        nvda_mdd = {
            "avg_price_krw": round(nvda_avg_krw),
            "current_price_krw": round(nvda_current_krw),
            "mdd_pct": round(mdd_pct, 1),
            "threshold_15": -15,
            "threshold_25": -25,
        }

    # ── 토스 NVDA 손익 ───────────────────────────────────────────────
    toss_data = notion_manual.get("toss", {})
    toss_nvda: dict = {}
    if isinstance(toss_data, dict) and toss_data.get("principal", 0) > 0:
        toss_nvda = {
            "principal_krw": toss_data["principal"],
            "current_krw":   toss_data.get("eval", 0),
            "pnl_krw":       toss_data.get("pnl", 0),
            "pnl_pct":       toss_data.get("pnl_pct", 0.0),
        }

    # ── 양도세 면세 한도 ─────────────────────────────────────────────
    capital_gains_raw = extras.get("capital_gains", 0)
    cap_limit = 2500000
    capital_gains = {
        "ytd_gain": round(capital_gains_raw),
        "limit": cap_limit,
        "used_pct": round(min(capital_gains_raw / cap_limit * 100, 100), 1) if cap_limit else 0,
    }

    # ── Build core & satellite holdings ─────────────────────────────
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
                "principal_krw": h.get("principal_krw", 0),
                "pnl_krw": h.get("pnl_krw", 0),
                "pnl_pct": round(h.get("pnl_pct", 0), 2),
                "color": _holding_color(h["ticker"], h.get("asset_class", "")),
            })
        core_holdings.append({
            "account_id": meta["id"], "account_name": meta["name"],
            "account_sub": meta["sub"], "account_color": meta["color"],
            "total_krw": round(acct_total), "items": items,
        })

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
            if api_key == "UPBIT" and h["eval_krw"] < 5000:
                continue
            items.append({
                "ticker": h["ticker"], "name": h["name"],
                "cat": h.get("asset_class", ""),
                "krw": round(h["eval_krw"]),
                "principal_krw": h.get("principal_krw", 0),
                "pnl_krw": h.get("pnl_krw", 0),
                "pnl_pct": round(h.get("pnl_pct", 0), 2),
                "color": _holding_color(h["ticker"], h.get("asset_class", "")),
            })
        satellite_holdings.append({
            "account_id": meta["id"], "account_name": meta["name"],
            "account_sub": meta["sub"], "account_color": meta["color"],
            "total_krw": round(acct_total), "items": items,
        })

    # toss satellite — 노션 우선, 없으면 기존 보존
    existing_sat = existing.get("satellite_holdings", [])
    toss_sat = next((s for s in existing_sat if s.get("account_id") == "toss"), None)
    if toss_sat:
        toss_sat["total_krw"] = manual_krw.get("toss", toss_sat["total_krw"])
        if nvda_mdd:
            toss_sat["nvda_mdd"] = nvda_mdd
        if toss_nvda:
            toss_sat["toss_nvda"] = toss_nvda
        satellite_holdings.append(toss_sat)

    # bank satellite card (비상금 + 사업자금)
    bank_items = []
    for acc_id in ("emergency", "biz"):
        meta_b = MANUAL_ACCOUNT_META[acc_id]
        krw_b = manual_krw.get(acc_id, 0)
        if krw_b > 0:
            bank_items.append({
                "ticker": acc_id, "name": meta_b["name"],
                "cat": "cash", "krw": round(krw_b), "color": meta_b["color"],
            })
    if bank_items:
        satellite_holdings.append({
            "account_id": "bank",
            "account_name": "은행계좌",
            "account_sub": "비상금 · 사업자금",
            "account_color": "#1D9E75",
            "total_krw": round(sum(i["krw"] for i in bank_items)),
            "items": bank_items,
        })

    # ── Liquidity metrics ─────────────────────────────────────────────
    upbit_okx_toss = (upbit_krw + okx_krw + manual_krw.get("toss", 0)
                      + acct_totals.get("KIS_JONGHAP", 0))
    instant_cash = manual_krw.get("emergency", 0) + manual_krw.get("biz", 0)
    existing_metrics = existing.get("metrics", {})
    existing_tax = existing_metrics.get("tax", {})

    # ── Split buying — 기존 data.json 보존, 없으면 기본값 ─────────────
    split_buying = existing.get("split_buying", DEFAULT_SPLIT_BUYING)

    # ── Markets ──────────────────────────────────────────────────────
    market_data = extras.get("market_data", {})
    markets: dict = {}
    if market_data:
        markets = {
            "macro":             market_data.get("macro", {}),
            "assets":            market_data.get("assets", {}),
            "fear_greed":        market_data.get("fear_greed", {}),
            "usdkrw_change_pct": market_data.get("usdkrw_change_pct"),
        }

    # ── 리밸런싱 ─────────────────────────────────────────────────────
    global_rebal = []
    for cls in ["us_stock", "crypto", "bond", "gold", "oil", "cash"]:
        val = cls_raw.get(cls, 0)
        actual = round(val / total_krw * 100, 1) if total_krw else 0
        target = GLOBAL_REBAL_TARGETS.get(cls, 0)
        global_rebal.append({
            "cls": cls, "name": _CLS_NAMES.get(cls, cls),
            "target": target, "actual": actual,
            "diff": round(actual - target, 1),
        })

    per_acct_rebal = []
    for api_key in ["KIS_JONGHAP", "KIS_ISA", "KIS_YEON"]:
        meta_r = ACCOUNT_META[api_key]
        items_raw_r = kis_account_map.get(api_key, [])
        acct_total_r = acct_totals.get(api_key, 0)
        acct_class_targets = ACCOUNT_REBAL_TARGETS.get(meta_r["id"], {})
        # Aggregate actual by asset class within this account
        cls_krw_r: dict = {}
        for h in items_raw_r:
            rcls = h.get("asset_class", "us_stock")
            cls_krw_r[rcls] = cls_krw_r.get(rcls, 0) + h["eval_krw"]
        class_rows = []
        for cls, target in acct_class_targets.items():
            actual_pct = round(cls_krw_r.get(cls, 0) / acct_total_r * 100, 1) if acct_total_r else 0
            class_rows.append({
                "cls": cls, "name": _CLS_NAMES.get(cls, cls),
                "target": target, "actual": actual_pct,
                "diff": round(actual_pct - target, 1),
            })
        per_acct_rebal.append({
            "account_id": meta_r["id"], "account_name": meta_r["name"],
            "class_rows": class_rows,
        })

    rebalancing = {"global": global_rebal, "per_account": per_acct_rebal}

    payload = {
        "updated_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).isoformat(timespec="seconds"),
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
        "daily_change": daily_change,
        "split_buying": split_buying,
        "capital_gains": capital_gains,
        "markets": markets,
        "crypto_indicators": market_data.get("crypto_indicators", {}),
        "rebalancing": rebalancing,
        "sparkline_history": extras.get("sparkline_history", []),
        "nvda_mdd": nvda_mdd,
        "toss_nvda": toss_nvda,
    }

    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  docs/data.json 저장 완료 (총 자산: {total_krw:,.0f} KRW)")


def _cls_delta(today: float, yesterday: float) -> dict:
    diff = round(today - yesterday)
    pct = round((today - yesterday) / yesterday * 100, 2) if yesterday else 0
    return {"today": round(today), "yesterday": round(yesterday), "diff": diff, "pct": pct}


def run_pipeline() -> None:
    print("=" * 60)
    print("포트폴리오 파이프라인 시작")
    print("=" * 60)

    errors = []
    all_holdings = []

    # ── 0. 노션 수동 잔고 (양방향 동기화) ───────────────────────────
    notion_manual: dict = {"toss": {"eval": 0}, "emergency": {"eval": 0}, "biz": {"eval": 0}}
    print("\n[0/9] 노션 수동 잔고 조회 (토스/비상금/사업자금)...")
    try:
        notion_manual = pull_manual_holdings()
        toss_eval = notion_manual.get("toss", {}).get("eval", 0)
        toss_pnl  = notion_manual.get("toss", {}).get("pnl_pct")
        em_eval   = notion_manual.get("emergency", {}).get("eval", 0)
        biz_eval  = notion_manual.get("biz", {}).get("eval", 0)
        pnl_str   = f" (수익률 {toss_pnl:+.1f}%)" if toss_pnl is not None else ""
        print(f"  토스: {toss_eval:,.0f}{pnl_str} | 비상금: {em_eval:,.0f} | 사업: {biz_eval:,.0f}")
    except Exception as e:
        errors.append(f"노션수동잔고: {e}")
        print(f"  [오류] {e}")

    # ── 1. 업비트 잔고 ──────────────────────────────────────────
    upbit_krw = 0.0
    print("\n[1/9] 업비트 잔고 조회...")
    try:
        krw_cash, upbit_holdings = upbit_fetch()
        upbit_krw += krw_cash
        for h in upbit_holdings:
            upbit_krw += h["eval_krw"]
            principal = round(h["avg_buy_price"] * h["quantity"])
            all_holdings.append({
                "ticker": h["currency"],
                "name": h["currency"],
                "quantity": h["quantity"],
                "avg_price": h["avg_buy_price"],
                "current_price": h["current_price"],
                "eval_krw": h["eval_krw"],
                "profit_rate": h["profit_rate"],
                "principal_krw": principal,
                "pnl_krw": round(h["eval_krw"] - principal),
                "pnl_pct": h["profit_rate"],
                "account": "UPBIT",
                "asset_class": classify(h["currency"]),
            })
        print(f"  업비트 총액: {upbit_krw:,.0f} KRW")
    except Exception as e:
        errors.append(f"업비트: {e}")
        print(f"  [오류] {e}")

    # ── 2. 시세 조회 ────────────────────────────────────────────
    print("\n[2/9] 시장 데이터 조회...")
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
    print("\n[3/9] OKX 잔고 조회...")
    try:
        okx_holdings = okx_fetch()
        for h in okx_holdings:
            krw_val = h["usd_value"] * usdkrw
            if krw_val < 5000:
                continue
            okx_krw += krw_val
            all_holdings.append({
                "ticker": h["currency"],
                "name": h["currency"],
                "quantity": h["quantity"],
                "avg_price": 0,
                "current_price": h["usd_value"] / h["quantity"] if h["quantity"] else 0,
                "eval_krw": krw_val,
                "profit_rate": 0,
                "principal_krw": 0,
                "pnl_krw": 0,
                "pnl_pct": 0.0,
                "account": "OKX",
                "asset_class": classify(h["currency"]),
            })
        print(f"  OKX 총액: {okx_krw:,.0f} KRW")
    except Exception as e:
        errors.append(f"OKX: {e}")
        print(f"  [오류] {e}")

    # ── 4. 한투 잔고 ─────────────────────────────────────────────
    kis_krw = 0.0
    kis_cash: dict = {}
    print("\n[4/9] 한투 KIS 잔고 조회...")
    try:
        kis_data = kis_fetch()
        for h in kis_data.get("overseas", []):
            krw_val = h["eval_usd"] * usdkrw
            kis_krw += krw_val
            principal = round(h["avg_price"] * h["quantity"] * usdkrw)
            all_holdings.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "quantity": h["quantity"],
                "avg_price": h["avg_price"] * usdkrw,
                "current_price": (h["eval_usd"] / h["quantity"] * usdkrw) if h["quantity"] else 0,
                "eval_krw": krw_val,
                "profit_rate": h["profit_rate"],
                "principal_krw": principal,
                "pnl_krw": round(krw_val - principal),
                "pnl_pct": h["profit_rate"],
                "account": f"KIS_{h['account']}",
                "asset_class": classify(h["ticker"]),
            })
        for h in kis_data.get("domestic", []):
            kis_krw += h["eval_krw"]
            principal = round(h["avg_price"] * h["quantity"])
            all_holdings.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "quantity": h["quantity"],
                "avg_price": h["avg_price"],
                "current_price": h["eval_krw"] / h["quantity"] if h["quantity"] else 0,
                "eval_krw": h["eval_krw"],
                "profit_rate": h["profit_rate"],
                "principal_krw": principal,
                "pnl_krw": round(h["eval_krw"] - principal),
                "pnl_pct": h["profit_rate"],
                "account": f"KIS_{h['account']}",
                "asset_class": classify(h["ticker"]),
            })
        # KIS 현금 잔고 (예수금)
        kis_cash = {
            "KIS_JONGHAP": kis_data.get("cash_jonghap", 0),
            "KIS_ISA":     kis_data.get("cash_isa", 0),
            "KIS_YEON":    kis_data.get("cash_yeon", 0),
        }
        kis_cash_total = sum(kis_cash.values())
        kis_krw += kis_cash_total
        print(f"  한투 총액: {kis_krw:,.0f} KRW (현금 {kis_cash_total:,.0f} 포함)")
    except Exception as e:
        errors.append(f"KIS: {e}")
        print(f"  [오류] {e}")

    # ── 5. 포트폴리오 지표 계산 ───────────────────────────────────
    print("\n[5/9] 포트폴리오 집계...")
    total_krw = upbit_krw + okx_krw + kis_krw  # kis_krw already includes cash

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
        "asset_class_targets": {"crypto": 35.0, "us_stock": 25.0, "bond": 20.0, "gold": 10.0, "oil": 5.0},
    }

    btc_holding = next((h for h in all_holdings if h["ticker"] == "BTC" and h["account"] == "UPBIT"), None)
    eth_holding = next((h for h in all_holdings if h["ticker"] == "ETH" and h["account"] == "UPBIT"), None)

    snapshot = {
        "total_krw": total_krw,
        "crypto_krw": class_totals.get("crypto", 0),
        "stock_krw": class_totals.get("us_stock", 0),
        "gold_krw": class_totals.get("gold", 0),
        "oil_krw": class_totals.get("oil", 0),
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

    # ── 6. 마켓 데이터 (지수 + BTC 도미넌스 + 공포탐욕) ──────────────
    print("\n[6/9] 시장 지수 조회...")
    market_data = {}
    try:
        market_data = get_market_data(usdkrw)
        macro_keys = list(market_data.get("macro", {}).keys())
        asset_keys = list(market_data.get("assets", {}).keys())
        fg = market_data.get("fear_greed", {})
        print(f"  매크로: {macro_keys} | 자산: {asset_keys}")
        print(f"  크립토 F&G: {fg.get('crypto', {}).get('value')} | S&P F&G: {fg.get('sp500', {}).get('value')}")
    except Exception as e:
        errors.append(f"마켓데이터: {e}")
        print(f"  [오류] {e}")

    # ── 7. 어제 스냅샷 + 일일 변동 ─────────────────────────────────
    print("\n[7/9] 어제 스냅샷 조회...")
    yesterday_snapshot = None
    try:
        yesterday_snapshot = get_yesterday_snapshot()
        if yesterday_snapshot:
            print(f"  어제 총자산: {yesterday_snapshot.get('total_krw', 0):,.0f}")
        else:
            print("  어제 데이터 없음")
    except Exception as e:
        errors.append(f"어제스냅샷: {e}")
        print(f"  [오류] {e}")

    # ── 8. 양도세 YTD ─────────────────────────────────────────────
    print("\n[8/9] 양도세 YTD 계산...")
    capital_gains = 0.0
    try:
        capital_gains = get_capital_gains_ytd()
        print(f"  YTD 실현 차익: {capital_gains:,.0f} KRW")
    except Exception as e:
        errors.append(f"양도세: {e}")
        print(f"  [오류] {e}")

    # ── 9. 스파크라인 히스토리 ────────────────────────────────────
    print("\n[9/9] 스파크라인 히스토리 조회...")
    sparkline_history = []
    try:
        sparkline_history = get_sparkline_history(30)
        print(f"  스파크라인: {len(sparkline_history)}일")
    except Exception as e:
        errors.append(f"스파크라인: {e}")
        print(f"  [오류] {e}")

    # NVDA 평단 (토스, USD → KRW 변환)
    nvda_avg_krw = 0.0
    try:
        nvda_avg_usd = get_nvda_avg_price()
        nvda_avg_krw = nvda_avg_usd * usdkrw if nvda_avg_usd else 0
    except Exception:
        pass
    nvda_current_krw = (prices.get("NVDA") or 0) * usdkrw

    extras = {
        "notion_manual": notion_manual,
        "yesterday": yesterday_snapshot,
        "capital_gains": capital_gains,
        "sparkline_history": sparkline_history,
        "market_data": market_data,
        "nvda_avg_krw": nvda_avg_krw,
        "nvda_current_krw": nvda_current_krw,
        "risky_pct": risky_pct,
        "safe_pct": safe_pct,
        "kis_cash": kis_cash,
    }

    # ── docs/data.json 생성 ──────────────────────────────────────
    print("\n[data.json] GitHub Pages 데이터 생성...")
    try:
        write_data_json(all_holdings, upbit_krw, okx_krw, class_totals, usdkrw, extras)
    except Exception as e:
        errors.append(f"data.json: {e}")
        print(f"  [오류] {e}")
        traceback.print_exc()

    # ── 노션 업데이트 ─────────────────────────────────────────────
    print("\n[notion] 노션 동기화...")
    try:
        sync_holdings(all_holdings)
        add_snapshot(snapshot)
    except Exception as e:
        errors.append(f"노션: {e}")
        print(f"  [오류] {e}")

    # ── Slack 알림 ───────────────────────────────────────────────
    print("\n[slack] Slack 발송...")
    try:
        send_daily_report(portfolio)
        if datetime.date.today().weekday() == 0:
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
