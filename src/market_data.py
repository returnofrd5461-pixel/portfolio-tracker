import sys
import requests
import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")

TICKERS = ["QQQM", "AGIX", "IAU", "IEF", "USO", "NVDA"]
FX_TICKER = "USDKRW=X"
INDEX_TICKERS = ["^IXIC", "^GSPC", "^KS11"]
INDEX_NAMES = {"^IXIC": "나스닥", "^GSPC": "S&P 500", "^KS11": "KOSPI"}


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
    """주요 지수 + BTC 도미넌스 + 공포탐욕지수 반환."""
    result: dict = {
        "indices": {},
        "btc_dominance": None,
        "fear_greed": None,
        "usdkrw_change_pct": None,
    }

    # ── 지수 + 환율 변화율 ──────────────────────────────────────────
    try:
        fetch_tickers = INDEX_TICKERS + [FX_TICKER]
        data = yf.download(fetch_tickers, period="2d", auto_adjust=True, progress=False)
        close_df = data["Close"]

        for t in INDEX_TICKERS:
            try:
                col = None
                for candidate in [t, t.lstrip("^")]:
                    if candidate in close_df.columns:
                        col = close_df[candidate].dropna()
                        break
                if col is None:
                    continue
                if len(col) >= 2:
                    prev, curr = float(col.iloc[-2]), float(col.iloc[-1])
                    chg = (curr - prev) / prev * 100 if prev else 0.0
                    result["indices"][t] = {
                        "name": INDEX_NAMES.get(t, t),
                        "price": round(curr, 2),
                        "change_pct": round(chg, 2),
                    }
                elif len(col) == 1:
                    result["indices"][t] = {
                        "name": INDEX_NAMES.get(t, t),
                        "price": round(float(col.iloc[-1]), 2),
                        "change_pct": 0.0,
                    }
            except Exception:
                pass

        # USD/KRW 변화율
        try:
            fx_col = None
            for candidate in [FX_TICKER, "USDKRW=X"]:
                if candidate in close_df.columns:
                    fx_col = close_df[candidate].dropna()
                    break
            if fx_col is not None and len(fx_col) >= 2:
                p, c = float(fx_col.iloc[-2]), float(fx_col.iloc[-1])
                result["usdkrw_change_pct"] = round((c - p) / p * 100, 2) if p else 0.0
        except Exception:
            pass

    except Exception as e:
        print(f"  [경고] 지수 조회 실패: {e}")

    # ── BTC 도미넌스 ─────────────────────────────────────────────────
    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if r.status_code == 200:
            pct = r.json()["data"]["market_cap_percentage"].get("btc", 0)
            result["btc_dominance"] = round(float(pct), 1)
    except Exception as e:
        print(f"  [경고] BTC 도미넌스 조회 실패: {e}")

    # ── 공포·탐욕 지수 ───────────────────────────────────────────────
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        if r.status_code == 200:
            item = r.json()["data"][0]
            result["fear_greed"] = {
                "value": int(item["value"]),
                "label": item["value_classification"],
            }
    except Exception as e:
        print(f"  [경고] 공포탐욕지수 조회 실패: {e}")

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
    print("지수:", mkt["indices"])
    print("BTC 도미넌스:", mkt["btc_dominance"])
    print("공포탐욕:", mkt["fear_greed"])
