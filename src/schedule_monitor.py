"""
GitHub Actions schedule run 모니터링.
self-hosted runner에서 GITHUB_TOKEN 자동 주입 받아 동작.
로컬 수동 실행 시엔 토큰 없으므로 None 반환 (스킵).
"""
import os
from datetime import datetime, timedelta, timezone
import requests

REPO = "returnofrd5461-pixel/portfolio-tracker"
WORKFLOW_FILE = "daily_portfolio.yml"
EXPECTED_PER_DAY = 7  # cron 횟수


def get_schedule_summary(hours: int = 24):
    """최근 N시간 schedule run 통계. workflow 컨텍스트에서만 작동."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return None  # 로컬 실행 시 스킵

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"event": "schedule", "created": f">={since}", "per_page": 50}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        executed = r.json().get("total_count", 0)
    except Exception:
        return None

    expected = EXPECTED_PER_DAY
    rate = round(executed / expected * 100, 1) if expected > 0 else 0
    return {"executed": executed, "expected": expected, "rate": rate}


def format_summary_line(summary: dict) -> str:
    """슬랙 메시지에 박을 한 줄 포맷."""
    if not summary:
        return ""
    return f"📊 24h schedule: {summary['executed']}/{summary['expected']} ({summary['rate']}%)"
