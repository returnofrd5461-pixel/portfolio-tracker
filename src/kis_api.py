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


def _get_overseas_psamount(name: str) -> dict:
    """해외주식 매수가능금액조회 (TTTS3007R) — KRW + USD 예수금 반환."""
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
    except Exception:
        return {}


def get_overseas_balance(name: str, usdkrw: float = 1380.0) -> tuple[list[dict], float]:
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

    # output2에서 KRW/USD 예수금 후보 필드 탐색
    krw_cash = float(out2.get("dnca_tot_amt") or 0)
    usd_cash = 0.0
    for f in ("frcr_dncl_amt1", "frcr_dncl_amt", "frcr_buy_able_amt"):
        v = out2.get(f)
        if v:
            try:
                usd_cash = float(v)
                if usd_cash > 0:
                    break
            except (ValueError, TypeError):
                pass

    # output2에 없으면 inquire-psamount 보조 호출
    if krw_cash == 0 and usd_cash == 0:
        psa = _get_overseas_psamount(name)
        try:
            krw_cash = float(psa.get("frcr_ord_psbl_amt1") or psa.get("ord_psbl_frcr_amt") or 0)
        except (ValueError, TypeError):
            pass
        try:
            usd_cash = float(psa.get("ovrs_ord_psbl_amt") or 0)
        except (ValueError, TypeError):
            pass

    total_cash_krw = krw_cash + usd_cash * usdkrw

    # 디버그: output2 비-제로 필드 출력
    print(f"  [DEBUG {name} output2 비-제로 필드]")
    for k, v in sorted(out2.items()):
        try:
            fv = float(v or 0)
            if fv != 0:
                print(f"    {k}: {fv:,.2f}")
        except (ValueError, TypeError):
            pass
    print(f"  [{name}] KRW예수금 {krw_cash:,.0f} | USD예수금 {usd_cash:,.2f} (≈{usd_cash*usdkrw:,.0f}원) → 합산 {total_cash_krw:,.0f}원")

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
    return holdings, total_cash_krw


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
    cash_krw = float(out2.get("dnca_tot_amt") or 0)
    print(f"  [{name}] KRW예수금 {cash_krw:,.0f}원")
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
              "cash_jonghap": 0.0, "cash_isa": 0.0, "cash_yeon": 0.0}
    for name, cfg in ACCOUNTS.items():
        try:
            if cfg["type"] == "overseas":
                holdings, cash = get_overseas_balance(name, usdkrw)
                result["overseas"].extend(holdings)
                result[f"cash_{name.lower()}"] = cash
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
