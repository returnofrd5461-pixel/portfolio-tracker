import os
import sys
import hmac
import hashlib
import base64
import datetime
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

API_KEY = os.getenv("OKX_API_KEY")
SECRET_KEY = os.getenv("OKX_SECRET_KEY")
PASSPHRASE = os.getenv("OKX_PASSPHRASE")
BASE_URL = "https://www.okx.com"


def _sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    mac = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _headers(method: str, path: str, body: str = "") -> dict:
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": _sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }


def get_balance() -> list[dict]:
    path = "/api/v5/account/balance"
    resp = requests.get(BASE_URL + path, headers=_headers("GET", path))
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API 오류: {data.get('msg')}")
    details = data["data"][0]["details"]
    return [
        {
            "currency": d["ccy"],
            "quantity": float(d["eq"]),
            "usd_value": float(d.get("eqUsd") or d.get("usdEq") or 0),
        }
        for d in details
        if float(d["eq"]) > 0
    ]


def print_balances() -> None:
    holdings = get_balance()
    print(f"\n{'코인':<10} {'보유수량':>20} {'USD 평가':>16}")
    print("─" * 50)
    total_usd = 0.0
    for h in sorted(holdings, key=lambda x: x["usd_value"], reverse=True):
        print(f"{h['currency']:<10}{h['quantity']:>20.8f}{h['usd_value']:>16,.2f}")
        total_usd += h["usd_value"]
    print("─" * 50)
    print(f"{'총 평가':>30}{total_usd:>16,.2f} USD\n")


if __name__ == "__main__":
    print_balances()
