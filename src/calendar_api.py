"""매크로 캘린더 — FOMC / 빅테크 실적 / 한국 금통위 일정.

데이터 소스:
- FOMC: 2026년 일정 하드코딩 (연방준비제도 공식 발표 기준)
- 빅테크 실적: yfinance Ticker.calendar (티커별)
- 한국 금통위: 한국은행 공시 일정 하드코딩

각 이벤트는 importance(high/mid/low)로 분류되어 UI에서 강조 표시.
"""
import sys
import datetime

sys.stdout.reconfigure(encoding="utf-8")

# ── 2026년 FOMC 일정 (FED 공식 발표 기준) ────────────────────────────────────
FOMC_2026 = [
    ("2026-01-28", "FOMC 1월"),
    ("2026-03-18", "FOMC 3월 (SEP)"),
    ("2026-04-29", "FOMC 4월"),
    ("2026-06-17", "FOMC 6월 (SEP)"),
    ("2026-07-29", "FOMC 7월"),
    ("2026-09-16", "FOMC 9월 (SEP)"),
    ("2026-11-04", "FOMC 11월"),
    ("2026-12-16", "FOMC 12월 (SEP)"),
]

# ── 2026년 한국 금통위 (한국은행 공시 일정 추정) ─────────────────────────────
BOK_2026 = [
    ("2026-01-15", "금통위 1월"),
    ("2026-02-26", "금통위 2월"),
    ("2026-04-09", "금통위 4월"),
    ("2026-05-28", "금통위 5월"),
    ("2026-07-09", "금통위 7월"),
    ("2026-08-27", "금통위 8월"),
    ("2026-10-15", "금통위 10월"),
    ("2026-11-26", "금통위 11월"),
]

# 빅테크 실적 추적 대상 (보유 종목 + 시장 핵심)
EARNINGS_TICKERS = ["NVDA", "MSFT", "GOOGL", "AAPL", "AMZN", "META", "TSLA"]


def _fomc_events() -> list[dict]:
    today = datetime.date.today()
    out = []
    for d, title in FOMC_2026:
        try:
            ed = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        d_day = (ed - today).days
        out.append({
            "date":       d,
            "time":       "03:00 KST",  # FOMC 통상 ET 14:00 = KST 04:00 (서머타임)
            "type":       "fomc",
            "title":      title,
            "importance": "high",
            "d_day":      d_day,
        })
    return out


def _bok_events() -> list[dict]:
    today = datetime.date.today()
    out = []
    for d, title in BOK_2026:
        try:
            ed = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        d_day = (ed - today).days
        out.append({
            "date":       d,
            "time":       "10:00 KST",
            "type":       "kbok",
            "title":      title,
            "importance": "mid",
            "d_day":      d_day,
        })
    return out


def _earnings_events() -> list[dict]:
    """yfinance Ticker.calendar 로 실적 발표일 조회. 실패시 빈 리스트."""
    out: list[dict] = []
    today = datetime.date.today()
    try:
        import yfinance as yf
    except Exception:
        return out

    for tk in EARNINGS_TICKERS:
        try:
            cal = yf.Ticker(tk).calendar
        except Exception:
            continue
        if cal is None:
            continue
        # cal은 dict 또는 DataFrame 형태로 올 수 있음
        ed = None
        try:
            if isinstance(cal, dict):
                e_arr = cal.get("Earnings Date") or cal.get("earnings_date") or []
                if isinstance(e_arr, list) and e_arr:
                    ed = e_arr[0]
                elif e_arr:
                    ed = e_arr
            else:
                # DataFrame
                if "Earnings Date" in getattr(cal, "index", []):
                    val = cal.loc["Earnings Date"]
                    ed = val.iloc[0] if hasattr(val, "iloc") else val
        except Exception:
            ed = None

        if ed is None:
            continue
        # datetime → ISO date
        if hasattr(ed, "date"):
            ed_date = ed.date() if callable(getattr(ed, "date", None)) else ed
        elif isinstance(ed, datetime.date):
            ed_date = ed
        else:
            try:
                ed_date = datetime.date.fromisoformat(str(ed)[:10])
            except Exception:
                continue

        d_day = (ed_date - today).days
        if d_day < -7 or d_day > 90:
            continue  # 7일 전 ~ 90일 후 범위만
        out.append({
            "date":       ed_date.isoformat(),
            "time":       "AMC",  # After Market Close (보통)
            "type":       "earnings",
            "title":      f"{tk} 실적 발표",
            "ticker":     tk,
            "importance": "high" if tk in ("NVDA", "MSFT", "GOOGL") else "mid",
            "d_day":      d_day,
        })
    return out


def get_calendar_events() -> list[dict]:
    """전체 매크로/실적 이벤트를 날짜순 정렬해 반환 (-7일 ~ +90일)."""
    today = datetime.date.today()
    cutoff_past = today - datetime.timedelta(days=7)
    cutoff_future = today + datetime.timedelta(days=90)

    events = _fomc_events() + _bok_events()
    try:
        events += _earnings_events()
    except Exception as e:
        print(f"  [경고] 실적 캘린더 조회 실패: {e}")

    events = [
        e for e in events
        if cutoff_past <= datetime.date.fromisoformat(e["date"]) <= cutoff_future
    ]
    events.sort(key=lambda x: x["date"])
    return events


if __name__ == "__main__":
    evs = get_calendar_events()
    print(f"총 {len(evs)}개 이벤트")
    for e in evs[:15]:
        print(f"  D{e['d_day']:+4} {e['date']} [{e['type']:<8}] {e['title']} ({e['importance']})")
