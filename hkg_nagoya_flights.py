"""
香港国際空港（HKG）カーゴフライト（出発・到着）の Nagoya (NGO) 便を抽出するスクリプト
"""

import os
import requests
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.hongkongairport.com/en/flights/departures/cargo.page",
}

BASE_URL = "https://www.hongkongairport.com/flightinfo-rest/rest/flights"

NAGOYA_KEYWORDS = ["nagoya", "ngo", "中部"]

# 今日・明日・明後日
TARGET_DATES = [
    (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
    for i in range(3)
]

DATE_LABELS = {
    TARGET_DATES[0]: "今日",
    TARGET_DATES[1]: "明日",
    TARGET_DATES[2]: "明後日",
}

# Discord Webhook URL（環境変数 DISCORD_WEBHOOK_URL から取得）
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1482606854541676664/7vIH3CSM4lHG7tuMsPvrds263kxlfo9BEdur6EtEo37lLkyqqnrZYtEUXwvn4GXokjaL"

# API パラメータ候補（上から順に試す）
PARAM_TEMPLATES = [
    "type={type}&cargo=true&lang=en&date={date}",
    "type={type}&cargo=Y&lang=en&date={date}",
]

# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------


def fetch_for_date(session: requests.Session, date: str, flight_type: str) -> list | None:
    """指定日・種別 (dep/arr) のカーゴフライト JSON を取得して返す。失敗時は None。"""
    for tmpl in PARAM_TEMPLATES:
        url = f"{BASE_URL}?{tmpl.format(type=flight_type, date=date)}"
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# フィルタリング・整形
# ---------------------------------------------------------------------------


def extract_nagoya_flights(data: list, flight_type: str) -> list[dict]:
    """JSON データから Nagoya (NGO) 発着フライトを抽出して返す。

    dep → destination フィールドで検索
    arr → origin フィールドで検索
    """
    results = []
    for date_block in data:
        date_str = date_block.get("date", "")
        for flight in date_block.get("list", []):
            targets = (
                flight.get("origin", [])
                if flight_type == "arr"
                else flight.get("destination", []) or flight.get("dest", [])
            ) or []
            if any(kw in " ".join(targets).lower() for kw in NAGOYA_KEYWORDS):
                results.append({"date": date_str, "_type": flight_type, **flight})
    return results


def format_flight(flight: dict) -> str:
    """フライト辞書を読みやすい文字列に整形して返す。"""
    flight_nos = ", ".join(f["no"] for f in flight.get("flight", []))
    airlines   = ", ".join(f.get("airline", "") for f in flight.get("flight", []))
    dest       = ", ".join(flight.get("destination", [])) or "-"
    origin     = ", ".join(flight.get("origin", [])) or "-"
    ftype      = "出発" if flight.get("_type") == "dep" else "到着"
    return "\n".join([
        f"  種別      : {ftype}",
        f"  日付      : {flight.get('date', '-')}",
        f"  時刻      : {flight.get('time', '-')}",
        f"  便名      : {flight_nos}",
        f"  航空会社  : {airlines}",
        f"  出発地    : {origin}",
        f"  目的地    : {dest}",
        f"  ステータス: {flight.get('status', '-')}",
    ])


# ---------------------------------------------------------------------------
# Discord 通知
# ---------------------------------------------------------------------------


def _flights_to_embed(flights: list[dict], ftype: str) -> dict:
    """フライト一覧を1つの Discord Embed にまとめる。"""
    color = 0x00BFFF if ftype == "dep" else 0xFF8C00
    label = "出発 (HKG→NGO)" if ftype == "dep" else "到着 (NGO→HKG)"
    if not flights:
        desc = "該当なし"
    else:
        lines = []
        for f in flights:
            nos        = " ".join(x["no"] for x in f.get("flight", []))
            date_label = DATE_LABELS.get(f.get("date", ""), "")
            date_str   = f.get("date", "-")
            status     = f.get("status") or "-"
            lines.append(f"{nos}  {date_label}({date_str}) {f.get('time', '-')}  [{status}]")
        desc = "\n".join(lines)
    return {"title": label, "color": color, "description": desc}


def notify_discord(dep_flights: list[dict], arr_flights: list[dict]) -> None:
    """出発・到着フライト情報を Discord Webhook へ送信する。"""
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] DISCORD_WEBHOOK_URL が未設定のため通知をスキップします。")
        return

    all_flights = dep_flights + arr_flights
    if not all_flights:
        payload = {"content": "🔍 HKG カーゴ | NGO 便 該当なし"}
    else:
        embeds = [
            _flights_to_embed(dep_flights, "dep"),
            _flights_to_embed(arr_flights, "arr"),
        ]
        payload = {
            "content": f"✈ **HKG カーゴ NGO 便** | 出発 {len(dep_flights)} 件 / 到着 {len(arr_flights)} 件",
            "embeds": embeds,
        }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print(f"[Discord] 通知送信完了 ({len(all_flights)} 件)")
        else:
            print(f"[Discord] 送信失敗: HTTP {resp.status_code} {resp.text[:200]}")
    except requests.RequestException as e:
        print(f"[Discord] 送信エラー: {e}")


# ---------------------------------------------------------------------------
# 集計・出力
# ---------------------------------------------------------------------------


def fetch_all(session: requests.Session, flight_type: str) -> list[dict]:
    """全対象日分のデータを取得し、Nagoya 便のみ返す。"""
    direction = "出発" if flight_type == "dep" else "到着"
    results: list[dict] = []
    for date in TARGET_DATES:
        label = DATE_LABELS[date]
        data = fetch_for_date(session, date, flight_type)
        if data is None:
            print(f"  [{label} ({date})] データ取得失敗")
            continue
        flights = extract_nagoya_flights(data, flight_type)
        print(f"  [{label} ({date})] Nagoya {direction}: {len(flights)} 件")
        results.extend(flights)
    return results


def print_section(flights: list[dict], title: str) -> None:
    """フライト一覧をセクション形式で標準出力に表示する。"""
    print(f"\n{'=' * 60}")
    print(f" {title}  合計: {len(flights)} 件")
    print(f"{'=' * 60}")
    if not flights:
        print("  (該当なし)")
        return
    for i, flight in enumerate(flights, 1):
        label = DATE_LABELS.get(flight.get("date", ""), "")
        print(f"\n[{i}] {label} ({flight.get('date', '-')}) ---")
        print(format_flight(flight))


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60)
    print("香港空港 カーゴフライト Nagoya (NGO) 便 検索")
    print(f"取得対象日: {', '.join(TARGET_DATES)}")
    print("=" * 60)

    session = requests.Session()
    session.headers.update(HEADERS)

    print("\n【出発便】 HKG → NGO")
    dep_flights = fetch_all(session, "dep")

    print("\n【到着便】 NGO → HKG")
    arr_flights = fetch_all(session, "arr")

    print_section(dep_flights, "出発便 (HKG → NGO)")
    print_section(arr_flights, "到着便 (NGO → HKG)")

    notify_discord(dep_flights, arr_flights)


if __name__ == "__main__":
    main()
