"""거래 회고 엔진 — 매매 시점 가격 vs 30일 후 가격 비교.

각 거래에 대해 30일 후 close price를 yfinance/Upbit candle로 가져와
result_pct = (price_30d_after - trade_price) / trade_price * 100 계산.

매수의 경우: result_pct > 0 = 좋은 결정 (사고 올랐다)
매도의 경우: result_pct < 0 = 좋은 결정 (팔고 떨어졌다)
"""
import sys
import datetime
import requests

sys.stdout.reconfigure(encoding="utf-8")


def _yf_close_on_date(ticker: str, date_str: str) -> float | None:
    """yfinance로 특정 날짜 close 조회 (date_str: 'YYYY-MM-DD').

    해당일에 거래 없으면 다음 영업일 close 사용. 5영업일 내 없으면 None.
    """
    try:
        import yfinance as yf
        target = datetime.date.fromisoformat(date_str)
        # 해당일 ~ 다음 7일 윈도우로 받아 첫 close 사용
        start = target.isoformat()
        end = (target + datetime.timedelta(days=7)).isoformat()
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        close = df["Close"]
        # MultiIndex 가능
        if hasattr(close, "columns") and len(close.columns) > 0:
            col = close.iloc[:, 0]
        else:
            col = close
        col = col.dropna()
        if len(col) == 0:
            return None
        return float(col.iloc[0])
    except Exception as e:
        print(f"    [yf {ticker} {date_str} 실패] {e}")
        return None


def _yahoo_symbol(ticker: str) -> str:
    """노션 티커 → Yahoo 심볼 변환.

    - 6자리 숫자 (예: '426030', '411060'): KOSPI/KOSDAQ ETF → '.KS' suffix
    - 영문자: 그대로 (예: 'NVDA')
    - 그 외 (한국 ETF 변형 코드 등): 그대로 시도
    """
    t = (ticker or "").strip().upper()
    if not t:
        return t
    # 6자리 숫자거나 첫글자가 숫자인 한국 종목 코드
    if t.isdigit() and len(t) == 6:
        return f"{t}.KS"
    # 알파벳+숫자 (예: '0091C0' = KODEX 미국10년)
    if t[0].isdigit() or (len(t) == 6 and t[1:5].isdigit()):
        return f"{t}.KS"
    return t


def _upbit_close_on_date(currency: str, date_str: str) -> float | None:
    """Upbit candle API로 KRW 마켓의 특정일 close 조회."""
    target = datetime.date.fromisoformat(date_str)
    # 해당일+1 (UTC 자정 기준 일봉) — 안전하게 다음날까지 1봉 조회
    to = (target + datetime.timedelta(days=2)).isoformat() + "T00:00:00Z"
    try:
        r = requests.get(
            "https://api.upbit.com/v1/candles/days",
            params={"market": f"KRW-{currency}", "to": to, "count": "3"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        rows = r.json() or []
        # rows[0] = 가장 최신 (=target+1 또는 target). 정확한 날짜 찾기
        for row in rows:
            d = (row.get("candle_date_time_kst") or "")[:10]
            if d == date_str:
                return float(row.get("trade_price") or row.get("opening_price") or 0)
        # 정확히 일치 없으면 가장 가까운 미래 close
        return float(rows[-1].get("trade_price")) if rows else None
    except Exception as e:
        print(f"    [upbit {currency} {date_str} 실패] {e}")
        return None


def _close_on_date(t: dict, date_str: str) -> float | None:
    """거래 종목의 date_str 종가를 거래소에 맞게 조회."""
    if t.get("exchange") == "upbit":
        return _upbit_close_on_date(t.get("ticker", ""), date_str)
    sym = _yahoo_symbol(t.get("ticker", ""))
    return _yf_close_on_date(sym, date_str)


def compute_review(transactions: list[dict], window_days: int = 30) -> list[dict]:
    """30일 + 이상 지난 거래만 골라 결과(%) 계산. result_30d 미기록만 처리.

    반환: [{page_id, uid, side, result_pct, ...t fields...}, ...]
    호출 측에서 노션 업데이트.
    """
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=window_days)
    results = []

    for t in transactions:
        if t.get("result_30d") is not None:
            continue  # 이미 기록됨
        date_str = (t.get("date") or "")[:10]
        if not date_str:
            continue
        try:
            trade_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if trade_date > cutoff:
            continue

        target_date = (trade_date + datetime.timedelta(days=window_days)).isoformat()
        # 미래 날짜 방지
        if datetime.date.fromisoformat(target_date) > today:
            continue

        trade_price = t.get("price") or 0
        if trade_price <= 0:
            continue

        future_price = _close_on_date(t, target_date)
        if future_price is None or future_price <= 0:
            continue

        result_pct = (future_price - trade_price) / trade_price * 100
        results.append({
            **t,
            "future_price": future_price,
            "result_pct":   round(result_pct, 2),
            "review_date":  target_date,
        })

    return results


def classify_outcome(side: str, result_pct: float) -> tuple[str, str]:
    """(emoji, label) 반환. 매수는 +면 good, 매도는 -면 good."""
    is_buy = side == "buy"
    good = (is_buy and result_pct > 0) or (not is_buy and result_pct < 0)
    if good:
        return ("✓", "좋은 결정") if abs(result_pct) > 2 else ("◯", "무난")
    bad = (is_buy and result_pct < -2) or (not is_buy and result_pct > 2)
    if bad:
        return ("✗", "아쉬움")
    return ("◯", "무난")


def build_review_summary(reviews: list[dict]) -> dict:
    """회고 탭 상단 요약 위젯용 패턴 인사이트."""
    if not reviews:
        return {"total": 0, "good": 0, "bad": 0, "avg_result": 0,
                "best": None, "worst": None}
    good_count = 0
    bad_count = 0
    sum_pct = 0.0
    best = None  # 가장 큰 +결과
    worst = None  # 가장 큰 -결과 (=가장 나쁜 매수, 또는 가장 나쁜 매도)
    for r in reviews:
        side = r.get("side", "buy")
        pct = r.get("result_pct", 0)
        # outcome 부호 정규화 (매수=+가 좋음, 매도=-가 좋음)
        signed = pct if side == "buy" else -pct
        sum_pct += signed
        if signed > 2:
            good_count += 1
        elif signed < -2:
            bad_count += 1
        if best is None or signed > best.get("_signed", -1e18):
            best = {**r, "_signed": signed}
        if worst is None or signed < worst.get("_signed", 1e18):
            worst = {**r, "_signed": signed}
    n = len(reviews)
    return {
        "total":      n,
        "good":       good_count,
        "bad":        bad_count,
        "neutral":    n - good_count - bad_count,
        "avg_result": round(sum_pct / n, 2) if n else 0,
        "best":       {k: v for k, v in (best or {}).items() if not k.startswith("_")},
        "worst":      {k: v for k, v in (worst or {}).items() if not k.startswith("_")},
    }


if __name__ == "__main__":
    # 단순 단위 테스트
    p = _yf_close_on_date("NVDA", "2026-04-01")
    print(f"NVDA 2026-04-01 close: {p}")
    p = _upbit_close_on_date("BTC", "2026-04-01")
    print(f"BTC 2026-04-01 close: {p}")
    e, l = classify_outcome("buy", 5.0)
    print(f"buy +5%: {e} {l}")
    e, l = classify_outcome("sell", -3.0)
    print(f"sell -3%: {e} {l}")
