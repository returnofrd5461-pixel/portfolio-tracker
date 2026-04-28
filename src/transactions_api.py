"""거래내역 fetch — 업비트/OKX/한투 KIS 통합.

각 거래소의 체결 내역을 통일된 형태로 정규화해 반환:
  {
    "uid":      "upbit:<order_uuid>",      # 노션 dedup 키
    "date":     "2026-04-29",
    "datetime": "2026-04-29T15:30:00+09:00",
    "exchange": "upbit"|"okx"|"kis_jonghap"|"kis_isa"|"kis_yeon",
    "account":  "UPBIT" 등 사람이 읽는 라벨,
    "ticker":   "BTC",
    "name":     "BTC",
    "side":     "buy"|"sell",
    "qty":      float,
    "price":    float,                      # 체결 단가 (현지 통화)
    "amount":   float,                      # 체결 금액 (현지 통화)
    "fee":      float,                      # 수수료 (현지 통화)
    "currency": "KRW"|"USD",
  }
"""
import os
import sys
import uuid as _uuid
import hashlib
import datetime
import jwt
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

# 환율: KRW 정규화는 메인 파이프라인에서 별도 변환

# ── 업비트 ────────────────────────────────────────────────────────────────────
UPBIT_BASE = "https://api.upbit.com"
UPBIT_AK = os.getenv("UPBIT_ACCESS_KEY")
UPBIT_SK = os.getenv("UPBIT_SECRET_KEY")


def _upbit_auth(query_string: str | None = None) -> dict:
    payload = {"access_key": UPBIT_AK, "nonce": str(_uuid.uuid4())}
    if query_string:
        payload["query_hash"] = hashlib.sha512(query_string.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(payload, UPBIT_SK, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def fetch_upbit_orders(days: int = 30) -> list[dict]:
    """업비트 체결 완료 주문 — /v1/orders/closed."""
    if not UPBIT_AK or not UPBIT_SK:
        return []
    end = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    start = end - datetime.timedelta(days=days)

    params = {
        "states[]": "done",
        "start_time": start.isoformat(timespec="seconds"),
        "end_time":   end.isoformat(timespec="seconds"),
        "limit": "1000",
        "order_by": "desc",
    }
    qs = urlencode(params, doseq=True)
    try:
        resp = requests.get(
            f"{UPBIT_BASE}/v1/orders/closed",
            headers=_upbit_auth(qs),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json() or []
    except Exception as e:
        print(f"  [업비트 주문조회 실패] {e}")
        return []

    out: list[dict] = []
    for r in rows:
        market = r.get("market", "")  # "KRW-BTC"
        ticker = market.split("-", 1)[1] if "-" in market else market
        side = "buy" if r.get("side") == "bid" else "sell"
        executed_qty = float(r.get("executed_volume") or 0)
        if executed_qty <= 0:
            continue
        # 평균 체결가
        funds = float(r.get("executed_funds") or r.get("funds_value") or 0)
        # executed_funds가 없는 응답도 있어 fallback
        if funds <= 0:
            price_each = float(r.get("price") or 0)
            funds = price_each * executed_qty
        price_avg = funds / executed_qty if executed_qty else 0
        fee = float(r.get("paid_fee") or 0)
        created = r.get("created_at") or r.get("trades_at") or ""
        date_str = created[:10] if created else ""
        out.append({
            "uid":      f"upbit:{r.get('uuid')}",
            "date":     date_str,
            "datetime": created,
            "exchange": "upbit",
            "account":  "업비트",
            "ticker":   ticker,
            "name":     ticker,
            "side":     side,
            "qty":      executed_qty,
            "price":    price_avg,
            "amount":   funds,
            "fee":      fee,
            "currency": "KRW",
        })
    return out


# ── OKX ───────────────────────────────────────────────────────────────────────
import hmac
import base64

OKX_BASE = "https://www.okx.com"
OKX_KEY = os.getenv("OKX_API_KEY")
OKX_SK = os.getenv("OKX_SECRET_KEY")
OKX_PASS = os.getenv("OKX_PASSPHRASE")


def _okx_sign(ts: str, method: str, path: str, body: str = "") -> str:
    msg = ts + method.upper() + path + body
    mac = hmac.new(OKX_SK.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _okx_headers(method: str, path: str, body: str = "") -> dict:
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "OK-ACCESS-KEY": OKX_KEY,
        "OK-ACCESS-SIGN": _okx_sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": OKX_PASS,
        "Content-Type": "application/json",
    }


def fetch_okx_orders(days: int = 7) -> list[dict]:
    """OKX 체결내역 — /api/v5/trade/orders-history (최근 7일).

    7일 초과는 orders-history-archive로 따로 호출 가능.
    """
    if not OKX_KEY or not OKX_SK or not OKX_PASS:
        return []
    path = "/api/v5/trade/orders-history?instType=SPOT&state=filled&limit=100"
    try:
        resp = requests.get(OKX_BASE + path, headers=_okx_headers("GET", path), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [OKX 주문조회 실패] {e}")
        return []
    if data.get("code") != "0":
        print(f"  [OKX] {data.get('msg')}")
        return []

    out: list[dict] = []
    for r in data.get("data", []):
        inst = r.get("instId", "")  # "BTC-USDT"
        ticker = inst.split("-", 1)[0] if "-" in inst else inst
        side = "buy" if r.get("side") == "buy" else "sell"
        qty = float(r.get("accFillSz") or r.get("fillSz") or 0)
        if qty <= 0:
            continue
        price = float(r.get("avgPx") or r.get("px") or 0)
        amount = qty * price
        fee = abs(float(r.get("fee") or 0))
        ts_ms = int(r.get("uTime") or r.get("cTime") or 0)
        if ts_ms:
            dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc)
            iso = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
            date_str = iso[:10]
        else:
            iso = ""
            date_str = ""
        out.append({
            "uid":      f"okx:{r.get('ordId')}",
            "date":     date_str,
            "datetime": iso,
            "exchange": "okx",
            "account":  "OKX",
            "ticker":   ticker,
            "name":     ticker,
            "side":     side,
            "qty":      qty,
            "price":    price,
            "amount":   amount,
            "fee":      fee,
            "currency": "USD",
        })
    return out


# ── 한투 KIS ───────────────────────────────────────────────────────────────────
KIS_BASE = "https://openapi.koreainvestment.com:9443"
KIS_ACCOUNTS = {
    "JONGHAP": {
        "app_key": os.getenv("KIS_APP_KEY_JONGHAP"),
        "app_secret": os.getenv("KIS_APP_SECRET_JONGHAP"),
        "account": os.getenv("KIS_ACCOUNT_JONGHAP"),
        "type": "overseas",
        "label": "한투 종합",
        "exchange_id": "kis_jonghap",
    },
    "ISA": {
        "app_key": os.getenv("KIS_APP_KEY_ISA"),
        "app_secret": os.getenv("KIS_APP_SECRET_ISA"),
        "account": os.getenv("KIS_ACCOUNT_ISA"),
        "type": "domestic",
        "label": "한투 ISA",
        "exchange_id": "kis_isa",
    },
    "YEON": {
        "app_key": os.getenv("KIS_APP_KEY_YEON"),
        "app_secret": os.getenv("KIS_APP_SECRET_YEON"),
        "account": os.getenv("KIS_ACCOUNT_YEON"),
        "type": "domestic",
        "label": "한투 연저",
        "exchange_id": "kis_yeon",
    },
}
_kis_token_cache: dict[tuple, str] = {}


def _kis_token(name: str) -> str:
    acc = KIS_ACCOUNTS[name]
    cache_key = (acc["app_key"], acc["app_secret"])
    if cache_key in _kis_token_cache:
        return _kis_token_cache[cache_key]
    resp = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": acc["app_key"], "appsecret": acc["app_secret"]},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _kis_token_cache[cache_key] = token
    return token


def _kis_parse_account(s: str) -> tuple[str, str]:
    parts = s.split("-")
    return parts[0], parts[1]


def _kis_headers(name: str, tr_id: str) -> dict:
    acc = KIS_ACCOUNTS[name]
    return {
        "authorization": f"Bearer {_kis_token(name)}",
        "appkey": acc["app_key"],
        "appsecret": acc["app_secret"],
        "tr_id": tr_id,
        "Content-Type": "application/json; charset=utf-8",
    }


def _kis_overseas_period_trans(name: str, start: str, end: str) -> list[dict]:
    """KIS 해외주식 기간별 거래내역 (TTTS3035R).

    start/end 형식: 'YYYYMMDD'
    """
    acc = KIS_ACCOUNTS[name]
    if not acc["app_key"]:
        return []
    cano, prdt = _kis_parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "ERLM_STRT_DT": start,
        "ERLM_END_DT":  end,
        "OVRS_EXCG_CD": "NASD",
        "PDNO": "%",
        "SLL_BUY_DVSN_CD": "00",  # 전체
        "LOAN_DVSN_CD": "",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    try:
        resp = requests.get(
            f"{KIS_BASE}/uapi/overseas-stock/v1/trading/inquire-period-trans",
            headers=_kis_headers(name, "TTTS3035R"),
            params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [KIS {name} 해외 거래내역 실패] {e}")
        return []
    if data.get("rt_cd") != "0":
        print(f"  [KIS {name}] {data.get('msg1')}")
        return []
    return data.get("output1", []) or []


def _kis_domestic_daily_ccld(name: str, start: str, end: str) -> list[dict]:
    """KIS 국내 일별 주문체결조회 (TTTC8001R).

    start/end: 'YYYYMMDD'. 응답이 90일 제한이라 30일 단위 권장.
    """
    acc = KIS_ACCOUNTS[name]
    if not acc["app_key"]:
        return []
    cano, prdt = _kis_parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "INQR_STRT_DT": start,
        "INQR_END_DT":  end,
        "SLL_BUY_DVSN_CD": "00",  # 전체
        "INQR_DVSN":     "00",     # 역순
        "PDNO":          "",
        "CCLD_DVSN":     "01",     # 체결만
        "ORD_GNO_BRNO":  "",
        "ODNO":          "",
        "INQR_DVSN_3":   "00",
        "INQR_DVSN_1":   "",
        "CTX_AREA_FK100":"",
        "CTX_AREA_NK100":"",
    }
    try:
        resp = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=_kis_headers(name, "TTTC8001R"),
            params=params, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [KIS {name} 국내 거래내역 실패] {e}")
        return []
    if data.get("rt_cd") != "0":
        print(f"  [KIS {name}] {data.get('msg1')}")
        return []
    return data.get("output1", []) or []


def _to_f(v) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def fetch_kis_orders(days: int = 30) -> list[dict]:
    """모든 한투 계좌(종합/ISA/연저)의 거래내역 통합 반환."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    out: list[dict] = []

    for name, acc in KIS_ACCOUNTS.items():
        if not acc["app_key"]:
            continue
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

        if acc["type"] == "overseas":
            rows = _kis_overseas_period_trans(name, s, e)
            for r in rows:
                qty = _to_f(r.get("ccld_qty") or r.get("ord_qty"))
                if qty <= 0:
                    continue
                price = _to_f(r.get("ovrs_excg_unpr") or r.get("ord_unpr") or r.get("ccld_unpr"))
                amount = _to_f(r.get("frcr_excg_amt") or r.get("frcr_ord_amt"))
                if amount <= 0:
                    amount = qty * price
                fee = _to_f(r.get("frcr_fee") or r.get("ovrs_excg_unpr_fee"))
                # 매매구분: 01=매수, 02=매도 (TTTS3035R 표준)
                side_code = str(r.get("sll_buy_dvsn_cd") or "")
                if side_code == "02":
                    side = "sell"
                elif side_code == "01":
                    side = "buy"
                else:
                    name_str = str(r.get("sll_buy_dvsn_cd_name") or "")
                    side = "sell" if "매도" in name_str else "buy"
                ord_dt = (r.get("erlm_dt") or r.get("ord_dt") or "").replace("-", "")
                date_str = f"{ord_dt[:4]}-{ord_dt[4:6]}-{ord_dt[6:8]}" if len(ord_dt) >= 8 else ""
                ord_no = r.get("odno") or r.get("ovrs_odno") or ""
                ticker = r.get("pdno") or r.get("ovrs_pdno") or ""
                pdt_name = r.get("prdt_name") or r.get("ovrs_item_name") or ticker
                out.append({
                    "uid":      f"kis_{name.lower()}:{ord_dt}:{ord_no}",
                    "date":     date_str,
                    "datetime": date_str,
                    "exchange": acc["exchange_id"],
                    "account":  acc["label"],
                    "ticker":   ticker,
                    "name":     pdt_name,
                    "side":     side,
                    "qty":      qty,
                    "price":    price,
                    "amount":   amount,
                    "fee":      fee,
                    "currency": "USD",
                })
        else:  # domestic (ISA/YEON)
            rows = _kis_domestic_daily_ccld(name, s, e)
            for r in rows:
                qty = _to_f(r.get("tot_ccld_qty") or r.get("ccld_qty") or r.get("ord_qty"))
                if qty <= 0:
                    continue
                price = _to_f(r.get("avg_prvs") or r.get("ccld_unpr") or r.get("ord_unpr"))
                amount = _to_f(r.get("tot_ccld_amt") or r.get("ord_amt"))
                if amount <= 0:
                    amount = qty * price
                fee = _to_f(r.get("tot_fee") or r.get("ccld_fee") or 0) + _to_f(r.get("tot_tax") or 0)
                side_code = str(r.get("sll_buy_dvsn_cd") or "")
                if side_code == "02":
                    side = "sell"
                elif side_code == "01":
                    side = "buy"
                else:
                    name_str = str(r.get("sll_buy_dvsn_cd_name") or "")
                    side = "sell" if "매도" in name_str else "buy"
                ord_dt = (r.get("ord_dt") or r.get("ord_gno_brno_dt") or "").replace("-", "")
                date_str = f"{ord_dt[:4]}-{ord_dt[4:6]}-{ord_dt[6:8]}" if len(ord_dt) >= 8 else ""
                ord_no = r.get("odno") or r.get("ord_gno_brno") or ""
                ticker = r.get("pdno") or ""
                pdt_name = r.get("prdt_name") or ticker
                out.append({
                    "uid":      f"kis_{name.lower()}:{ord_dt}:{ord_no}",
                    "date":     date_str,
                    "datetime": date_str,
                    "exchange": acc["exchange_id"],
                    "account":  acc["label"],
                    "ticker":   ticker,
                    "name":     pdt_name,
                    "side":     side,
                    "qty":      qty,
                    "price":    price,
                    "amount":   amount,
                    "fee":      fee,
                    "currency": "KRW",
                })
    return out


# ── 통합 fetch ────────────────────────────────────────────────────────────────
def fetch_all_transactions(usdkrw: float = 1380.0, days: int = 30) -> list[dict]:
    """모든 거래소의 거래내역을 통합 fetch — KRW 환산값을 amount_krw로 부착."""
    txns: list[dict] = []
    print(f"  [업비트] 최근 {days}일 거래내역 fetch...")
    upbit = fetch_upbit_orders(days=days)
    print(f"    {len(upbit)}건")
    txns.extend(upbit)

    print(f"  [OKX] 최근 7일 거래내역 fetch...")
    okx = fetch_okx_orders(days=min(days, 7))
    print(f"    {len(okx)}건")
    txns.extend(okx)

    print(f"  [KIS] 최근 {days}일 거래내역 fetch...")
    kis = fetch_kis_orders(days=days)
    print(f"    {len(kis)}건")
    txns.extend(kis)

    # KRW 환산
    for t in txns:
        if t["currency"] == "USD":
            t["amount_krw"] = round(t["amount"] * usdkrw)
            t["fee_krw"]    = round(t["fee"] * usdkrw)
        else:
            t["amount_krw"] = round(t["amount"])
            t["fee_krw"]    = round(t["fee"])

    # 최신순 정렬
    txns.sort(key=lambda x: x.get("date", ""), reverse=True)
    return txns


if __name__ == "__main__":
    txns = fetch_all_transactions(usdkrw=1473.0, days=30)
    print(f"\n총 {len(txns)}건 거래내역")
    for t in txns[:10]:
        print(f"  {t['date']} {t['exchange']:<12} {t['ticker']:<6} "
              f"{t['side']:<4} {t['qty']:>10.4f} @ {t['price']:>10,.2f} "
              f"= {t['amount_krw']:>12,} KRW")
