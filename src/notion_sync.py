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

    # Holdings 실제 컬럼명 매핑
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

    # Snapshot 실제 컬럼명 매핑
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
