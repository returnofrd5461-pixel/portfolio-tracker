import os
import sys
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

BASE_URL = "https://openapi.koreainvestment.com:9443"

ACCOUNTS = {
    "JONGHAP": {
        "app_key": os.getenv("KIS_APP_KEY_JONGHAP"),
        "app_secret": os.getenv("KIS_APP_SECRET_JONGHAP"),
        "account": os.getenv("KIS_ACCOUNT_JONGHAP"),  # 64545469-01
        "type": "overseas",
    },
    "ISA": {
        "app_key": os.getenv("KIS_APP_KEY_ISA"),
        "app_secret": os.getenv("KIS_APP_SECRET_ISA"),
        "account": os.getenv("KIS_ACCOUNT_ISA"),  # 44437343-01
        "type": "domestic",
    },
    "YEON": {
        "app_key": os.getenv("KIS_APP_KEY_YEON"),
        "app_secret": os.getenv("KIS_APP_SECRET_YEON"),
        "account": os.getenv("KIS_ACCOUNT_YEON"),  # 44427416-22
        "type": "domestic",
    },
}

_token_cache: dict[tuple, str] = {}


def _get_token(name: str) -> str:
    acc = ACCOUNTS[name]
    # 동일 key+secret 조합이면 토큰 공유, 다르면 별도 발급
    cache_key = (acc["app_key"], acc["app_secret"])
    if cache_key in _token_cache:
        return _token_cache[cache_key]
    resp = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": acc["app_key"],
            "appsecret": acc["app_secret"],
        },
    )
    if resp.status_code == 403:
        body = resp.json() if resp.content else {}
        raise RuntimeError(
            f"403 Forbidden — 앱키 '{acc['app_key'][:10]}...' 인증 실패. "
            f"KIS 포털에서 해당 앱키 활성화 여부 확인 필요. 응답: {body}"
        )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _token_cache[cache_key] = token
    return token


def _parse_account(account_str: str) -> tuple[str, str]:
    """'64545469-01' → ('64545469', '01')"""
    parts = account_str.split("-")
    return parts[0], parts[1]


def _base_headers(name: str, tr_id: str) -> dict:
    acc = ACCOUNTS[name]
    return {
        "authorization": f"Bearer {_get_token(name)}",
        "appkey": acc["app_key"],
        "appsecret": acc["app_secret"],
        "tr_id": tr_id,
        "Content-Type": "application/json; charset=utf-8",
    }


def _to_float(v) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def _print_nonzero(label: str, d: dict) -> None:
    print(f"  [{label}]")
    if not d:
        print("    (응답 없음)")
        return
    for k, v in sorted(d.items()):
        f = _to_float(v)
        if f != 0:
            print(f"    {k}: {f:,.4f}")
        elif v not in (None, "", "0", "0.00", "0.0000"):
            # 문자열 또는 비정상 값은 그대로 표시
            if not isinstance(v, (int, float)) or v != 0:
                print(f"    {k}: {v}")


def _pick_max(label: str, candidates: list[tuple[str, object]]) -> tuple[float, str]:
    """후보 [(필드명, 값), ...] 중 최댓값과 그 필드명 반환."""
    best, best_field = 0.0, None
    for field, v in candidates:
        f = _to_float(v)
        if f > best:
            best, best_field = f, field
    return best, best_field


def _get_overseas_psamount(name: str) -> dict:
    """해외주식 매수가능금액조회 (TTTS3007R) — output dict 반환."""
    acc = ACCOUNTS[name]
    cano, acnt_prdt_cd = _parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD",
        "OVRS_ORD_UNPR": "1",
        "ITEM_CD": "AAPL",
    }
    try:
        resp = requests.get(
            f"{BASE_URL}/uapi/overseas-stock/v1/trading/inquire-psamount",
            headers=_base_headers(name, "TTTS3007R"),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("output", {}) or {}
    except Exception as e:
        print(f"  [psamount 호출 실패 ({name}): {e}]")
        return {}


def _get_domestic_psbl(name: str) -> dict:
    """국내주식 매수가능조회 (TTTC8908R) — output dict 반환."""
    acc = ACCOUNTS[name]
    cano, acnt_prdt_cd = _parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": "005930",  # 더미: 삼성전자
        "ORD_UNPR": "0",
        "ORD_DVSN": "01",
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }
    try:
        resp = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            headers=_base_headers(name, "TTTC8908R"),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("output", {}) or {}
    except Exception as e:
        print(f"  [psbl-order 호출 실패 ({name}): {e}]")
        return {}


def get_overseas_balance(name: str, usdkrw: float = 1380.0) -> tuple[list[dict], float, float]:
    acc = ACCOUNTS[name]
    cano, acnt_prdt_cd = _parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    resp = requests.get(
        f"{BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance",
        headers=_base_headers(name, "TTTS3012R"),
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"KIS {name} 해외잔고 오류: {data.get('msg1')}")
    out2 = data.get("output2", {})
    if isinstance(out2, list):
        out2 = out2[0] if out2 else {}

    # 항상 매수가능금액 조회 병행
    psa = _get_overseas_psamount(name)

    # 디버그: 두 응답의 비-제로 필드 모두 출력
    _print_nonzero(f"{name} inquire-balance output2", out2)
    _print_nonzero(f"{name} inquire-psamount output", psa)

    # KRW 예수금: 해외계좌는 보통 0이거나 dnca_tot_amt에만 소액 존재
    krw_cash, krw_field = _pick_max(name, [
        ("balance.dnca_tot_amt",  out2.get("dnca_tot_amt")),
        ("balance.tot_dncl_amt",  out2.get("tot_dncl_amt")),
    ])

    # USD 예수금/매수가능액 — psa.tr_crcy_cd가 USD면 frcr_ord_psbl_amt1 = USD cash
    psa_is_usd = str(psa.get("tr_crcy_cd", "")).upper() == "USD"
    usd_candidates = [
        ("balance.frcr_dncl_amt1", out2.get("frcr_dncl_amt1")),
        ("balance.frcr_dncl_amt",  out2.get("frcr_dncl_amt")),
    ]
    if psa_is_usd:
        usd_candidates.extend([
            ("psa.frcr_ord_psbl_amt1", psa.get("frcr_ord_psbl_amt1")),
            ("psa.ovrs_ord_psbl_amt",  psa.get("ovrs_ord_psbl_amt")),
            ("psa.tr_frcr_amt1",       psa.get("tr_frcr_amt1")),
        ])
    usd_cash, usd_field = _pick_max(name, usd_candidates)

    # KIS 자체 환율 (있으면 우선 사용)
    psa_exrt = _to_float(psa.get("exrt"))
    fx_used = psa_exrt if psa_exrt > 0 else usdkrw

    total_cash_krw = krw_cash + usd_cash * fx_used
    print(f"  [{name}] 한투 종합 예수금: {total_cash_krw:,.0f}원 = "
          f"KRW {krw_cash:,.0f}(필드: {krw_field}) + "
          f"USD {usd_cash:,.2f}(필드: {usd_field}) × {fx_used:.2f}")

    holdings = [
        {
            "account": name,
            "ticker": item["ovrs_pdno"],
            "name": item["ovrs_item_name"],
            "quantity": float(item["ovrs_cblc_qty"]),
            "avg_price": float(item["pchs_avg_pric"]),
            "eval_usd": float(item["ovrs_stck_evlu_amt"]),
            "profit_rate": float(item.get("evlu_pfls_rt", 0)),
        }
        for item in data.get("output1", [])
        if float(item.get("ovrs_cblc_qty", 0)) > 0
    ]
    return holdings, total_cash_krw, usd_cash


def get_domestic_balance(name: str) -> tuple[list[dict], float]:
    acc = ACCOUNTS[name]
    cano, acnt_prdt_cd = _parse_account(acc["account"])
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    resp = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=_base_headers(name, "TTTC8434R"),
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"KIS {name} 국내잔고 오류: {data.get('msg1')}")
    out2 = data.get("output2", {})
    if isinstance(out2, list):
        out2 = out2[0] if out2 else {}

    # 항상 매수가능조회 병행
    psbl = _get_domestic_psbl(name)

    # 디버그
    _print_nonzero(f"{name} inquire-balance output2", out2)
    _print_nonzero(f"{name} inquire-psbl-order output", psbl)

    # KRW 후보 — 잔고 + 매수가능 양쪽 시도
    cash_krw, cash_field = _pick_max(name, [
        ("balance.dnca_tot_amt",       out2.get("dnca_tot_amt")),
        ("balance.nxdy_excc_amt",      out2.get("nxdy_excc_amt")),
        ("balance.prvs_rcdl_excc_amt", out2.get("prvs_rcdl_excc_amt")),
        ("psbl.ord_psbl_cash",         psbl.get("ord_psbl_cash")),
        ("psbl.max_buy_amt",           psbl.get("max_buy_amt")),
        ("psbl.nrcvb_buy_amt",         psbl.get("nrcvb_buy_amt")),
        ("psbl.ruse_psbl_amt",         psbl.get("ruse_psbl_amt")),
    ])

    print(f"  [{name}] 예수금 {cash_krw:,.0f}원 (필드: {cash_field})")

    holdings = [
        {
            "account": name,
            "ticker": item["pdno"],
            "name": item["prdt_name"],
            "quantity": float(item["hldg_qty"]),
            "avg_price": float(item["pchs_avg_pric"]),
            "eval_krw": float(item["evlu_amt"]),
            "profit_rate": float(item.get("evlu_pfls_rt", 0)),
        }
        for item in data.get("output1", [])
        if float(item.get("hldg_qty", 0)) > 0
    ]
    return holdings, cash_krw


def fetch_all_balances(usdkrw: float = 1380.0) -> dict:
    result = {"overseas": [], "domestic": [],
              "cash_jonghap": 0.0, "cash_isa": 0.0, "cash_yeon": 0.0,
              "cash_jonghap_usd": 0.0}
    for name, cfg in ACCOUNTS.items():
        try:
            if cfg["type"] == "overseas":
                holdings, cash_krw, cash_usd = get_overseas_balance(name, usdkrw)
                result["overseas"].extend(holdings)
                result[f"cash_{name.lower()}"] = cash_krw
                result[f"cash_{name.lower()}_usd"] = cash_usd
            else:
                holdings, cash = get_domestic_balance(name)
                result["domestic"].extend(holdings)
                result[f"cash_{name.lower()}"] = cash
        except Exception as e:
            print(f"  [경고] {name} 잔고 조회 실패: {e}")
    return result


def print_balances() -> None:
    data = fetch_all_balances()

    if data["overseas"]:
        print(f"\n[해외주식 - JONGHAP]")
        print(f"{'티커':<10} {'종목명':<30} {'수량':>8} {'평균가(USD)':>14} {'평가(USD)':>14} {'수익률':>8}")
        print("─" * 90)
        for h in data["overseas"]:
            print(
                f"{h['ticker']:<10}{h['name']:<30}{h['quantity']:>8.4f}"
                f"{h['avg_price']:>14,.2f}{h['eval_usd']:>14,.2f}{h['profit_rate']:>+7.2f}%"
            )

    if data["domestic"]:
        print(f"\n[국내주식/ETF - ISA + YEON]")
        print(f"{'계좌':<8} {'티커':<10} {'종목명':<25} {'수량':>8} {'평균가(KRW)':>14} {'평가(KRW)':>14} {'수익률':>8}")
        print("─" * 95)
        for h in data["domestic"]:
            print(
                f"{h['account']:<8}{h['ticker']:<10}{h['name']:<25}{h['quantity']:>8.0f}"
                f"{h['avg_price']:>14,.0f}{h['eval_krw']:>14,.0f}{h['profit_rate']:>+7.2f}%"
            )
    print()


if __name__ == "__main__":
    print_balances()
