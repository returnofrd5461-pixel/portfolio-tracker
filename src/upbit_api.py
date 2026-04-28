import os
import sys
import uuid
import hashlib
import jwt
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
BASE_URL = "https://api.upbit.com"


def _auth_header(query_string: str | None = None) -> dict:
    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
    }
    if query_string:
        query_hash = hashlib.sha512(query_string.encode()).hexdigest()
        payload["query_hash"] = query_hash
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def get_accounts() -> list[dict]:
    resp = requests.get(f"{BASE_URL}/v1/accounts", headers=_auth_header())
    resp.raise_for_status()
    return resp.json()


def get_current_prices(markets: list[str]) -> dict[str, float]:
    """공개 API — 인증 불필요. markets: ["KRW-BTC", "KRW-ETH", ...]"""
    if not markets:
        return {}
    resp = requests.get(
        f"{BASE_URL}/v1/ticker",
        params={"markets": ",".join(markets)},
    )
    resp.raise_for_status()
    return {item["market"]: item["trade_price"] for item in resp.json()}


def fetch_balances() -> list[dict]:
    """계좌 잔고를 조회해 KRW 평가금액을 포함한 리스트로 반환."""
    accounts = get_accounts()

    krw_balance = 0.0
    holdings = []

    for acc in accounts:
        currency = acc["currency"]
        total_qty = float(acc["balance"]) + float(acc["locked"])
        avg_price = float(acc["avg_buy_price"])

        if currency == "KRW":
            krw_balance = total_qty
        elif total_qty > 0:
            holdings.append({
                "currency": currency,
                "market": f"KRW-{currency}",
                "quantity": total_qty,
                "avg_buy_price": avg_price,
            })

    # 현재가 일괄 조회
    markets = [h["market"] for h in holdings]
    prices = get_current_prices(markets)

    for h in holdings:
        current_price = prices.get(h["market"], 0.0)
        h["current_price"] = current_price
        h["eval_krw"] = h["quantity"] * current_price
        h["profit_rate"] = (
            (current_price / h["avg_buy_price"] - 1) * 100
            if h["avg_buy_price"] > 0
            else 0.0
        )

    holdings.sort(key=lambda x: x["eval_krw"], reverse=True)
    return krw_balance, holdings


def print_balances() -> None:
    krw_balance, holdings = fetch_balances()

    total_eval = krw_balance + sum(h["eval_krw"] for h in holdings)

    print(f"\n{'코인':<8} {'보유수량':>18} {'평균매수가':>14} {'현재가':>14} {'평가금액(KRW)':>18} {'수익률':>8}")
    print("─" * 86)

    for h in holdings:
        print(
            f"{h['currency']:<8}"
            f"{h['quantity']:>18.8f}"
            f"{h['avg_buy_price']:>14,.0f}"
            f"{h['current_price']:>14,.0f}"
            f"{h['eval_krw']:>18,.0f}"
            f"{h['profit_rate']:>+7.2f}%"
        )

    print("─" * 86)
    print(f"{'KRW 잔고':<8}{'':>18}{'':>14}{'':>14}{krw_balance:>18,.0f}")
    print(f"{'총 평가':>64}{total_eval:>18,.0f} KRW\n")


if __name__ == "__main__":
    print_balances()
