"""
Phase 2-A 마이그레이션:
NOTION_SNAPSHOT_DB에 계좌별 KRW Number 컬럼 8개 추가.
멱등 — 이미 있으면 노션이 무시. 한 번만 실행하면 됨.

실행 후:
  - notion_sync.add_snapshot()이 신규 컬럼에 값 저장 시작
  - notion_sync.get_yesterday_snapshot()이 yesterday_accounts 반환
"""
import os
import sys
from notion_client import Client
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

notion = Client(auth=os.getenv("NOTION_TOKEN"))
SNAPSHOT_DB = os.getenv("NOTION_SNAPSHOT_DB")

NEW_COLUMNS = {
    "KIS_종합_KRW":       {"number": {"format": "number"}},
    "KIS_ISA_KRW":        {"number": {"format": "number"}},
    "KIS_연저_KRW":       {"number": {"format": "number"}},
    "OKX_KRW":            {"number": {"format": "number"}},
    "Upbit_KRW":          {"number": {"format": "number"}},
    "Toss_KRW":           {"number": {"format": "number"}},
    "Bank_비상금_KRW":    {"number": {"format": "number"}},
    "Bank_사업자금_KRW":  {"number": {"format": "number"}},
}


def main() -> None:
    if not SNAPSHOT_DB:
        print("[오류] NOTION_SNAPSHOT_DB 환경변수 없음.")
        sys.exit(1)
    print(f"DB: {SNAPSHOT_DB}")
    print(f"추가 시도: {list(NEW_COLUMNS.keys())}")
    notion.databases.update(database_id=SNAPSHOT_DB, properties=NEW_COLUMNS)

    db = notion.databases.retrieve(database_id=SNAPSHOT_DB)
    have = set(db["properties"].keys())
    missing = [k for k in NEW_COLUMNS if k not in have]
    if missing:
        print(f"[경고] 미반영: {missing}")
        sys.exit(2)
    print(f"검증 OK — 신규 컬럼 {len(NEW_COLUMNS)}개 모두 존재 (총 {len(have)}개)")


if __name__ == "__main__":
    main()
