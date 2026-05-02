import os
import sys
import datetime
from notion_client import Client
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

notion = Client(auth=os.getenv("NOTION_TOKEN"))
HOLDINGS_DB = os.getenv("NOTION_HOLDINGS_DB")
SNAPSHOT_DB = os.getenv("NOTION_SNAPSHOT_DB")
TRANSACTIONS_DB = os.getenv("NOTION_TRANSACTIONS_DB")


def _text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": str(value)}}]}


def _title(value: str) -> dict:
    return {"title": [{"text": {"content": str(value)}}]}


def _number(value) -> dict:
    return {"number": round(float(value), 6) if value is not None else None}


def _date(value: str) -> dict:
    return {"date": {"start": value}}


def _select(value: str) -> dict:
    return {"select": {"name": str(value)}}


def upsert_holding(ticker: str, props: dict) -> None:
    """Holdings DB — '티커' rich_text로 기존 행 검색 후 upsert."""
    existing = notion.databases.query(
        database_id=HOLDINGS_DB,
        filter={"property": "티커", "rich_text": {"equals": ticker}},
    )

    page_props = {
        "종목": _title(props.get("name", ticker)),
        "티커": _text(ticker),
        "보유수량": _number(props.get("quantity", 0)),
        "평균단가": _number(props.get("avg_price", 0)),
        "현재가": _number(props.get("current_price", 0)),
        "평가금액(KRW)": _number(props.get("eval_krw", 0)),
        "수익률%": _number(props.get("profit_rate", 0)),
        "최종갱신": _date(datetime.date.today().isoformat()),
    }

    account = props.get("account", "")
    if account:
        page_props["계좌"] = _select(account)

    asset_class = props.get("asset_class", "")
    if asset_class:
        page_props["자산군"] = _select(asset_class)

    if existing["results"]:
        notion.pages.update(
            page_id=existing["results"][0]["id"],
            properties=page_props,
        )
    else:
        notion.pages.create(
            parent={"database_id": HOLDINGS_DB},
            properties=page_props,
        )


# 계좌 키(소문자) → Snapshots DB 컬럼명 매핑 (Phase 2 D-1 비교용)
_ACCOUNT_KEY_TO_COLUMN = {
    "kis_jonghap": "KIS_종합_KRW",
    "kis_isa":     "KIS_ISA_KRW",
    "kis_yeon":    "KIS_연저_KRW",
    "okx":         "OKX_KRW",
    "upbit":       "Upbit_KRW",
    "toss":        "Toss_KRW",
    "emergency":   "Bank_비상금_KRW",
    "biz":         "Bank_사업자금_KRW",
}


def ensure_snapshot_crypto_columns() -> None:
    """Snapshots DB에 크립토 지표 number 컬럼 4종 보장 (있으면 무시)."""
    if not SNAPSHOT_DB:
        return
    new_props = {
        "BTC도미넌스":   {"number": {"format": "number"}},
        "USDT도미넌스":  {"number": {"format": "number"}},
        "ETHBTC비율":    {"number": {"format": "number"}},
        "TOTAL3시총":    {"number": {"format": "number"}},
    }
    try:
        notion.databases.update(database_id=SNAPSHOT_DB, properties=new_props)
        print("  Snapshots DB 크립토 컬럼 4종 보장 완료")
    except Exception as e:
        print(f"  [경고] Snapshots 크립토 컬럼 보장 실패: {e}")


def ensure_snapshot_account_columns() -> None:
    """Snapshots DB에 계좌별 KRW number 컬럼 8종 보장 (있으면 무시). 멱등 안전망."""
    if not SNAPSHOT_DB:
        return
    new_props = {
        col: {"number": {"format": "number"}}
        for col in _ACCOUNT_KEY_TO_COLUMN.values()
    }
    try:
        notion.databases.update(database_id=SNAPSHOT_DB, properties=new_props)
        print("  Snapshots DB 계좌별 KRW 컬럼 8종 보장 완료")
    except Exception as e:
        print(f"  [경고] Snapshots 계좌별 KRW 컬럼 보장 실패: {e}")


def add_snapshot(snapshot: dict) -> None:
    """Daily Snapshots DB — '날짜' date로 오늘 행 upsert."""
    today = datetime.date.today().isoformat()

    existing = notion.databases.query(
        database_id=SNAPSHOT_DB,
        filter={"property": "날짜", "date": {"equals": today}},
    )

    page_props = {
        "Name": _title(today),
        "날짜": _date(today),
        "총자산": _number(snapshot.get("total_krw", 0)),
        "크립토": _number(snapshot.get("crypto_krw", 0)),
        "주식": _number(snapshot.get("stock_krw", 0)),
        "금": _number(snapshot.get("gold_krw", 0)),
        "원유": _number(snapshot.get("oil_krw", 0)),
        "채권": _number(snapshot.get("bond_krw", 0)),
        "현금": _number(snapshot.get("cash_krw", 0)),
        "위험자산%": _number(snapshot.get("risky_pct", 0)),
        "안전자산%": _number(snapshot.get("safe_pct", 0)),
        "USD/KRW": _number(snapshot.get("usdkrw", 0)),
        "MDD%": _number(snapshot.get("mdd", 0)),
        "전일대비%": _number(snapshot.get("daily_change_pct", 0)),
    }

    if snapshot.get("btc_price"):
        page_props["BTC가격"] = _number(snapshot["btc_price"])
    if snapshot.get("eth_price"):
        page_props["ETH가격"] = _number(snapshot["eth_price"])

    # 크립토 지표 4종 (어제 대비 변화율 계산용)
    ci = snapshot.get("crypto_indicators", {}) or {}
    if ci.get("btc_dominance") is not None:
        page_props["BTC도미넌스"]  = _number(ci["btc_dominance"])
    if ci.get("usdt_dominance") is not None:
        page_props["USDT도미넌스"] = _number(ci["usdt_dominance"])
    if ci.get("eth_btc_ratio") is not None:
        page_props["ETHBTC비율"]   = _number(ci["eth_btc_ratio"])
    if ci.get("total3_market_cap") is not None:
        page_props["TOTAL3시총"]   = _number(ci["total3_market_cap"])

    # 계좌별 KRW 8종 (D-1 비교용)
    accounts_krw = snapshot.get("accounts_krw", {}) or {}
    for key, col in _ACCOUNT_KEY_TO_COLUMN.items():
        val = accounts_krw.get(key)
        if val is not None:
            page_props[col] = _number(val)

    if existing["results"]:
        notion.pages.update(
            page_id=existing["results"][0]["id"],
            properties=page_props,
        )
        print(f"  스냅샷 업데이트: {today}")
    else:
        notion.pages.create(
            parent={"database_id": SNAPSHOT_DB},
            properties=page_props,
        )
        print(f"  스냅샷 생성: {today}")


def sync_holdings(holdings: list[dict]) -> None:
    print(f"  Holdings DB 동기화 중 ({len(holdings)}개 종목)...")
    for h in holdings:
        try:
            upsert_holding(h["ticker"], h)
        except Exception as e:
            print(f"    [경고] {h['ticker']} 업데이트 실패: {e}")
    print("  완료.")


# ── 노션 → 파이프라인 양방향 동기화 ──────────────────────────────────


_ACCOUNT_NOTION_LABEL = {
    "toss": "토스",
    "emergency": "비상금",
    "biz": "사업",
}


def pull_manual_holdings() -> dict:
    """Holdings DB에서 수동 입력 계좌 평가금액 + 원금 읽어 반환.

    반환 구조:
      {
        "toss":      {"eval": 22000000, "principal": 20000000, "pnl": 2000000, "pnl_pct": 10.0},
        "emergency": {"eval": 20000000},
        "biz":       {"eval": 10000000},
      }
    원금(KRW) 컬럼이 없거나 0이면 pnl 필드 미포함.
    """
    result: dict = {"toss": {"eval": 0}, "emergency": {"eval": 0}, "biz": {"eval": 0}}
    # 원금 컬럼 후보 (사용자가 어떤 명칭으로 입력했을 수 있음)
    PRINCIPAL_KEYS = ("원금(KRW)", "원금", "원금 (KRW)", "원금(₩)", "Principal", "원금KRW")
    EVAL_KEYS = ("평가금액(KRW)", "평가금액", "평가금액 (KRW)", "Eval")

    for account_id in result:
        label = _ACCOUNT_NOTION_LABEL.get(account_id, account_id)
        try:
            rows = notion.databases.query(
                database_id=HOLDINGS_DB,
                filter={"property": "계좌", "select": {"equals": label}},
            )

            # toss: 디버그 출력 (NVDA 원금 인식 확인)
            if account_id == "toss" and rows["results"]:
                print(f"  [DEBUG toss 행 {len(rows['results'])}개]")
                for row in rows["results"]:
                    props = row["properties"]
                    name_p = props.get("종목", {}).get("title", [])
                    nm = name_p[0]["plain_text"] if name_p else "?"
                    nums = []
                    for k, v in props.items():
                        if v.get("type") == "number" and v.get("number") is not None:
                            nums.append(f"{k}={v['number']}")
                    print(f"    [{nm}] {' | '.join(nums) if nums else '(number 컬럼 없음)'}")

            def _sum_col(rows_list, candidates):
                for col in candidates:
                    s = sum(
                        (row["properties"].get(col, {}).get("number") or 0)
                        for row in rows_list
                    )
                    if s > 0:
                        return s, col
                return 0, None

            total_eval, eval_col = _sum_col(rows["results"], EVAL_KEYS)
            total_principal, princ_col = _sum_col(rows["results"], PRINCIPAL_KEYS)

            if account_id == "toss":
                print(f"  [DEBUG toss] 평가합계={total_eval:,.0f} (col={eval_col}) | 원금합계={total_principal:,.0f} (col={princ_col})")

            if total_eval > 0:
                result[account_id]["eval"] = round(total_eval)
            if total_principal > 0:
                pnl = total_eval - total_principal
                result[account_id]["principal"] = round(total_principal)
                result[account_id]["pnl"] = round(pnl)
                result[account_id]["pnl_pct"] = round(pnl / total_principal * 100, 2) if total_principal else 0.0
        except Exception as e:
            if account_id == "toss":
                print(f"  [경고] toss 조회 실패: {e}")
    return result


def get_yesterday_snapshot() -> dict | None:
    """Snapshots DB에서 어제 행 반환."""
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    try:
        rows = notion.databases.query(
            database_id=SNAPSHOT_DB,
            filter={"property": "날짜", "date": {"equals": yesterday}},
        )
        if not rows["results"]:
            return None
        props = rows["results"][0]["properties"]

        def _num(key):
            return props.get(key, {}).get("number") or 0

        accounts_krw = {
            key: _num(col)
            for key, col in _ACCOUNT_KEY_TO_COLUMN.items()
        }
        return {
            "total_krw": _num("총자산"),
            "crypto_krw": _num("크립토"),
            "stock_krw": _num("주식"),
            "gold_krw": _num("금"),
            "oil_krw": _num("원유"),
            "bond_krw": _num("채권"),
            "cash_krw": _num("현금"),
            "risky_pct": _num("위험자산%"),
            "safe_pct": _num("안전자산%"),
            "crypto_indicators": {
                "btc_dominance":     _num("BTC도미넌스") or None,
                "usdt_dominance":    _num("USDT도미넌스") or None,
                "eth_btc_ratio":     _num("ETHBTC비율") or None,
                "total3_market_cap": _num("TOTAL3시총") or None,
            },
            "accounts_krw": accounts_krw,
        }
    except Exception as e:
        print(f"  [경고] 어제 스냅샷 조회 실패: {e}")
        return None


def get_capital_gains_ytd() -> float:
    """Transactions DB에서 올해 매도 기록의 매매차익(KRW) 합계 반환."""
    if not TRANSACTIONS_DB:
        return 0.0
    year_start = f"{datetime.date.today().year}-01-01"
    try:
        rows = notion.databases.query(
            database_id=TRANSACTIONS_DB,
            filter={
                "and": [
                    {"property": "거래유형", "select": {"equals": "매도"}},
                    {"property": "거래일", "date": {"on_or_after": year_start}},
                ]
            },
        )
        return sum(
            (row["properties"].get("매매차익(KRW)", {}).get("number") or 0)
            for row in rows["results"]
        )
    except Exception as e:
        print(f"  [경고] 양도세 계산 실패: {e}")
        return 0.0


def get_sparkline_history(days: int = 30) -> list[dict]:
    """Snapshots DB에서 최근 N일 데이터 반환 (스파크라인용)."""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    try:
        rows = notion.databases.query(
            database_id=SNAPSHOT_DB,
            filter={"property": "날짜", "date": {"on_or_after": since}},
            sorts=[{"property": "날짜", "direction": "ascending"}],
        )
        result = []
        for row in rows["results"]:
            props = row["properties"]

            def _num(key):
                return props.get(key, {}).get("number") or 0

            date_node = props.get("날짜", {}).get("date") or {}
            result.append({
                "date": date_node.get("start", ""),
                "total": _num("총자산"),
                "crypto": _num("크립토"),
                "stock": _num("주식"),
                "gold": _num("금"),
                "oil": _num("원유"),
                "bond": _num("채권"),
                "cash": _num("현금"),
            })
        return result
    except Exception as e:
        print(f"  [경고] 스파크라인 히스토리 조회 실패: {e}")
        return []


def get_nvda_avg_price() -> float:
    """Holdings DB에서 토스 계좌 NVDA 평균단가(USD) 반환."""
    try:
        rows = notion.databases.query(
            database_id=HOLDINGS_DB,
            filter={
                "and": [
                    {"property": "티커", "rich_text": {"contains": "NVDA"}},
                    {"property": "계좌", "select": {"equals": "토스"}},
                ]
            },
        )
        if rows["results"]:
            return rows["results"][0]["properties"].get("평균단가", {}).get("number") or 0.0
    except Exception as e:
        print(f"  [경고] NVDA 평단 조회 실패: {e}")
    return 0.0


# ── Transactions DB ──────────────────────────────────────────────────────────


_EXCHANGE_LABEL = {
    "upbit": "업비트", "okx": "OKX",
    "kis_jonghap": "한투 종합", "kis_isa": "한투 ISA", "kis_yeon": "한투 연저",
}


def ensure_transactions_columns() -> None:
    """Transactions DB 필수 컬럼 보장 (이미 있으면 노션이 무시)."""
    if not TRANSACTIONS_DB:
        return
    new_props = {
        "거래일":       {"date": {}},
        "거래유형":     {"select": {"options": [
            {"name": "매수", "color": "green"},
            {"name": "매도", "color": "red"},
        ]}},
        "거래소":       {"select": {}},
        "계좌":         {"select": {}},
        "티커":         {"rich_text": {}},
        "수량":         {"number": {"format": "number"}},
        "단가":         {"number": {"format": "number"}},
        "금액":         {"number": {"format": "number"}},
        "금액(KRW)":    {"number": {"format": "won"}},
        "수수료":       {"number": {"format": "number"}},
        "UID":          {"rich_text": {}},
        "메모":         {"rich_text": {}},
        "30일결과%":    {"number": {"format": "percent"}},
    }
    try:
        notion.databases.update(database_id=TRANSACTIONS_DB, properties=new_props)
        print("  Transactions DB 컬럼 보장 완료")
    except Exception as e:
        print(f"  [경고] Transactions 컬럼 보장 실패: {e}")


def _existing_transaction_uids() -> set[str]:
    """Transactions DB에서 기존 UID 집합을 한 번에 가져옴 (중복 방지용)."""
    if not TRANSACTIONS_DB:
        return set()
    uids: set[str] = set()
    cursor = None
    try:
        while True:
            kwargs: dict = {"database_id": TRANSACTIONS_DB, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            res = notion.databases.query(**kwargs)
            for row in res.get("results", []):
                rt = row["properties"].get("UID", {}).get("rich_text", []) or []
                if rt:
                    uids.add(rt[0]["plain_text"])
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
    except Exception as e:
        print(f"  [경고] 기존 거래 UID 조회 실패: {e}")
    return uids


def sync_transactions(transactions: list[dict]) -> int:
    """신규 거래만 노션 Transactions DB에 추가 (UID 기반 dedup). 추가 건수 반환."""
    if not TRANSACTIONS_DB or not transactions:
        return 0
    existing = _existing_transaction_uids()
    added = 0
    for t in transactions:
        uid = t.get("uid")
        if not uid or uid in existing:
            continue
        try:
            page_props = {
                "종목":     _title(t.get("name", t.get("ticker", ""))),
                "티커":     _text(t.get("ticker", "")),
                "거래일":   _date(t.get("date") or datetime.date.today().isoformat()),
                "거래유형": _select("매수" if t.get("side") == "buy" else "매도"),
                "거래소":   _select(_EXCHANGE_LABEL.get(t.get("exchange", ""), t.get("exchange", ""))),
                "계좌":     _select(t.get("account", "")),
                "수량":     _number(t.get("qty", 0)),
                "단가":     _number(t.get("price", 0)),
                "금액":     _number(t.get("amount", 0)),
                "금액(KRW)":_number(t.get("amount_krw", 0)),
                "수수료":   _number(t.get("fee", 0)),
                "UID":      _text(uid),
            }
            notion.pages.create(parent={"database_id": TRANSACTIONS_DB}, properties=page_props)
            added += 1
        except Exception as e:
            print(f"    [경고] 거래 추가 실패 {uid}: {e}")
    if added:
        print(f"  Transactions DB: {added}건 신규 추가 ({len(transactions)-added}건 기존)")
    return added


def pull_transactions(days: int = 90) -> list[dict]:
    """Transactions DB에서 최근 N일 거래 리스트 반환 (메모 포함, 매매일지 탭용)."""
    if not TRANSACTIONS_DB:
        return []
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out: list[dict] = []
    cursor = None
    try:
        while True:
            kwargs: dict = {
                "database_id": TRANSACTIONS_DB,
                "page_size": 100,
                "filter": {"property": "거래일", "date": {"on_or_after": since}},
                "sorts": [{"property": "거래일", "direction": "descending"}],
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            res = notion.databases.query(**kwargs)
            for row in res.get("results", []):
                p = row["properties"]

                def _rt(key):
                    arr = p.get(key, {}).get("rich_text", []) or []
                    return arr[0]["plain_text"] if arr else ""

                def _tit(key):
                    arr = p.get(key, {}).get("title", []) or []
                    return arr[0]["plain_text"] if arr else ""

                def _sel(key):
                    s = p.get(key, {}).get("select")
                    return s.get("name") if s else ""

                def _num(key):
                    return p.get(key, {}).get("number") or 0

                def _dt(key):
                    d = p.get(key, {}).get("date")
                    return d.get("start") if d else ""

                out.append({
                    "page_id":  row["id"],
                    "uid":      _rt("UID"),
                    "date":     _dt("거래일"),
                    "side":     "buy" if _sel("거래유형") == "매수" else "sell",
                    "exchange": _sel("거래소"),
                    "account":  _sel("계좌"),
                    "name":     _tit("종목"),
                    "ticker":   _rt("티커"),
                    "qty":      _num("수량"),
                    "price":    _num("단가"),
                    "amount":   _num("금액"),
                    "amount_krw": _num("금액(KRW)"),
                    "fee":      _num("수수료"),
                    "memo":     _rt("메모"),
                    "result_30d": p.get("30일결과%", {}).get("number"),
                })
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
    except Exception as e:
        print(f"  [경고] 거래내역 조회 실패: {e}")
    return out


def update_transaction_review(page_id: str, result_pct: float) -> None:
    """30일 후 결과(%)를 Transactions DB에 기록 (review_engine용)."""
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"30일결과%": _number(result_pct / 100)},  # percent format
        )
    except Exception as e:
        print(f"  [경고] 회고 업데이트 실패 {page_id}: {e}")


if __name__ == "__main__":
    test_snapshot = {
        "total_krw": 88_000_000,
        "crypto_krw": 45_000_000,
        "stock_krw": 32_000_000,
        "gold_krw": 2_000_000,
        "oil_krw": 1_000_000,
        "bond_krw": 5_000_000,
        "cash_krw": 3_000_000,
        "risky_pct": 77.6,
        "safe_pct": 22.4,
        "usdkrw": 1473.0,
        "mdd": 0.0,
        "daily_change_pct": 0.0,
        "btc_price": 114_000_000,
        "eth_price": 3_400_000,
    }
    add_snapshot(test_snapshot)
    print("노션 스냅샷 테스트 완료.")
