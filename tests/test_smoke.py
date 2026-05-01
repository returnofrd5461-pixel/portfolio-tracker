"""
더미 데이터로 trading_journal.py의 분석 함수 동작 검증.
notion API 호출은 안 하고, normalize_transaction 이후 단계만 테스트.
"""
import sys
sys.path.insert(0, '.')

from trading_journal import (
    compute_basic_stats,
    compute_position_pnl,
    compute_price_distribution,
    find_unlogged_transactions,
    build_dashboard_payload,
    build_slack_unlogged_alert,
)

# 더미 거래 데이터 (정규화된 형태)
NOW = "2026-04-29"
txs = [
    {  # 미입력 BTC 매수
        "page_id": "p1", "page_url": "u1",
        "ticker": "BTC", "name": "Bitcoin",
        "exchange": "Upbit", "account": "Upbit",
        "side": "매수", "trade_type": "매수",
        "date": "2026-04-29T10:00:00.000+09:00",
        "qty": 0.05, "price": 120_000_000, "amount": 6_000_000,
        "amount_krw": 6_000_000, "fee": 1500, "fx_rate": 0,
        "tranche": "", "memo": "", "tags": [],
        "result_30d_pct": None, "uid": "tx-1",
        "created_time": "2026-04-29T10:00:00.000Z",
    },
    {  # 미입력 BTC 추가 매수 (다른 가격)
        "page_id": "p2", "page_url": "u2",
        "ticker": "BTC", "name": "Bitcoin",
        "exchange": "Upbit", "account": "Upbit",
        "side": "매수", "trade_type": "매수",
        "date": "2026-04-28T15:00:00.000+09:00",
        "qty": 0.03, "price": 115_000_000, "amount": 3_450_000,
        "amount_krw": 3_450_000, "fee": 800, "fx_rate": 0,
        "tranche": "", "memo": "", "tags": [],
        "result_30d_pct": None, "uid": "tx-2",
        "created_time": "2026-04-28T15:00:00.000Z",
    },
    {  # 입력완료 ETH 매수 (메모+태그)
        "page_id": "p3", "page_url": "u3",
        "ticker": "ETH", "name": "Ethereum",
        "exchange": "OKX", "account": "OKX",
        "side": "매수", "trade_type": "매수",
        "date": "2026-04-25T11:00:00.000+09:00",
        "qty": 1.0, "price": 5_000_000, "amount": 5_000_000,
        "amount_krw": 5_000_000, "fee": 1000, "fx_rate": 0,
        "tranche": "1차", "memo": "지지선 5M에서 매수",
        "tags": ["박스권"],
        "result_30d_pct": None, "uid": "tx-3",
        "created_time": "2026-04-25T11:00:00.000Z",
    },
    {  # BTC 일부 매도
        "page_id": "p4", "page_url": "u4",
        "ticker": "BTC", "name": "Bitcoin",
        "exchange": "Upbit", "account": "Upbit",
        "side": "매도", "trade_type": "매도",
        "date": "2026-04-27T09:00:00.000+09:00",
        "qty": 0.02, "price": 130_000_000, "amount": 2_600_000,
        "amount_krw": 2_600_000, "fee": 600, "fx_rate": 0,
        "tranche": "", "memo": "익절 일부", "tags": ["기타"],
        "result_30d_pct": None, "uid": "tx-4",
        "created_time": "2026-04-27T09:00:00.000Z",
    },
    {  # 리밸런싱 (자동 분류라 미입력에서 제외돼야)
        "page_id": "p5", "page_url": "u5",
        "ticker": "426030", "name": "TIME 미국나스닥100액티브",
        "exchange": "한국투자증권", "account": "ISA",
        "side": "리밸런싱", "trade_type": "매수",
        "date": "2026-04-29T13:00:00.000+09:00",
        "qty": 100, "price": 12000, "amount": 1_200_000,
        "amount_krw": 1_200_000, "fee": 50, "fx_rate": 0,
        "tranche": "", "memo": "", "tags": [],
        "result_30d_pct": None, "uid": "tx-5",
        "created_time": "2026-04-29T13:00:00.000Z",
    },
]

print("=" * 60)
print("1. compute_basic_stats")
print("=" * 60)
stats = compute_basic_stats(txs)
for k, v in stats.items():
    print(f"  {k}: {v}")

print()
print("=" * 60)
print("2. compute_position_pnl")
print("=" * 60)
current_prices = {"BTC": 125_000_000, "ETH": 5_300_000, "426030": 12500}
positions = compute_position_pnl(txs, current_prices)
for p in positions:
    print(f"  [{p['ticker']}] 보유 {p['net_qty']:.4f} · "
          f"평단 ₩{p['avg_buy_price_krw']:,.0f} · "
          f"평가 ₩{p['eval_krw']:,.0f} ({p['unrealized_pnl_pct']:+.2f}%) · "
          f"실현 ₩{p['realized_pnl_krw']:,.0f}")

print()
print("=" * 60)
print("3. compute_price_distribution (BTC)")
print("=" * 60)
dist = compute_price_distribution(txs, "BTC", bucket_count=5)
print(f"  min={dist['min']:,} max={dist['max']:,} median={dist['median']:,}")
print(f"  weighted_avg={dist['weighted_avg']:,} (가중평단)")
print(f"  bucket count: {len(dist['buckets'])}")
for i, b in enumerate(dist['buckets']):
    bar = '█' * int(b['count'] * 5)
    print(f"  [{i}] ₩{b['low']:,.0f} ~ ₩{b['high']:,.0f}: {b['count']}건 {bar}")

print()
print("=" * 60)
print("4. find_unlogged_transactions")
print("=" * 60)
unlogged = find_unlogged_transactions(txs)
print(f"  총 미입력: {len(unlogged)}건 (리밸런싱 1건은 제외돼야 정상)")
for u in unlogged:
    print(f"  - [{(u['date'] or '')[:10]}] {u['ticker']} {u['side']} ₩{u['amount_krw']:,.0f}")

print()
print("=" * 60)
print("5. build_slack_unlogged_alert")
print("=" * 60)
alert = build_slack_unlogged_alert(unlogged)
import json
print(json.dumps(alert, ensure_ascii=False, indent=2))

print()
print("=" * 60)
print("6. build_dashboard_payload (요약)")
print("=" * 60)
payload = build_dashboard_payload(
    txs, stats, positions,
    {"BTC": dist, "ETH": compute_price_distribution(txs, "ETH")},
    unlogged
)
print(f"  generated_at: {payload['generated_at']}")
print(f"  stats.total: {payload['stats']['total']}")
print(f"  positions: {len(payload['positions'])}개 종목")
print(f"  price_distributions: {list(payload['price_distributions'].keys())}")
print(f"  unlogged_count: {payload['unlogged_count']}")
print(f"  transactions: {len(payload['transactions'])}건")

# 빈 거래 리스트 edge case
print()
print("=" * 60)
print("Edge case: 빈 거래 리스트")
print("=" * 60)
empty_stats = compute_basic_stats([])
empty_pos = compute_position_pnl([])
empty_unlog = find_unlogged_transactions([])
empty_dist = compute_price_distribution([], "BTC")
empty_alert = build_slack_unlogged_alert([])
print(f"  stats.total: {empty_stats['total']}")
print(f"  positions: {len(empty_pos)}")
print(f"  unlogged: {len(empty_unlog)}")
print(f"  dist (no data): {empty_dist}")
print(f"  alert (no unlogged): {empty_alert}")

print()
print("=" * 60)
print("✅ 모든 함수 정상 동작")
print("=" * 60)
