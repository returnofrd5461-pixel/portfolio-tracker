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
    """Holdings DB에서 수동 입력 계좌(toss/emergency/biz) 평가금액 합계 반환."""
    result = {"toss": 0, "emergency": 0, "biz": 0}
    for account_id in result:
        label = _ACCOUNT_NOTION_LABEL.get(account_id, account_id)
        try:
            rows = notion.databases.query(
                database_id=HOLDINGS_DB,
                filter={"property": "계좌", "select": {"equals": label}},
            )
            total = sum(
                (row["properties"].get("평가금액(KRW)", {}).get("number") or 0)
                for row in rows["results"]
            )
            if total > 0:
                result[account_id] = round(total)
        except Exception:
            pass  # 해당 선택지가 없으면 0 반환 (기존 data.json 값 사용)
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
