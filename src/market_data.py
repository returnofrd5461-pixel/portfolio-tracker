import sys
import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")

TICKERS = ["QQQM", "AGIX", "IAU", "IEF", "USO", "NVDA"]
FX_TICKER = "USDKRW=X"


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
