#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
香港国際空港（HKG）カーゴフライト（出発・到着）の Nagoya (NGO) 便を抽出して Discord に通知する。
表示は Changi / 仁川 の通知と共通の表形式。

使い方:
    python hkg_nagoya_flights.py              # 3 日分を取得して Discord へ通知
    python hkg_nagoya_flights.py --dry-run    # 送信せず表示のみ
    python hkg_nagoya_flights.py --only-new   # 前回通知から変化が無ければ送らない

前回通知からの変化（実績時刻が入った、予定が変わった、新しい便 など）は行頭に「!」を付け、
状態欄に「←前回値」を添えて目立たせる。状態は notified_state.json に保存。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "notified_state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.hongkongairport.com/en/flights/departures/cargo.page",
}
BASE_URL = "https://www.hongkongairport.com/flightinfo-rest/rest/flights"
PARAM_TEMPLATES = [
    "type={type}&cargo=true&lang=en&date={date}",
    "type={type}&cargo=Y&lang=en&date={date}",
]
NAGOYA_WORDS = ("nagoya", "chubu", "centrair", "中部")
NAGOYA_CODE = "ngo"
WEEKDAY_JA = "月火水木金土日"

# 名古屋側時刻の推定に使う標準所要時間（分）。香港 UTC+8 → 日本 UTC+9 なので +1h
FLIGHT_MIN_HKG_NGO = 220
FLIGHT_MIN_NGO_HKG = 250
TZ_DIFF_MIN = 60



# ---------------------------------------------------------------- 設定
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _color(env_name: str, default: int) -> int:
    v = os.environ.get(env_name, "").strip().lstrip("#")
    try:
        return int(v, 16) if v else default
    except ValueError:
        return default


# ---------------------------------------------------------------- 取得
def fetch_for_date(session: requests.Session, date: str, flight_type: str) -> list | None:
    for tmpl in PARAM_TEMPLATES:
        url = f"{BASE_URL}?{tmpl.format(type=flight_type, date=date)}"
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
    return None


def _hit(*parts) -> bool:
    hay = " ".join(str(p) for p in parts if p).lower()
    toks = set(re.split(r"[\s(),/]+", hay))
    return any(w in hay for w in NAGOYA_WORDS) or NAGOYA_CODE in toks


def extract_nagoya_flights(data: list, flight_type: str) -> list[dict]:
    results = []
    for date_block in data or []:
        date_str = date_block.get("date", "")
        for flight in date_block.get("list", []):
            targets = (flight.get("origin") if flight_type == "arr"
                       else flight.get("destination") or flight.get("dest")) or []
            if _hit(*targets):
                results.append({"date": date_str, "_type": flight_type, **flight})
    return results


def fetch_all(session: requests.Session, flight_type: str, dates: list[str]) -> list[dict]:
    """対象日を並列取得し、Nagoya 便のみ返す"""
    direction = "出発" if flight_type == "dep" else "到着"
    with ThreadPoolExecutor(max_workers=min(4, len(dates))) as ex:
        pages = list(ex.map(lambda d: fetch_for_date(session, d, flight_type), dates))
    results: list[dict] = []
    for date, data in zip(dates, pages):
        if data is None:
            print(f"  [{date}] {direction} データ取得失敗", file=sys.stderr)
            continue
        flights = extract_nagoya_flights(data, flight_type)
        print(f"  [{date}] Nagoya {direction}: {len(flights)} 件")
        results.extend(flights)
    seen, uniq = set(), []
    for f in results:
        key = (f["date"], f.get("time"), tuple(x.get("no") for x in f.get("flight", [])))
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    uniq.sort(key=lambda f: (f["date"], f.get("time") or ""))
    return uniq


# ---------------------------------------------------------------- 整形
def day_label(date_str: str, today: dt.date) -> str:
    try:
        d = dt.date.fromisoformat(date_str[:10])
    except ValueError:
        return date_str
    rel = {-1: "昨日", 0: "今日", 1: "明日", 2: "明後日"}.get((d - today).days)
    base = f"{d:%m/%d} ({WEEKDAY_JA[d.weekday()]})"
    return f"{rel} {base}" if rel else base


def flight_code(flight: dict) -> str:
    nos = [x.get("no", "").strip() for x in flight.get("flight", []) if x.get("no")]
    if not nos:
        return "?"
    fn = nos[0].replace(" ", "")
    m = re.match(r"^([A-Z][A-Z0-9]|[0-9][A-Z])(\d{1,5}[A-Z]?)$", fn.upper())
    code = f"{m.group(1)} {m.group(2)}" if m else nos[0]
    return code if len(nos) == 1 else f"{code}+"


def parse_dt(date_str: str, hhmm: str | None) -> dt.datetime | None:
    if not hhmm:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", hhmm)
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{date_str[:10]} {int(m.group(1)):02d}:{m.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parse_status(flight: dict, sched_dt: dt.datetime | None) -> tuple[str, dt.datetime | None, bool]:
    """HKG の status 文字列 → (日本語状態, 実績 or 変更後時刻, 欠航か)
    例: 'Dep 04:37' → ('出発済 04:37', 04:37, False) / 'Est at 05:20' → ('変更 05:20', 05:20, False)
    """
    is_dep = flight.get("_type") == "dep"
    raw = (flight.get("status") or "").strip()
    s = raw.lower()
    date_str = flight.get("date", "")
    t = parse_dt(date_str, raw)
    # 日付をまたぐ実績（例: 23:50 予定 → 00:10 実績）は翌日扱い
    if t and sched_dt and (t - sched_dt).total_seconds() < -12 * 3600:
        t += dt.timedelta(days=1)
    tag = "出発済" if is_dep else "到着済"
    if "cancel" in s:
        return "欠航", None, True
    if s.startswith("dep") or s.startswith("landed") or s.startswith("arr") or s.startswith("at gate"):
        return (f"{tag} {t:%H:%M}" if t else tag), t, False
    if s.startswith("est"):
        if t and sched_dt and t != sched_dt:
            return f"変更 {t:%H:%M}", t, False
        return "定刻", None, False
    if "delay" in s:
        return "遅延", t, False
    if not raw:
        return "定刻", None, False
    return raw, t, False


def _hhmm_rel(t: dt.datetime | None, ref_date: dt.date | None, approx: bool) -> str:
    if t is None:
        return "--:--"
    s = f"{t:%H:%M}" + ("*" if approx else "")
    if ref_date:
        diff = (t.date() - ref_date).days
        if diff:
            s += f"{diff:+d}"
    return s


def fmt_md(d) -> str:
    """11/09(月) 形式"""
    if d is None:
        return "--/--"
    return f"{d:%m/%d}({WEEKDAY_JA[d.weekday()]})"


def _mins_diff(a: str, b: str):
    """'04:37' - '04:35' → +2（日またぎは ±12h 以内で補正）"""
    try:
        ha, ma = map(int, a[:5].split(":")); hb, mb = map(int, b[:5].split(":"))
    except ValueError:
        return None
    d = (ha * 60 + ma) - (hb * 60 + mb)
    if d > 720:
        d -= 1440
    elif d < -720:
        d += 1440
    return d


def pretty_status(status: str, sched_txt: str, is_dep: bool, prev_status, prev_sched, kind: str) -> str:
    """内部状態（'定刻' / '出発済 04:37' / '変更 05:20' / '遅延' / '欠航' / その他）→ 見やすい 1 行
    例: ✅ 出発済み 04:37（予定 04:35、+2分） / 🟡 時刻変更 → 05:20（予定 04:35、+45分） / 🟢 定刻どおり
    """
    s = status.strip()
    tag = "出発" if is_dep else "到着"

    def delta(t: str) -> str:
        d = _mins_diff(t, sched_txt)
        if d is None:
            return ""
        return "、定刻" if d == 0 else f"、{d:+d}分"

    if s == "定刻":
        line = "🟢 定刻どおり"
    elif s.startswith(("出発済", "到着済")):
        actual = s.split(" ", 1)[1] if " " in s else ""
        line = f"✅ {tag}済み {actual}（予定 {sched_txt}{delta(actual)}）" if actual else f"✅ {tag}済み"
    elif s.startswith("変更"):
        new = s.split(" ", 1)[1] if " " in s else ""
        line = f"🟡 時刻変更 → {new}（予定 {sched_txt}{delta(new)}）" if new else "🟡 時刻変更"
    elif s == "遅延":
        line = "🟠 遅延"
    elif s == "欠航":
        line = "🔴 欠航"
    else:
        line = f"🔵 {s}"

    if kind == "new":
        line += "\n🆕 新しく追加された便"
    elif kind == "changed":
        if prev_sched and prev_sched != sched_txt:
            line += f"\n🔔 前回: 予定 {prev_sched}・{prev_status or '?'}"
        else:
            line += f"\n🔔 前回: {prev_status or '?'}"
    return line


def ngo_time_suffix(txt: str) -> str:
    """'05:40*+1' → ' (翌日・推定)'"""
    parts = []
    if "+1" in txt:
        parts.append("翌日")
    elif "-1" in txt:
        parts.append("前日")
    elif "+2" in txt:
        parts.append("翌々日")
    if "*" in txt:
        parts.append("推定")
    return f" ({'・'.join(parts)})" if parts else ""


def describe(flight: dict, today: dt.date, prev: dict | None) -> tuple[dict, dict, str]:
    """1 便 = 埋め込みフィールド 1 枠。戻り値: (field, 署名, 変化種別 ""/"new"/"changed")"""
    is_dep = flight.get("_type") == "dep"
    date_str = flight.get("date", "")
    sched_dt = parse_dt(date_str, flight.get("time"))
    sched_txt = f"{sched_dt:%H:%M}" if sched_dt else (flight.get("time") or "--:--")
    ref = sched_dt.date() if sched_dt else None
    status, eff_dt, cancelled = parse_status(flight, sched_dt)

    base = eff_dt or sched_dt
    if cancelled or base is None:
        ngo_dt = None
    elif is_dep:
        ngo_dt = base + dt.timedelta(minutes=FLIGHT_MIN_HKG_NGO + TZ_DIFF_MIN)
    else:
        ngo_dt = base - dt.timedelta(minutes=FLIGHT_MIN_NGO_HKG - TZ_DIFF_MIN)
    ngo_txt = _hhmm_rel(ngo_dt, ref, True)

    ports = (flight.get("destination") if is_dep else flight.get("origin")) or []
    others = [p for p in ports if not _hit(p)]
    if is_dep:
        route_text = "HKG → NGO" + (f" → {others[-1]}" if others else "")
    else:
        route_text = (f"{others[0]} → " if others else "") + "NGO → HKG"
    DATE_STR = date_str

    sig = {"sched": sched_txt, "status": status}
    mark = " "
    kind = ""
    if prev is None:
        mark, kind = "+", "new"
    elif prev.get("status") != status or prev.get("sched") != sched_txt:
        mark, kind = "!", "changed"
    status_disp = pretty_status(status, sched_txt, is_dep, (prev or {}).get("status"), (prev or {}).get("sched"), kind)

    # 1 便 = 埋め込みフィールド 1 枠
    badge = {"+": "🆕 ", "!": "🔔 "}.get(mark, "")
    icon = "🛫" if is_dep else "🛬"
    code = flight_code(flight)
    name = f"{badge}{icon} {code} ｜ {day_label(date_str, today)}"
    hkg_line = f"HKG {fmt_md(sched_dt)} {sched_txt} HKT"
    ngo_line = "NGO --/-- --:-- JST" if ngo_dt is None else f"NGO {fmt_md(ngo_dt)} {ngo_dt:%H:%M} JST (推定)"
    if is_dep:
        times = [f"`🛫 {hkg_line}`", f"`🛬 {ngo_line}`"]
    else:
        times = [f"`🛫 {ngo_line}`", f"`🛬 {hkg_line}`"]
    value = "\n".join([route_text, *times, status_disp])
    kind = {"+": "new", "!": "changed"}.get(mark, "")
    return {"name": name[:256], "value": value[:1024], "inline": False}, sig, kind


def flight_key(flight: dict) -> str:
    nos = "/".join(x.get("no", "") for x in flight.get("flight", []))
    return f"{flight.get('_type')}_{flight.get('date')}_{nos}"


def build_fields(flights: list[dict], today: dt.date, state: dict, new_state: dict) -> tuple[list[dict], int]:
    fields, changed = [], 0
    for f in flights:
        key = flight_key(f)
        field, sig, kind = describe(f, today, state.get(key))
        new_state[key] = sig
        if kind:
            changed += 1
        fields.append(field)
    return fields, changed


def strip_new_badges(fields: list[dict]) -> list[dict]:
    return [{**f, "name": f["name"].replace("🆕 ", ""), "value": f["value"].replace("\n🆕 新しく追加された便", "")} for f in fields]


def fields_to_text(fields: list[dict]) -> str:
    if not fields:
        return "  該当便なし"
    out = []
    for f in fields:
        if f["value"] == "\u200b":
            out.append(f" {f['name']}")
        else:
            out.append(f"  {f['name']}\n" + "\n".join("    " + l for l in f["value"].splitlines()))
    return "\n".join(out)


# ---------------------------------------------------------------- Discord
def _embeds(title: str, fields: list[dict], color: int) -> list[dict]:
    if not fields:
        return [{"title": title, "description": "該当便なし", "color": color}]
    return [{"title": title if i == 0 else f"{title} (続き)", "color": color, "fields": fields[i:i + 24]}
            for i in range(0, len(fields), 24)]


def post_discord(webhook: str, title: str, period: str, dep_fields: list[dict], arr_fields: list[dict],
                 n_dep: int, n_arr: int, n_changed: int) -> None:
    embeds = _embeds(f"出発便  HKG → NGO  ({n_dep} 件)", dep_fields, _color("DISCORD_COLOR_DEP", 0x3B82F6)) + \
             _embeds(f"到着便  NGO → HKG  ({n_arr} 件)", arr_fields, _color("DISCORD_COLOR_ARR", 0x22C55E))
    embeds[-1]["footer"] = {"text": f"HKT=香港時間 / JST=日本時間 ・ 推定=標準所要時間からの概算 ・ 🔔=前回通知から変化 ・ 更新 {dt.datetime.now():%m/%d %H:%M} JST"}
    chg = f"  ・ 変化 {n_changed} 件" if n_changed else ""
    for i in range(0, len(embeds), 10):
        payload = {"embeds": embeds[i:i + 10]}
        if i == 0:
            payload["content"] = f"**{title}**{chg}\n対象期間: {period}"
        r = requests.post(webhook, json=payload, timeout=15)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "2")))
            r = requests.post(webhook, json=payload, timeout=15)
        r.raise_for_status()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------- main
def main() -> int:
    global FLIGHT_MIN_HKG_NGO, FLIGHT_MIN_NGO_HKG
    load_dotenv(BASE_DIR / ".env")
    FLIGHT_MIN_HKG_NGO = int(os.environ.get("FLIGHT_MINUTES_HKG_NGO", FLIGHT_MIN_HKG_NGO))
    FLIGHT_MIN_NGO_HKG = int(os.environ.get("FLIGHT_MINUTES_NGO_HKG", FLIGHT_MIN_NGO_HKG))

    p = argparse.ArgumentParser(description="HKG カーゴ便から名古屋発着便を Discord へ通知")
    p.add_argument("--date", help="起点日 YYYY-MM-DD（既定: 今日）")
    p.add_argument("--days", type=int, default=3, help="取得する日数（既定 3）")
    p.add_argument("--dry-run", action="store_true", help="Discord へ送信せず表示のみ")
    p.add_argument("--only-new", action="store_true", help="前回通知から変化が無ければ送らない")
    p.add_argument("--notify-empty", action="store_true", help="該当便 0 件でも通知する")
    p.add_argument("--webhook", help="Discord Webhook URL（環境変数より優先）")
    p.add_argument("--reset-state", action="store_true", help="前回状態を無視する（全件を新規扱い）")
    a = p.parse_args()

    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    dates = [(today + dt.timedelta(days=i)).isoformat() for i in range(max(1, a.days))]

    session = requests.Session()
    session.headers.update(HEADERS)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_dep = ex.submit(fetch_all, session, "dep", dates)
        f_arr = ex.submit(fetch_all, session, "arr", dates)
        dep, arr = f_dep.result(), f_arr.result()
    print(f"取得 {time.perf_counter() - t0:.1f}s")

    state = {} if a.reset_state else load_state()
    new_state: dict = {}
    dep_fields, c1 = build_fields(dep, today, state, new_state)
    arr_fields, c2 = build_fields(arr, today, state, new_state)
    n_changed = c1 + c2 if state else 0  # 初回（状態ファイル無し）は変化件数を数えない
    if not state:
        dep_fields, arr_fields = strip_new_badges(dep_fields), strip_new_badges(arr_fields)

    period = f"{day_label(dates[0], today)} 〜 {day_label(dates[-1], today)}"
    title = f"HKG ⇄ NGO カーゴ便  |  出発 {len(dep)} 件 / 到着 {len(arr)} 件"
    print(title, f"(変化 {n_changed} 件)" if n_changed else "")
    print(f"対象期間: {period}")
    print("[出発便 HKG → NGO]"); print(fields_to_text(dep_fields))
    print("[到着便 NGO → HKG]"); print(fields_to_text(arr_fields))

    if a.dry_run:
        return 0
    if not (dep or arr) and not a.notify_empty:
        print("通知対象なしのため送信スキップ（--notify-empty で送信可）")
        return 0
    if a.only_new and state and n_changed == 0:
        print("前回通知から変化なしのためスキップ")
        return 0

    webhook = a.webhook or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("[ERROR] DISCORD_WEBHOOK_URL が未設定です（.env か環境変数で指定）", file=sys.stderr)
        return 3
    try:
        post_discord(webhook, title, period, dep_fields, arr_fields, len(dep), len(arr), n_changed)
        print("Discord へ送信しました")
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Discord 送信失敗: {e}", file=sys.stderr)
        return 4
    # 送信できたときだけ状態を更新（古い日付は捨てる）
    keep = {k: v for k, v in new_state.items()}
    STATE_FILE.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
