"""포트폴리오 X-Ray — ETF 보유 종목 분석 (look-through).

각 ETF의 top holdings를 하드코딩(공식 disclosure 기반)해 보유 비중을 누적.
실질 종목 노출 = ETF 간접 노출 + 직접 보유.

데이터 출처 (참고일자: 2026-04 기준 — 정기 갱신 필요):
- AGIX: Themes Generative AI ETF 공식 disclosure
- 426030 (TIME 미국나스닥100액티브): KRX 공시
- 456600 (TIME 글로벌AI인공지능액티브): KRX 공시
- QQQM: Invesco NASDAQ-100 ETF (Top 10 holdings)

ETF별 top 10 holdings만 추적 (long tail은 무시).
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")


# ── ETF 구성 종목 (티커 → {symbol: weight%}) ──────────────────────────────────
# 모든 weight는 % 단위. 합이 100이 안 돼도 OK (top 10만)
ETF_HOLDINGS: dict[str, dict[str, float]] = {
    "AGIX": {
        # Themes Generative AI ETF (top holdings, ~2026-04)
        "NVDA": 5.5, "MSFT": 4.8, "GOOGL": 4.2, "META": 3.9, "AMZN": 3.5,
        "AVGO": 3.2, "AMD": 2.9, "ORCL": 2.4, "TSM": 2.1, "PLTR": 2.0,
    },
    "QQQM": {
        # Invesco Nasdaq-100 (M class) — same holdings as QQQ
        "AAPL": 8.7, "MSFT": 8.3, "NVDA": 7.9, "AMZN": 5.4, "GOOGL": 4.5,
        "META": 4.1, "GOOG": 3.0, "AVGO": 4.2, "TSLA": 3.0, "COST": 2.5,
    },
    "426030": {
        # TIME 미국나스닥100액티브 (NASDAQ-100 액티브 추종)
        "AAPL": 9.0, "MSFT": 8.5, "NVDA": 8.0, "AMZN": 5.5, "GOOGL": 4.6,
        "META": 4.2, "AVGO": 4.0, "TSLA": 3.0, "COST": 2.5, "NFLX": 2.2,
    },
    "456600": {
        # TIME 글로벌AI인공지능액티브
        "NVDA": 8.0, "MSFT": 6.5, "GOOGL": 5.0, "META": 4.5, "AMZN": 4.0,
        "TSM": 3.8, "AVGO": 3.5, "AMD": 3.0, "ASML": 2.5, "ORCL": 2.0,
    },
    # 비주식 ETF (gold/oil/bond)는 종목 분해 의미 없음 → 빈 dict
    "411060": {},   # ACE KRX금현물
    "130680": {},   # TIGER 원유선물
    "0091C0": {},   # KODEX 미국10년국채
    "308620": {},   # KODEX 미국채10년선물
}


def compute_xray(holdings: list[dict], total_krw: float) -> dict:
    """holdings(보유종목 리스트) → 실질 종목/섹터/지역 노출 분석.

    Args:
        holdings: [{"ticker", "eval_krw", "asset_class"}, ...]
        total_krw: 포트폴리오 총자산
    Returns:
        {
          "look_through": [{symbol, krw, pct, direct, indirect}],  # 실질 종목 노출 top 20
          "sectors":      [{sector, krw, pct}],
          "regions":      [{region, krw, pct}],
        }
    """
    if total_krw <= 0:
        return {"look_through": [], "sectors": [], "regions": []}

    # 종목별 노출 KRW 누적
    direct_krw: dict[str, float] = {}
    indirect_krw: dict[str, float] = {}

    for h in holdings:
        ticker = (h.get("ticker") or "").upper()
        krw = h.get("eval_krw", 0) or 0
        if krw <= 0 or not ticker:
            continue

        # 1) 직접 보유 (NVDA, BTC, AGIX 자체 등)
        if ticker not in ETF_HOLDINGS:
            direct_krw[ticker] = direct_krw.get(ticker, 0) + krw
            continue

        # 2) ETF인 경우, 분해 시도
        components = ETF_HOLDINGS[ticker]
        if not components:
            # bond/gold/oil ETF — 분해 없이 자체로 카운트
            direct_krw[ticker] = direct_krw.get(ticker, 0) + krw
            continue

        # ETF top 10 = 전체의 일부일 뿐. 그 부분만 look-through.
        for comp_sym, weight_pct in components.items():
            indirect_amt = krw * weight_pct / 100
            indirect_krw[comp_sym] = indirect_krw.get(comp_sym, 0) + indirect_amt

        # ETF 자체도 ticker로 직접 추가 (잔여분)
        accounted = sum(components.values())  # %
        residual = krw * (100 - accounted) / 100
        if residual > 0:
            direct_krw[f"{ticker} (잔여)"] = direct_krw.get(f"{ticker} (잔여)", 0) + residual

    # 합산
    all_syms = set(direct_krw) | set(indirect_krw)
    rows = []
    for sym in all_syms:
        d = direct_krw.get(sym, 0)
        i = indirect_krw.get(sym, 0)
        total = d + i
        rows.append({
            "symbol":   sym,
            "krw":      round(total),
            "pct":      round(total / total_krw * 100, 2),
            "direct":   round(d),
            "indirect": round(i),
        })
    rows.sort(key=lambda x: -x["krw"])
    look_through = rows[:20]

    # ── 섹터 분류 ─────────────────────────────────────────────────────
    SECTOR_MAP = {
        "NVDA": "AI/반도체", "AMD": "AI/반도체", "AVGO": "AI/반도체",
        "TSM": "AI/반도체", "ASML": "AI/반도체",
        "AAPL": "빅테크 하드웨어",
        "MSFT": "빅테크 SW", "GOOGL": "빅테크 SW", "GOOG": "빅테크 SW",
        "META": "빅테크 SW", "AMZN": "빅테크 SW", "ORCL": "빅테크 SW",
        "PLTR": "빅테크 SW",
        "TSLA": "EV/모빌리티",
        "NFLX": "엔터/콘텐츠",
        "COST": "소비재",
        "BTC": "크립토", "ETH": "크립토", "XRP": "크립토", "SOL": "크립토",
        "DOGE": "크립토", "ADA": "크립토", "USDT": "크립토",
        "IAU": "원자재", "GLD": "원자재", "411060": "원자재",
        "USO": "원자재", "130680": "원자재",
        "IEF": "채권", "SHY": "채권", "SGOV": "채권",
        "0091C0": "채권", "308620": "채권",
    }
    sector_krw: dict[str, float] = {}
    for r in rows:
        sym = r["symbol"].split(" ")[0]  # "(잔여)" 제거
        sector = SECTOR_MAP.get(sym, "기타")
        sector_krw[sector] = sector_krw.get(sector, 0) + r["krw"]
    sectors = sorted(
        [{"sector": s, "krw": round(v), "pct": round(v / total_krw * 100, 1)}
         for s, v in sector_krw.items()],
        key=lambda x: -x["krw"],
    )

    # ── 지역 분류 ─────────────────────────────────────────────────────
    KOREAN_TICKERS = {"426030", "456600", "411060", "130680", "0091C0", "308620"}
    CRYPTO_TICKERS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "AVAX",
                      "MATIC", "LINK", "USDT", "USDC", "BNB"}
    region_krw: dict[str, float] = {"미국": 0, "한국": 0, "크립토": 0, "기타": 0}
    for r in rows:
        sym = r["symbol"].split(" ")[0]
        if sym in CRYPTO_TICKERS:
            region_krw["크립토"] += r["krw"]
        elif sym in KOREAN_TICKERS:
            region_krw["한국"] += r["krw"]
        elif sym.isdigit() and len(sym) == 6:
            region_krw["한국"] += r["krw"]
        elif any(c.isalpha() for c in sym):
            region_krw["미국"] += r["krw"]
        else:
            region_krw["기타"] += r["krw"]
    regions = [
        {"region": k, "krw": round(v), "pct": round(v / total_krw * 100, 1)}
        for k, v in region_krw.items() if v > 0
    ]
    regions.sort(key=lambda x: -x["krw"])

    return {
        "look_through": look_through,
        "sectors":      sectors,
        "regions":      regions,
    }


if __name__ == "__main__":
    sample = [
        {"ticker": "NVDA",   "eval_krw": 30_000_000, "asset_class": "us_stock"},
        {"ticker": "AGIX",   "eval_krw": 20_000_000, "asset_class": "us_stock"},
        {"ticker": "426030", "eval_krw": 15_000_000, "asset_class": "us_stock"},
        {"ticker": "BTC",    "eval_krw": 50_000_000, "asset_class": "crypto"},
    ]
    res = compute_xray(sample, total_krw=120_000_000)
    print("\n[Top 종목 노출]")
    for r in res["look_through"][:10]:
        print(f"  {r['symbol']:<8} {r['krw']:>14,} ({r['pct']:>5.2f}%) "
              f"[직접 {r['direct']:>11,} | 간접 {r['indirect']:>11,}]")
    print("\n[섹터]")
    for s in res["sectors"]:
        print(f"  {s['sector']:<14} {s['krw']:>14,} ({s['pct']}%)")
    print("\n[지역]")
    for r in res["regions"]:
        print(f"  {r['region']:<8} {r['krw']:>14,} ({r['pct']}%)")
