"""
매매일지 분석 엔진 v1
=====================

목적:
- 노션 Transactions DB의 거래 데이터를 분석/집계
- 대시보드 매매일지 탭이 읽을 JSON 생성
- 메모/태그 미입력 거래 검출 → Slack 알림용 메시지 생성

데이터가 적은 단계 (거래 < 100건) 가정 v1 기능:
1. 기본 통계 (오늘/주/월 거래수, 거래소·종목별 카운트)
2. 종목별 P&L (단순 가중평단)
3. 매수 가격대 분포 (BTC/ETH 한정)
4. 미입력 거래 검출 (메모+태그 둘 다 빈 거래)

미적용 (v2 — 데이터 누적 후):
- 시간대/요일별 패턴, 승률, 평균 보유기간, 전략태그별 성과

CLI 실행:
    python -m src.trading_journal
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from notion_client import Client


# ============================================================
# 설정 상수
# ============================================================
KST = timezone(timedelta(hours=9))
TRANSACTIONS_DB_ID = os.environ.get("NOTION_TRANSACTIONS_DB", "65bce105-978d-4359-8455-6f7fcddd5825")
DEFAULT_OUTPUT_PATH = Path("data/trading_journal.json")
NOTION_TX_DB_PUBLIC_URL = "https://www.notion.so/65bce105978d435984556f7fcddd5825"

# 가격 분포 분석 대상 (코인만)
PRICE_DISTRIBUTION_TICKERS = ["BTC", "ETH"]

# 리밸런싱 거래는 자동 분류라 메모 강제 안 함
SKIP_UNLOGGED_FOR_REBALANCING = True


# ============================================================
# 1. 노션 데이터 추출
# ============================================================
def fetch_all_transactions(
    notion: Client, db_id: str = TRANSACTIONS_DB_ID
) -> list[dict]:
    """노션 Transactions DB 전체 페이지 가져오기 (페이지네이션 포함)."""
    all_results: list[dict] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "database_id": db_id,
            "page_size": 100,
            "sorts": [{"property": "거래일", "direction": "descending"}],
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        all_results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return [normalize_transaction(p) for p in all_results]


def _prop(props: dict, name: str) -> dict:
    return props.get(name, {}) or {}


def _select(prop: dict) -> str | None:
    s = prop.get("select")
    return s.get("name") if s else None


def _multi_select(prop: dict) -> list[str]:
    items = prop.get("multi_select") or []
    return [it.get("name") for it in items if it.get("name")]


def _text(prop: dict) -> str:
    if "title" in prop:
        return "".join(t.get("plain_text", "") for t in (prop.get("title") or []))
    if "rich_text" in prop:
        return "".join(t.get("plain_text", "") for t in (prop.get("rich_text") or []))
    return ""


def _number(prop: dict) -> float:
    return prop.get("number") or 0


def _date(prop: dict) -> str | None:
    d = prop.get("date")
    return d.get("start") if d else None


def normalize_transaction(page: dict) -> dict:
    """노션 raw page → 평탄화된 거래 dict."""
    props = page.get("properties", {})
    return {
        "page_id": page["id"],
        "page_url": page.get("url"),
        "ticker": _text(_prop(props, "티커")),
        "name": _text(_prop(props, "종목")),
        "exchange": _select(_prop(props, "거래소")),
        "account": _select(_prop(props, "계좌")),
        "side": _select(_prop(props, "매수/매도")),  # 매수/매도/리밸런싱
        "trade_type": _select(_prop(props, "거래유형")),  # 매수/매도
        "date": _date(_prop(props, "거래일")),
        "qty": _number(_prop(props, "수량")),
        "price": _number(_prop(props, "단가")),
        "amount": _number(_prop(props, "금액")),
        "amount_krw": _number(_prop(props, "금액(KRW)")),
        "fee": _number(_prop(props, "수수료")),
        "fx_rate": _number(_prop(props, "적용환율")),
        "tranche": _text(_prop(props, "차수")),
        "memo": _text(_prop(props, "메모")),
        "tags": _multi_select(_prop(props, "전략태그")),
        "result_30d_pct": _prop(props, "30일결과%").get("number"),
        "uid": _text(_prop(props, "UID")),
        "created_time": page.get("created_time"),
    }


# ============================================================
# 2. 기본 통계
# ============================================================
def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(KST)
        return datetime.fromisoformat(s).replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


def compute_basic_stats(txs: list[dict]) -> dict:
    """전체/오늘/주/월 거래수 + 거래소·종목·매매방향별 카운트."""
    now = _now_kst()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())  # 이번 주 월요일
    month_start = today.replace(day=1)

    today_count = 0
    week_count = 0
    month_count = 0
    by_exchange: Counter[str] = Counter()
    by_ticker: Counter[str] = Counter()
    by_side: Counter[str] = Counter()
    by_account: Counter[str] = Counter()

    for tx in txs:
        d = _parse_date(tx["date"])
        if d:
            d_date = d.date()
            if d_date == today:
                today_count += 1
            if d_date >= week_start:
                week_count += 1
            if d_date >= month_start:
                month_count += 1

        if tx["exchange"]:
            by_exchange[tx["exchange"]] += 1
        if tx["account"]:
            by_account[tx["account"]] += 1
        ticker_key = tx["ticker"] or tx["name"]
        if ticker_key:
            by_ticker[ticker_key] += 1
        if tx["side"]:
            by_side[tx["side"]] += 1

    return {
        "total": len(txs),
        "today": today_count,
        "this_week": week_count,
        "this_month": month_count,
        "by_exchange": dict(by_exchange),
        "by_account": dict(by_account),
        "by_ticker_top10": dict(by_ticker.most_common(10)),
        "by_side": dict(by_side),
    }


# ============================================================
# 3. 종목별 P&L (가중 평단)
# ============================================================
def compute_position_pnl(
    txs: list[dict],
    current_prices: dict[str, float] | None = None,
) -> list[dict]:
    """종목별 평단 / 보유수량 / 평가금액 / 손익 / 수익률.

    평단 계산: 단순 가중평균 (FIFO 미적용 — 데이터 적은 단계에서 충분)
    current_prices: {ticker: 현재가_KRW}
    """
    current_prices = current_prices or {}

    by_ticker: dict[str, dict] = defaultdict(
        lambda: {
            "ticker": None,
            "name": None,
            "exchange": None,
            "buy_qty": 0.0,
            "buy_amount_krw": 0.0,
            "sell_qty": 0.0,
            "sell_amount_krw": 0.0,
            "tx_count": 0,
            "first_buy_date": None,
            "last_tx_date": None,
        }
    )

    for tx in txs:
        ticker_key = tx["ticker"] or tx["name"]
        if not ticker_key:
            continue

        pos = by_ticker[ticker_key]
        pos["ticker"] = tx["ticker"] or ticker_key
        pos["name"] = tx["name"] or pos["name"]
        pos["exchange"] = tx["exchange"] or pos["exchange"]
        pos["tx_count"] += 1

        date_str = tx["date"]
        if date_str:
            if not pos["last_tx_date"] or date_str > pos["last_tx_date"]:
                pos["last_tx_date"] = date_str

        side = tx["side"] or tx["trade_type"]
        if side == "매수":
            pos["buy_qty"] += tx["qty"]
            pos["buy_amount_krw"] += tx["amount_krw"]
            if date_str and (not pos["first_buy_date"] or date_str < pos["first_buy_date"]):
                pos["first_buy_date"] = date_str
        elif side == "매도":
            pos["sell_qty"] += tx["qty"]
            pos["sell_amount_krw"] += tx["amount_krw"]

    results = []
    for ticker_key, pos in by_ticker.items():
        net_qty = pos["buy_qty"] - pos["sell_qty"]
        avg_buy_price_krw = (
            pos["buy_amount_krw"] / pos["buy_qty"] if pos["buy_qty"] > 0 else 0.0
        )

        cur_price = current_prices.get(pos["ticker"]) or current_prices.get(ticker_key) or 0
        eval_krw = cur_price * net_qty if (cur_price and net_qty > 0) else 0.0
        cost_krw = avg_buy_price_krw * net_qty if net_qty > 0 else 0.0
        unrealized_pnl_krw = eval_krw - cost_krw if cost_krw > 0 else 0.0
        unrealized_pnl_pct = (
            (unrealized_pnl_krw / cost_krw * 100) if cost_krw > 0 else 0.0
        )

        # 실현손익: 매도분 = 매도금액 - (평단 × 매도수량)
        realized_pnl_krw = (
            pos["sell_amount_krw"] - (avg_buy_price_krw * pos["sell_qty"])
            if pos["sell_qty"] > 0
            else 0.0
        )

        results.append(
            {
                "ticker": pos["ticker"],
                "name": pos["name"],
                "exchange": pos["exchange"],
                "tx_count": pos["tx_count"],
                "buy_qty": round(pos["buy_qty"], 8),
                "sell_qty": round(pos["sell_qty"], 8),
                "net_qty": round(net_qty, 8),
                "avg_buy_price_krw": round(avg_buy_price_krw, 2),
                "current_price_krw": round(cur_price, 2),
                "cost_krw": round(cost_krw, 0),
                "eval_krw": round(eval_krw, 0),
                "unrealized_pnl_krw": round(unrealized_pnl_krw, 0),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "realized_pnl_krw": round(realized_pnl_krw, 0),
                "first_buy_date": pos["first_buy_date"],
                "last_tx_date": pos["last_tx_date"],
            }
        )

    # 평가금액 큰 순 정렬
    results.sort(key=lambda x: x["eval_krw"] or x["cost_krw"], reverse=True)
    return results


# ============================================================
# 4. 매수 가격대 분포 (BTC/ETH)
# ============================================================
def compute_price_distribution(
    txs: list[dict],
    ticker: str,
    bucket_count: int = 10,
) -> dict | None:
    """티커별 매수 가격대 히스토그램 + 통계."""
    samples = []
    for tx in txs:
        if (tx["ticker"] or "").upper() != ticker.upper():
            continue
        if (tx["side"] or tx["trade_type"]) != "매수":
            continue
        if tx["price"] > 0 and tx["qty"] > 0:
            samples.append(
                {
                    "price": tx["price"],
                    "qty": tx["qty"],
                    "amount_krw": tx["amount_krw"],
                    "date": tx["date"],
                }
            )

    if not samples:
        return None

    sorted_prices = sorted(s["price"] for s in samples)
    p_min = sorted_prices[0]
    p_max = sorted_prices[-1]
    median = sorted_prices[len(sorted_prices) // 2]
    avg = sum(sorted_prices) / len(sorted_prices)
    total_qty = sum(s["qty"] for s in samples)
    weighted_avg = (
        sum(s["price"] * s["qty"] for s in samples) / total_qty
        if total_qty > 0
        else avg
    )

    # 단일 가격일 경우 단일 bucket
    if p_min == p_max:
        return {
            "ticker": ticker,
            "min": p_min,
            "max": p_max,
            "avg": avg,
            "median": median,
            "weighted_avg": weighted_avg,
            "tx_count": len(samples),
            "total_qty": round(total_qty, 8),
            "buckets": [
                {
                    "low": p_min,
                    "high": p_max,
                    "count": len(samples),
                    "qty": round(total_qty, 8),
                }
            ],
        }

    bucket_size = (p_max - p_min) / bucket_count
    buckets = [
        {
            "low": p_min + i * bucket_size,
            "high": p_min + (i + 1) * bucket_size,
            "count": 0,
            "qty": 0.0,
        }
        for i in range(bucket_count)
    ]

    for s in samples:
        idx = min(int((s["price"] - p_min) / bucket_size), bucket_count - 1)
        buckets[idx]["count"] += 1
        buckets[idx]["qty"] += s["qty"]

    for b in buckets:
        b["qty"] = round(b["qty"], 8)
        b["low"] = round(b["low"], 2)
        b["high"] = round(b["high"], 2)

    return {
        "ticker": ticker,
        "min": round(p_min, 2),
        "max": round(p_max, 2),
        "avg": round(avg, 2),
        "median": round(median, 2),
        "weighted_avg": round(weighted_avg, 2),
        "tx_count": len(samples),
        "total_qty": round(total_qty, 8),
        "buckets": buckets,
    }


# ============================================================
# 5. 미입력 거래 검출
# ============================================================
def find_unlogged_transactions(
    txs: list[dict], skip_rebalancing: bool = SKIP_UNLOGGED_FOR_REBALANCING
) -> list[dict]:
    """메모와 태그 둘 다 빈 거래 = 미입력."""
    unlogged = []
    for tx in txs:
        if skip_rebalancing and (tx["side"] == "리밸런싱" or "리밸런싱" in (tx["tags"] or [])):
            continue

        memo_empty = not (tx["memo"] or "").strip()
        tags_empty = not (tx["tags"] or [])
        if memo_empty and tags_empty:
            unlogged.append(
                {
                    "page_id": tx["page_id"],
                    "page_url": tx["page_url"],
                    "ticker": tx["ticker"],
                    "name": tx["name"],
                    "exchange": tx["exchange"],
                    "side": tx["side"],
                    "date": tx["date"],
                    "amount_krw": tx["amount_krw"],
                    "uid": tx["uid"],
                }
            )

    # 최근 거래 우선 (메모 채울 때 기억 살아있는 순)
    unlogged.sort(key=lambda x: x["date"] or "", reverse=True)
    return unlogged


# ============================================================
# 6. 리포트 페이로드
# ============================================================
def build_dashboard_payload(
    txs: list[dict],
    stats: dict,
    positions: list[dict],
    distributions: dict[str, dict | None],
    unlogged: list[dict],
) -> dict:
    """대시보드 매매일지 탭이 fetch할 단일 JSON."""
    return {
        "generated_at": _now_kst().isoformat(),
        "stats": stats,
        "positions": positions,
        "price_distributions": {k: v for k, v in distributions.items() if v},
        "unlogged_count": len(unlogged),
        "unlogged_recent": unlogged[:20],
        "transactions": txs,  # 전체 거래 (대시보드 리스트뷰용)
    }


def build_slack_unlogged_alert(
    unlogged: list[dict],
    notion_db_url: str = NOTION_TX_DB_PUBLIC_URL,
) -> dict | None:
    """미입력 거래가 있으면 Slack Block Kit 메시지 dict 반환. 없으면 None."""
    if not unlogged:
        return None

    now = _now_kst()
    today_str = now.strftime("%Y-%m-%d")
    week_ago_str = (now - timedelta(days=7)).date().isoformat()

    today_count = sum(1 for u in unlogged if (u["date"] or "")[:10] == today_str)
    week_count = sum(1 for u in unlogged if (u["date"] or "")[:10] >= week_ago_str)

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📝 매매일지 미입력 {len(unlogged)}건",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*오늘:* {today_count}건  ·  *최근 7일:* {week_count}건  ·  "
                    f"*전체:* {len(unlogged)}건"
                ),
            },
        },
    ]

    show = unlogged[:5]
    if show:
        lines = []
        for u in show:
            side = u["side"] or "?"
            emoji = "🟢" if side == "매수" else "🔴" if side == "매도" else "⚪"
            ticker = u["ticker"] or u["name"] or "?"
            amount_str = f"₩{int(u['amount_krw']):,}" if u["amount_krw"] else ""
            date_short = (u["date"] or "")[:10]
            lines.append(f"{emoji} `{ticker}` {side} {amount_str} · {date_short}")
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "노션에서 채우기 →"},
                    "url": notion_db_url,
                }
            ],
        }
    )

    return {
        "blocks": blocks,
        "text": f"매매일지 미입력 {len(unlogged)}건",
    }


# ============================================================
# 7. 진입점
# ============================================================
def run(
    notion: Client,
    current_prices: dict[str, float] | None = None,
    db_id: str = TRANSACTIONS_DB_ID,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> dict:
    """전체 파이프라인 실행 → payload 반환 + JSON 파일 저장."""
    current_prices = current_prices or {}

    print("[trading_journal] 거래 데이터 fetch...")
    txs = fetch_all_transactions(notion, db_id)
    print(f"[trading_journal] {len(txs)}건 로드")

    stats = compute_basic_stats(txs)
    positions = compute_position_pnl(txs, current_prices)
    distributions = {
        t: compute_price_distribution(txs, t) for t in PRICE_DISTRIBUTION_TICKERS
    }
    unlogged = find_unlogged_transactions(txs)

    payload = build_dashboard_payload(
        txs, stats, positions, distributions, unlogged
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[trading_journal] 저장: {output_path} ({output_path.stat().st_size:,} bytes)")

    return payload


def main():
    """python -m src.trading_journal — 단독 실행 (테스트용)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # dotenv 없으면 환경변수 직접 사용

    notion_token = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY")
    if not notion_token:
        raise SystemExit("NOTION_TOKEN (or NOTION_API_KEY) env var not set")

    notion = Client(auth=notion_token)
    payload = run(notion, current_prices={})

    print("\n=== 기본 통계 ===")
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))

    print(f"\n=== 미입력 거래: {payload['unlogged_count']}건 ===")
    for u in payload["unlogged_recent"][:10]:
        amount = f"₩{int(u['amount_krw']):,}" if u["amount_krw"] else ""
        print(
            f"  - [{(u['date'] or '')[:10]}] "
            f"{u['ticker'] or u['name']} {u['side']} {amount}"
        )

    print(f"\n=== 종목별 P&L (상위 5개) ===")
    for p in payload["positions"][:5]:
        print(
            f"  - {p['ticker']:8s} 보유 {p['net_qty']:.4f} · "
            f"평단 ₩{p['avg_buy_price_krw']:,.0f} · "
            f"평가 ₩{p['eval_krw']:,.0f} ({p['unrealized_pnl_pct']:+.2f}%)"
        )


if __name__ == "__main__":
    main()
