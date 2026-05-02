import os
import sys
import requests
from dotenv import load_dotenv

from schedule_monitor import get_schedule_summary, format_summary_line

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")

# 목표 비중 (%)
TARGET_RISKY = 70.0
TARGET_SAFE = 30.0
RISKY_THRESHOLD = 3.0   # ±3pp
CLASS_THRESHOLD = 5.0   # ±5pp
MDD_THRESHOLD = -10.0   # -10%


def send_slack(message: str) -> bool:
    if not SLACK_WEBHOOK:
        print("  [경고] SLACK_WEBHOOK_URL 미설정")
        return False
    resp = requests.post(SLACK_WEBHOOK, json={"text": message})
    return resp.status_code == 200


def send_slack_blocks(payload: dict) -> bool:
    """Block Kit payload 발송. payload = {"blocks": [...], "text": "fallback"}."""
    if not SLACK_WEBHOOK:
        print("  [경고] SLACK_WEBHOOK_URL 미설정")
        return False
    resp = requests.post(SLACK_WEBHOOK, json=payload)
    return resp.status_code == 200


def check_alerts(portfolio: dict) -> list[str]:
    """
    portfolio 예시:
    {
      "total_krw": 50_000_000,
      "risky_pct": 75.0,
      "safe_pct": 25.0,
      "asset_classes": {"crypto": 40.0, "us_stock": 30.0, "etf": 5.0},
      "mdd": -5.0,
      "prev_total_krw": 55_000_000,  # 고점 대비 MDD 계산용
    }
    """
    alerts = []

    risky = portfolio.get("risky_pct", TARGET_RISKY)
    safe = portfolio.get("safe_pct", TARGET_SAFE)
    mdd = portfolio.get("mdd", 0.0)

    if abs(risky - TARGET_RISKY) > RISKY_THRESHOLD:
        direction = "초과" if risky > TARGET_RISKY else "부족"
        alerts.append(
            f":warning: *위험자산 비중 이탈*\n"
            f"현재: {risky:.1f}% | 목표: {TARGET_RISKY}% | 편차: {risky - TARGET_RISKY:+.1f}pp ({direction})"
        )

    if abs(safe - TARGET_SAFE) > RISKY_THRESHOLD:
        direction = "초과" if safe > TARGET_SAFE else "부족"
        alerts.append(
            f":warning: *안전자산 비중 이탈*\n"
            f"현재: {safe:.1f}% | 목표: {TARGET_SAFE}% | 편차: {safe - TARGET_SAFE:+.1f}pp ({direction})"
        )

    for cls, pct in portfolio.get("asset_classes", {}).items():
        target = portfolio.get("asset_class_targets", {}).get(cls)
        if target is not None and abs(pct - target) > CLASS_THRESHOLD:
            alerts.append(
                f":bar_chart: *{cls} 비중 이탈*\n"
                f"현재: {pct:.1f}% | 목표: {target}% | 편차: {pct - target:+.1f}pp"
            )

    if mdd <= MDD_THRESHOLD:
        alerts.append(
            f":rotating_light: *MDD 경보*\n"
            f"현재 낙폭: {mdd:.1f}% (임계치: {MDD_THRESHOLD}%)"
        )

    return alerts


def _arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "·"


def send_daily_report(portfolio: dict) -> None:
    total = portfolio.get("total_krw", 0)
    total_usd = portfolio.get("total_usd", 0)
    risky = portfolio.get("risky_pct", 0)
    safe = portfolio.get("safe_pct", 0)
    usdkrw = portfolio.get("usdkrw", 0)
    kis_jonghap_usd = portfolio.get("kis_jonghap_usd", 0)
    okx_usd = portfolio.get("okx_usd", 0)

    total_usd_str    = f" (≈ ${total_usd:,.0f})" if total_usd else ""
    kis_jonghap_str  = f" (해외 ≈ ${kis_jonghap_usd:,.0f})" if kis_jonghap_usd else ""
    okx_usd_str      = f" (≈ ${okx_usd:,.0f})" if okx_usd else ""

    message = (
        f":briefcase: *포트폴리오 일일 리포트*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"총 자산: *{total:,.0f} KRW*{total_usd_str}\n"
        f"위험자산: {risky:.1f}% | 안전자산: {safe:.1f}%\n"
        f"USD/KRW: {usdkrw:,.0f}\n"
        f"업비트: {portfolio.get('upbit_krw', 0):,.0f} KRW\n"
        f"OKX: {portfolio.get('okx_krw', 0):,.0f} KRW{okx_usd_str}\n"
        f"한투: {portfolio.get('kis_krw', 0):,.0f} KRW{kis_jonghap_str}"
    )

    dc = portfolio.get("daily_change")
    if dc:
        td = dc.get("total_diff", 0)
        tp = dc.get("total_pct", 0)
        rd = dc.get("risky_diff", 0)
        sd = dc.get("safe_diff", 0)
        message += (
            f"\n어제比: 총자산 {_arrow(td)} {td:+,.0f} KRW ({tp:+.2f}%) | "
            f"위험 {rd:+.1f}%p | 안전 {sd:+.1f}%p"
        )

    summary_line = format_summary_line(get_schedule_summary())
    if summary_line:
        message += f"\n{summary_line}"

    ok = send_slack(message)
    print(f"  일일 리포트 발송: {'성공' if ok else '실패'}")

    alerts = check_alerts(portfolio)
    for alert in alerts:
        ok = send_slack(alert)
        print(f"  알림 발송: {'성공' if ok else '실패'} — {alert[:40]}...")


def send_manual_input_reminder() -> bool:
    """매주 월요일 — 수동 입력 필요 항목 Slack 리마인더."""
    message = (
        "📝 *수동 입력 갱신 필요*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "아래 3개 항목을 노션에 직접 업데이트해 주세요.\n\n"
        "• *토스 NVDA 평가금액* — 토스증권 앱 확인 후 KRW 입력\n"
        "• *비상금* — 파킹통장 잔액 확인 후 KRW 입력\n"
        "• *사업운영자금* — 사업용 계좌 잔액 확인 후 KRW 입력"
    )
    ok = send_slack(message)
    print(f"  주간 수동입력 리마인더 발송: {'성공' if ok else '실패'}")
    return ok


if __name__ == "__main__":
    test_portfolio = {
        "total_krw": 51_000_000,
        "upbit_krw": 33_000_000,
        "okx_krw": 8_000_000,
        "kis_krw": 10_000_000,
        "risky_pct": 74.5,
        "safe_pct": 25.5,
        "usdkrw": 1380.0,
        "mdd": -2.0,
        "asset_classes": {"crypto": 40.0, "us_stock": 20.0, "etf": 15.0},
        "asset_class_targets": {"crypto": 35.0, "us_stock": 25.0, "etf": 10.0},
    }
    send_daily_report(test_portfolio)
