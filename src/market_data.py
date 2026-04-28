import sys
import requests
import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")

TICKERS = ["QQQM", "AGIX", "IAU", "IEF", "USO", "NVDA"]
FX_TICKER = "USDKRW=X"

# macro + asset 배치 다운로드용
_MACRO_TICKERS = {
    "DX-Y.NYB": {"key": "DXY",   "name": "달러 인덱스", "prec": 2},
    "^TNX":     {"key": "US10Y", "name": "미국채 10년", "prec": 3},
    "^VIX":     {"key": "VIX",   "name": "VIX",        "prec": 2},
    "CL=F":     {"key": "CL1",   "name": "WTI 원유",   "prec": 2},
}
_ASSET_TICKERS = {
    "BTC-USD": {"key": "BTC",   "name": "BTC",    "prec": 0},
    "ETH-USD": {"key": "ETH",   "name": "ETH",    "prec": 0},   # ETH/BTC 비율용, 미표시
    "^IXIC":   {"key": "NDX",   "name": "나스닥", "prec": 0},
    "GC=F":    {"key": "GOLD",  "name": "금",     "prec": 0},
    "^KS11":   {"key": "KOSPI", "name": "KOSPI",  "prec": 0},
}


def _get_col(df, *candidates):
    """DataFrame에서 후보 컬럼명 순서로 찾아 dropna Series 반환."""
    for c in candidates:
        if c in df.columns:
            s = df[c].dropna()
            if len(s) > 0:
                return s
    return None


def _parse_change(series) -> dict | None:
    if series is None:
        return None
    if len(series) >= 2:
        prev, curr = float(series.iloc[-2]), float(series.iloc[-1])
        chg = (curr - prev) / prev * 100 if prev else 0.0
        return {"price": curr, "change_pct": round(chg, 2)}
    if len(series) == 1:
        return {"price": float(series.iloc[-1]), "change_pct": 0.0}
    return None


def get_prices() -> dict[str, float]:
    """종가 및 USD/KRW 환율 반환."""
    all_tickers = TICKERS + [FX_TICKER]
    data = yf.download(all_tickers, period="2d", auto_adjust=True, progress=False)
    closes = data["Close"].iloc[-1]

    result = {}
    for t in TICKERS:
        val = closes.get(t)
        result[t] = float(val) if val is not None else None

    fx = closes.get(FX_TICKER)
    result["USDKRW"] = float(fx) if fx is not None else None
    return result


def get_market_data(usdkrw: float = 1380.0) -> dict:
    """매크로 4종 + 자산 4종 + F&G 2종 + 크립토 인디케이터 반환.

    반환 구조:
      {
        "macro":   {"DXY": {name, price, change_pct}, "US10Y": ..., "VIX": ..., "CL1": ...},
        "assets":  {"BTC": ..., "NDX": ..., "GOLD": ..., "KOSPI": ...},
        "fear_greed": {
          "crypto": {"value": 33, "label": "Fear"},
          "sp500":  {"value": 45, "label": "Neutral"},   # CNN F&G
        },
        "crypto_indicators": {
          "btc_dominance": 58.1, "usdt_dominance": 7.5,
          "eth_btc_ratio": 0.029, "total3_market_cap": 4e11
        },
        "usdkrw_change_pct": -0.21,
      }
    """
    result: dict = {
        "macro": {},
        "assets": {},
        "fear_greed": {},
        "crypto_indicators": {},
        "usdkrw_change_pct": None,
    }

    # ── 매크로 + 자산 yfinance 배치 ─────────────────────────────────
    all_yf = (
        list(_MACRO_TICKERS.keys())
        + list(_ASSET_TICKERS.keys())
        + [FX_TICKER]
    )
    try:
        data = yf.download(all_yf, period="2d", auto_adjust=True, progress=False)
        close_df = data["Close"]

        for ticker, meta in _MACRO_TICKERS.items():
            try:
                col = _get_col(close_df, ticker, ticker.lstrip("^"))
                info = _parse_change(col)
                if info:
                    result["macro"][meta["key"]] = {
                        "name":       meta["name"],
                        "price":      round(info["price"], meta["prec"]),
                        "change_pct": info["change_pct"],
                    }
            except Exception:
                pass

        for ticker, meta in _ASSET_TICKERS.items():
            try:
                col = _get_col(close_df, ticker, ticker.lstrip("^"), ticker.split("-")[0])
                info = _parse_change(col)
                if info and meta["key"] != "ETH":   # ETH는 ratio용이라 미표시
                    result["assets"][meta["key"]] = {
                        "name":       meta["name"],
                        "price":      round(info["price"], meta["prec"]),
                        "change_pct": info["change_pct"],
                    }
            except Exception:
                pass

        # ETH/BTC 비율 계산 (yfinance 데이터 재사용)
        try:
            btc_col = _get_col(close_df, "BTC-USD", "BTC")
            eth_col = _get_col(close_df, "ETH-USD", "ETH")
            if btc_col is not None and eth_col is not None:
                btc_p = float(btc_col.iloc[-1])
                eth_p = float(eth_col.iloc[-1])
                if btc_p > 0:
                    result["crypto_indicators"]["eth_btc_ratio"] = round(eth_p / btc_p, 5)
        except Exception:
            pass

        # USD/KRW 변화율
        try:
            fx_col = _get_col(close_df, FX_TICKER, "USDKRW=X")
            if fx_col is not None and len(fx_col) >= 2:
                p, c = float(fx_col.iloc[-2]), float(fx_col.iloc[-1])
                result["usdkrw_change_pct"] = round((c - p) / p * 100, 2) if p else 0.0
        except Exception:
            pass

    except Exception as e:
        print(f"  [경고] yfinance 배치 조회 실패: {e}")

    # ── CoinGecko /global ────────────────────────────────────────────
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=12)
        if r.status_code == 200:
            cg = r.json()["data"]
            mcp = cg.get("market_cap_percentage", {})
            btc_dom  = round(float(mcp.get("btc",  0)), 2)
            eth_dom  = round(float(mcp.get("eth",  0)), 2)
            usdt_dom = round(float(mcp.get("usdt", 0)), 2)

            result["crypto_indicators"]["btc_dominance"]  = btc_dom
            result["crypto_indicators"]["usdt_dominance"] = usdt_dom

            total_mcap = cg.get("total_market_cap", {}).get("usd", 0)
            if total_mcap:
                total3 = total_mcap * (1 - btc_dom / 100 - eth_dom / 100)
                result["crypto_indicators"]["total3_market_cap"] = round(total3)
    except Exception as e:
        print(f"  [경고] CoinGecko 조회 실패: {e}")

    # ── Crypto Fear & Greed (alternative.me) ────────────────────────
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        if r.status_code == 200:
            item = r.json()["data"][0]
            result["fear_greed"]["crypto"] = {
                "value": int(item["value"]),
                "label": item["value_classification"],
            }
    except Exception as e:
        print(f"  [경고] Crypto F&G 조회 실패: {e}")

    # ── S&P 500 CNN Fear & Greed ─────────────────────────────────────
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://edition.cnn.com/markets/fear-and-greed",
            "Accept": "application/json",
        }
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=headers,
            timeout=12,
        )
        if r.status_code == 200:
            fg_data = r.json()
            fg_obj = fg_data.get("fear_and_greed", {})
            score = fg_obj.get("score")
            rating = fg_obj.get("rating", "")
            if score is not None:
                result["fear_greed"]["sp500"] = {
                    "value": round(float(score), 1),
                    "label": rating,
                }
    except Exception as e:
        print(f"  [경고] CNN F&G 조회 실패: {e}")

    return result


def get_usdkrw() -> float:
    data = yf.download(FX_TICKER, period="2d", auto_adjust=True, progress=False)
    return float(data["Close"].iloc[-1].iloc[0])


def print_prices() -> None:
    prices = get_prices()
    usdkrw = prices.get("USDKRW", 0)
    print(f"\n{'티커':<10} {'종가(USD)':>12} {'원화환산(KRW)':>18}")
    print("─" * 44)
    for ticker in TICKERS:
        usd = prices.get(ticker)
        if usd is None:
            print(f"{ticker:<10}  {'조회실패':>12}")
            continue
        krw = usd * usdkrw if usdkrw else 0
        print(f"{ticker:<10}{usd:>12,.2f}{krw:>18,.0f}")
    print("─" * 44)
    print(f"{'USD/KRW'::<10}{usdkrw:>12,.2f}\n")


if __name__ == "__main__":
    print_prices()
    mkt = get_market_data()
    import json
    print(json.dumps(mkt, ensure_ascii=False, indent=2))
