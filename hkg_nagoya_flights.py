#!/usr/bin/env python3
# coding: utf-8
# -----------------------------------------------------------------------------------#
# @file           HKG/hkg_nagoya_flights.py
# @brief          香港国際空港(HKG) カーゴ便 名古屋(NGO)関連便 Discord 通知スクリプト
# @author         Kidera Shunsuke
# @date           2026/11/08
# $Version:       1.01
# $Revision:      2026/11/11
# @note           hongkongairport.com の flightinfo-rest API から出発・到着便を取得し NGO 便を通知
# @attention      プロジェクトのコーディングフォーマット・コメントフォーマット遵守
# @par            History
#                 v1.00 (2026/11/08) - 新規作成
#                 v1.01 (2026/11/11) - Discord 通知フォーマットを Changi 通知に完全準拠
#                                      （便間の空行・フッター文言・日付またぎの変更時刻表記）
# Copyright (c) 2026. All Rights reserved.
#
# -----------------------------------------------------------------------------------#

"""
========================================================================================
モジュール概要：香港国際空港(HKG) カーゴ便 名古屋(NGO)関連便 Discord 通知スクリプト
========================================================================================
香港国際空港（HKG）カーゴフライト（出発・到着）の Nagoya (NGO) 便を抽出して Discord に通知します。
表示は Changi / 仁川 の通知と共通のフォーマット（1 便 = 埋め込みフィールド 1 枠）です。

使い方:
    python hkg_nagoya_flights.py              # 3 日分を取得して Discord へ通知
    python hkg_nagoya_flights.py --dry-run    # 送信せず表示のみ
    python hkg_nagoya_flights.py --only-new   # 前回通知から変化が無ければ送らない

変化検知:
    前回通知からの変化（実績時刻が入った、予定が変わった、新しい便 など）は 🔔(変化) / 🆕(新規) の印を付け、
    状態欄に前回値を添えて目立たせます。状態は notified_state.json に保存します。
========================================================================================
"""

# 標準ライブラリ
import argparse                                   # コマンドライン引数解析
import datetime as dt                             # 日付・時刻処理
import json                                       # JSON 入出力
import os                                         # 環境変数アクセス
import re                                         # 正規表現
import sys                                        # 標準エラー出力・終了コード
import time                                       # 時間計測・待機
from concurrent.futures import ThreadPoolExecutor  # 並列リクエスト
from pathlib import Path                          # パス操作

# 外部ライブラリ
import requests                                   # HTTP 通信


# ======================================================================================#
# ファイル・パス設定
# ======================================================================================#
BASE_DIR = Path(__file__).resolve().parent        # スクリプト配置フォルダ
STATE_FILE = BASE_DIR / "notified_state.json"     # 前回通知状態の保存先

# ======================================================================================#
# 香港国際空港 API 設定
# ======================================================================================#
# HTTP リクエストヘッダー
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.hongkongairport.com/en/flights/departures/cargo.page",
}
BASE_URL = "https://www.hongkongairport.com/flightinfo-rest/rest/flights"  # 便情報 REST API
# クエリパラメータのテンプレート（cargo パラメータの表記揺れに対応するため順に試す）
PARAM_TEMPLATES = [
    "type={type}&cargo=true&lang=en&date={date}",
    "type={type}&cargo=Y&lang=en&date={date}",
]

# ======================================================================================#
# 名古屋判定・表示設定
# ======================================================================================#
NAGOYA_WORDS = ("nagoya", "chubu", "centrair", "中部")  # 空港名称に含まれる語（小文字）
NAGOYA_CODE = "ngo"                               # IATA コード（小文字）
WEEKDAY_JA = "月火水木金土日"                      # 曜日表記（weekday() のインデックス順）

# ======================================================================================#
# 所要時間・時差設定
# ======================================================================================#
# 名古屋側時刻の推定に使う標準所要時間（分）。香港 UTC+8 → 日本 UTC+9 なので +1h
FLIGHT_MIN_HKG_NGO = 220                          # HKG→NGO 所要時間（分）
FLIGHT_MIN_NGO_HKG = 250                          # NGO→HKG 所要時間（分）
TZ_DIFF_MIN = 60                                  # 香港時間 → 日本時間 の時差（分）
JST = dt.timezone(dt.timedelta(hours=9), "JST")   # 日本時間（フッターの更新時刻用。tzdata 不要の固定オフセット）



# ======================================================================================#
# 設定読み込み
# ======================================================================================#

# ======================================================================================#
# @function name:   load_dotenv
# @function start
def load_dotenv(path: Path) -> None:
    """
    ----------------------------------------------------------------------
    関数概要：軽量な .env 読み込み（python-dotenv 不要）
    - 既存の環境変数は上書きしない
    - 空行・コメント行・"=" を含まない行は無視
    ----------------------------------------------------------------------
    Args:
        - path -> Path --- .env ファイルのパス
    ----------------------------------------------------------------------
    Returns:
        - なし
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# @function name:   load_dotenv
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   _color
# @function start
def _color(env_name: str, default: int) -> int:
    """
    ----------------------------------------------------------------------
    関数概要：環境変数から埋め込みの色（16 進）を取得する
    ----------------------------------------------------------------------
    Args:
        - env_name -> str --- 環境変数名
        - default -> int --- 未設定・不正時の既定値
    ----------------------------------------------------------------------
    Returns:
        - color -> int --- 色コード
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    v = os.environ.get(env_name, "").strip().lstrip("#")
    try:
        return int(v, 16) if v else default
    except ValueError:
        return default


# @function name:   _color
# @function end
# ======================================================================================#


# ======================================================================================#
# 便情報取得
# ======================================================================================#

# ======================================================================================#
# @function name:   fetch_for_date
# @function start
def fetch_for_date(session: requests.Session, date: str, flight_type: str) -> list | None:
    """
    ----------------------------------------------------------------------
    関数概要：1 日・1 方向分の便情報を API から取得する
    - PARAM_TEMPLATES を順に試し、200 が返った最初の結果を返す
    ----------------------------------------------------------------------
    Args:
        - session -> requests.Session --- HTTP セッション
        - date -> str --- 対象日（YYYY-MM-DD）
        - flight_type -> str --- 方向（"dep" / "arr"）
    ----------------------------------------------------------------------
    Returns:
        - data -> list | None --- API レスポンス（日付ブロックのリスト）。全テンプレート失敗時は None
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    for tmpl in PARAM_TEMPLATES:
        url = f"{BASE_URL}?{tmpl.format(type=flight_type, date=date)}"
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except (requests.RequestException, ValueError):
            pass
    return None


# @function name:   fetch_for_date
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   _hit
# @function start
def _hit(*parts) -> bool:
    """
    ----------------------------------------------------------------------
    関数概要：与えられた文字列群に名古屋を示す語・コードが含まれるか判定する
    ----------------------------------------------------------------------
    Args:
        - *parts --- 判定対象の文字列（None・空は無視）
    ----------------------------------------------------------------------
    Returns:
        - is_hit -> bool --- 名古屋関連ならば True
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    hay = " ".join(str(p) for p in parts if p).lower()
    toks = set(re.split(r"[\s(),/]+", hay))
    return any(w in hay for w in NAGOYA_WORDS) or NAGOYA_CODE in toks


# @function name:   _hit
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   extract_nagoya_flights
# @function start
def extract_nagoya_flights(data: list, flight_type: str) -> list[dict]:
    """
    ----------------------------------------------------------------------
    関数概要：API レスポンスから名古屋関連便のみを抽出する
    - 出発便は destination、到着便は origin を判定対象にする
    - 各便に取得日（date）と方向（_type）を付加する
    ----------------------------------------------------------------------
    Args:
        - data -> list --- API レスポンス（日付ブロックのリスト）
        - flight_type -> str --- 方向（"dep" / "arr"）
    ----------------------------------------------------------------------
    Returns:
        - results -> list[dict] --- 名古屋関連便のリスト
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    results = []
    for date_block in data or []:
        date_str = date_block.get("date", "")
        for flight in date_block.get("list", []):
            targets = (flight.get("origin") if flight_type == "arr"
                       else flight.get("destination") or flight.get("dest")) or []
            if _hit(*targets):
                results.append({"date": date_str, "_type": flight_type, **flight})
    return results


# @function name:   extract_nagoya_flights
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   fetch_all
# @function start
def fetch_all(session: requests.Session, flight_type: str, dates: list[str]) -> list[dict]:
    """
    ----------------------------------------------------------------------
    関数概要：対象日を並列取得し、Nagoya 便のみ返す
    - 取得失敗日は警告を出して省略
    - 取得日・時刻・便名で重複を除去し、日付・時刻順にソート
    ----------------------------------------------------------------------
    Args:
        - session -> requests.Session --- HTTP セッション
        - flight_type -> str --- 方向（"dep" / "arr"）
        - dates -> list[str] --- 対象日のリスト（YYYY-MM-DD）
    ----------------------------------------------------------------------
    Returns:
        - uniq -> list[dict] --- 名古屋関連便のリスト（重複除去・ソート済み）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    direction = "出発" if flight_type == "dep" else "到着"
    # 並列取得
    with ThreadPoolExecutor(max_workers=min(4, len(dates))) as ex:
        pages = list(ex.map(lambda d: fetch_for_date(session, d, flight_type), dates))
    # 名古屋関連便の抽出
    results: list[dict] = []
    for date, data in zip(dates, pages):
        if data is None:
            print(f"  [{date}] {direction} データ取得失敗", file=sys.stderr)
            continue
        flights = extract_nagoya_flights(data, flight_type)
        print(f"  [{date}] Nagoya {direction}: {len(flights)} 件")
        results.extend(flights)
    # 重複除去・ソート
    seen, uniq = set(), []
    for f in results:
        key = (f["date"], f.get("time"), tuple(x.get("no") for x in f.get("flight", [])))
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    uniq.sort(key=lambda f: (f["date"], f.get("time") or ""))
    return uniq


# @function name:   fetch_all
# @function end
# ======================================================================================#


# ======================================================================================#
# 整形
# ======================================================================================#

# ======================================================================================#
# @function name:   day_label
# @function start
def day_label(date_str: str, today: dt.date) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：日付文字列を相対表記付きのラベルに整形する
    - 例: '今日 11/08 (日)' / '明日 11/09 (月)' / '11/12 (木)'
    ----------------------------------------------------------------------
    Args:
        - date_str -> str --- 対象日（YYYY-MM-DD）
        - today -> dt.date --- 基準日
    ----------------------------------------------------------------------
    Returns:
        - label -> str --- 整形後ラベル（変換不能時は入力をそのまま返す）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    try:
        d = dt.date.fromisoformat(date_str[:10])
    except ValueError:
        return date_str
    rel = {-1: "昨日", 0: "今日", 1: "明日", 2: "明後日"}.get((d - today).days)
    base = f"{d:%m/%d} ({WEEKDAY_JA[d.weekday()]})"
    return f"{rel} {base}" if rel else base


# @function name:   day_label
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   flight_code
# @function start
def flight_code(flight: dict) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：航空会社コードと便名の間にスペースを入れた表示用便名を返す
    - 例: 'CX2088' → 'CX 2088'
    - 共同運航などで便名が複数ある場合は先頭の便名に '+' を付ける
    ----------------------------------------------------------------------
    Args:
        - flight -> dict --- 便情報
    ----------------------------------------------------------------------
    Returns:
        - code -> str --- 表示用便名（便名無しは '?'）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    nos = [x.get("no", "").strip() for x in flight.get("flight", []) if x.get("no")]
    if not nos:
        return "?"
    fn = nos[0].replace(" ", "")
    m = re.match(r"^([A-Z][A-Z0-9]|[0-9][A-Z])(\d{1,5}[A-Z]?)$", fn.upper())
    code = f"{m.group(1)} {m.group(2)}" if m else nos[0]
    return code if len(nos) == 1 else f"{code}+"


# @function name:   flight_code
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   parse_dt
# @function start
def parse_dt(date_str: str, hhmm: str | None) -> dt.datetime | None:
    """
    ----------------------------------------------------------------------
    関数概要：日付文字列と HH:MM を含む文字列から datetime を生成する
    ----------------------------------------------------------------------
    Args:
        - date_str -> str --- 対象日（YYYY-MM-DD）
        - hhmm -> str | None --- HH:MM を含む文字列（例: 'Dep 04:37'）
    ----------------------------------------------------------------------
    Returns:
        - datetime -> dt.datetime | None --- 変換結果（変換不能時は None）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if not hhmm:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", hhmm)
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{date_str[:10]} {int(m.group(1)):02d}:{m.group(2)}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


# @function name:   parse_dt
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   parse_status
# @function start
def parse_status(flight: dict, sched_dt: dt.datetime | None) -> tuple[str, dt.datetime | None, bool]:
    """
    ----------------------------------------------------------------------
    関数概要：HKG の status 文字列を (日本語状態, 実績 or 変更後時刻, 欠航か) に変換する
    - 例: 'Dep 04:37' → ('出発済 04:37', 04:37, False)
          'Est at 05:20' → ('変更 05:20', 05:20, False)
    - 日付をまたぐ実績（例: 23:50 予定 → 00:10 実績）は翌日扱い
    - 変更後の時刻が予定と別日になる場合は '変更 00:10 (11/09)' のように日付を添える
    ----------------------------------------------------------------------
    Args:
        - flight -> dict --- 便情報
        - sched_dt -> dt.datetime | None --- 予定時刻
    ----------------------------------------------------------------------
    Returns:
        - (status, t, cancelled) -> tuple[str, dt.datetime | None, bool] --- 内部状態、実績/変更後時刻、欠航フラグ
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
        - revision       v1.01 (2026/11/11) - 変更後時刻が別日の場合に日付を添える（Changi 通知に準拠）
    ----------------------------------------------------------------------
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
            txt = f"変更 {t:%H:%M}"
            if t.date() != sched_dt.date():
                txt += f" ({t:%m/%d})"
            return txt, t, False
        return "定刻", None, False
    if "delay" in s:
        return "遅延", t, False
    if not raw:
        return "定刻", None, False
    return raw, t, False


# @function name:   parse_status
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   _hhmm_rel
# @function start
def _hhmm_rel(t: dt.datetime | None, ref_date: dt.date | None, approx: bool) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：時刻を基準日との日差と推定マーク付きで整形する
    - 例: '12:17*' / '04:15*+1' / '18:10-1'
    ----------------------------------------------------------------------
    Args:
        - t -> dt.datetime | None --- 対象時刻
        - ref_date -> dt.date | None --- 基準日（日差計算用）
        - approx -> bool --- 推定値かどうか（True なら "*" を付ける）
    ----------------------------------------------------------------------
    Returns:
        - text -> str --- 整形後文字列（None の場合は '--:--'）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if t is None:
        return "--:--"
    s = f"{t:%H:%M}" + ("*" if approx else "")
    if ref_date:
        diff = (t.date() - ref_date).days
        if diff:
            s += f"{diff:+d}"
    return s


# @function name:   _hhmm_rel
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   fmt_md
# @function start
def fmt_md(d) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：日付を '11/09(月)' 形式に整形する
    ----------------------------------------------------------------------
    Args:
        - d -> dt.datetime | dt.date | None --- 対象日付
    ----------------------------------------------------------------------
    Returns:
        - text -> str --- 整形後文字列（None の場合は '--/--'）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if d is None:
        return "--/--"
    return f"{d:%m/%d}({WEEKDAY_JA[d.weekday()]})"


# @function name:   fmt_md
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   _mins_diff
# @function start
def _mins_diff(a: str, b: str):
    """
    ----------------------------------------------------------------------
    関数概要：2 つの HH:MM の差分（分）を求める
    - 例: '04:37' - '04:35' → +2
    - 日またぎは ±12h 以内で補正
    ----------------------------------------------------------------------
    Args:
        - a -> str --- 時刻（HH:MM）
        - b -> str --- 基準時刻（HH:MM）
    ----------------------------------------------------------------------
    Returns:
        - minutes -> int | None --- a - b の分差（変換不能時は None）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
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


# @function name:   _mins_diff
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   pretty_status
# @function start
def pretty_status(status: str, sched_txt: str, is_dep: bool, prev_status, prev_sched, kind: str) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：内部状態を見やすい表示行に変換する
    - 入力: '定刻' / '出発済 04:37' / '変更 05:20' / '遅延' / '欠航' / その他
    - 例: ✅ 出発済み 04:37（予定 04:35、+2分）
          🟡 時刻変更 → 05:20（予定 04:35、+45分）
          🟢 定刻どおり
    - 新規・変化がある場合は 2 行目に 🆕 / 🔔 の補足を付ける
    ----------------------------------------------------------------------
    Args:
        - status -> str --- 内部状態文字列（parse_status の戻り値）
        - sched_txt -> str --- 予定時刻（HH:MM）
        - is_dep -> bool --- 出発便かどうか
        - prev_status -> str | None --- 前回通知時の状態
        - prev_sched -> str | None --- 前回通知時の予定時刻
        - kind -> str --- 変化種別（"" / "new" / "changed"）
    ----------------------------------------------------------------------
    Returns:
        - line -> str --- 表示用文字列（1〜2 行）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    s = status.strip()
    tag = "出発" if is_dep else "到着"

    # 予定時刻との差分を '、+N分' / '、定刻' 形式で返す
    def delta(t: str) -> str:
        d = _mins_diff(t, sched_txt)
        if d is None:
            return ""
        return "、定刻" if d == 0 else f"、{d:+d}分"

    # 状態別の 1 行目
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

    # 新規・変化の補足（2 行目）
    if kind == "new":
        line += "\n🆕 新しく追加された便"
    elif kind == "changed":
        if prev_sched and prev_sched != sched_txt:
            line += f"\n🔔 前回: 予定 {prev_sched}・{prev_status or '?'}"
        else:
            line += f"\n🔔 前回: {prev_status or '?'}"
    return line


# @function name:   pretty_status
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   ngo_time_suffix
# @function start
def ngo_time_suffix(txt: str) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：名古屋側時刻文字列の日差・推定マークを日本語の補足に変換する
    - 例: '05:40*+1' → ' (翌日・推定)'
    ----------------------------------------------------------------------
    Args:
        - txt -> str --- _hhmm_rel で整形した時刻文字列
    ----------------------------------------------------------------------
    Returns:
        - suffix -> str --- 補足文字列（補足なしの場合は空文字）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
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


# @function name:   ngo_time_suffix
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   describe
# @function start
def describe(flight: dict, today: dt.date, prev: dict | None) -> tuple[dict, dict, str]:
    """
    ----------------------------------------------------------------------
    関数概要：1 便を Discord 埋め込みフィールド 1 枠に整形する
    - name : 🛫 CX 2088 ｜ 今日 11/08 (日)
    - value: HKG → NGO
             `🛫 HKG 11/08(日) 04:35 HKT`
             `🛬 NGO 11/08(日) 09:15 JST (推定)`
             ✅ 出発済み 04:37（予定 04:35、+2分）
    ----------------------------------------------------------------------
    Args:
        - flight -> dict --- 便情報
        - today -> dt.date --- 基準日（相対日付ラベル用）
        - prev -> dict | None --- 前回通知時の署名（無ければ新規扱い）
    ----------------------------------------------------------------------
    Returns:
        - (field, sig, kind) -> tuple[dict, dict, str] --- 埋め込みフィールド、署名、変化種別（"" / "new" / "changed"）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    # 予定時刻・状態の算出
    is_dep = flight.get("_type") == "dep"
    date_str = flight.get("date", "")
    sched_dt = parse_dt(date_str, flight.get("time"))
    sched_txt = f"{sched_dt:%H:%M}" if sched_dt else (flight.get("time") or "--:--")
    ref = sched_dt.date() if sched_dt else None
    status, eff_dt, cancelled = parse_status(flight, sched_dt)

    # 名古屋側の時刻（標準所要時間 + 時差 からの推定）
    base = eff_dt or sched_dt
    if cancelled or base is None:
        ngo_dt = None
    elif is_dep:
        ngo_dt = base + dt.timedelta(minutes=FLIGHT_MIN_HKG_NGO + TZ_DIFF_MIN)
    else:
        ngo_dt = base - dt.timedelta(minutes=FLIGHT_MIN_NGO_HKG - TZ_DIFF_MIN)
    ngo_txt = _hhmm_rel(ngo_dt, ref, True)

    # 経路表記（名古屋以外の経由地・目的地があれば付加）
    ports = (flight.get("destination") if is_dep else flight.get("origin")) or []
    others = [p for p in ports if not _hit(p)]
    if is_dep:
        route_text = "HKG → NGO" + (f" → {others[-1]}" if others else "")
    else:
        route_text = (f"{others[0]} → " if others else "") + "NGO → HKG"
    DATE_STR = date_str

    # 前回通知との比較（署名 = 予定時刻 + 状態）
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
    return {"name": name[:256], "value": value[:1024], "inline": False}, sig, kind  # Discord の文字数上限で切り詰め


# @function name:   describe
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   flight_key
# @function start
def flight_key(flight: dict) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：前回状態の保存・照合に使う便の一意キーを生成する
    - 形式: '<_type>_<date>_<便名1/便名2/...>'
    ----------------------------------------------------------------------
    Args:
        - flight -> dict --- 便情報
    ----------------------------------------------------------------------
    Returns:
        - key -> str --- 一意キー
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    nos = "/".join(x.get("no", "") for x in flight.get("flight", []))
    return f"{flight.get('_type')}_{flight.get('date')}_{nos}"


# @function name:   flight_key
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   build_fields
# @function start
def build_fields(flights: list[dict], today: dt.date, state: dict, new_state: dict) -> tuple[list[dict], int]:
    """
    ----------------------------------------------------------------------
    関数概要：便ごとの埋め込みフィールド一覧と変化件数を求める
    - new_state に今回の署名を書き込む（副作用）
    ----------------------------------------------------------------------
    Args:
        - flights -> list[dict] --- 便情報リスト
        - today -> dt.date --- 基準日
        - state -> dict --- 前回通知状態（便キー → 署名）
        - new_state -> dict --- 今回の通知状態を書き込む辞書（更新される）
    ----------------------------------------------------------------------
    Returns:
        - (fields, changed) -> tuple[list[dict], int] --- フィールド一覧と変化件数
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
        - revision       v1.01 (2026/11/11) - 便と便の間に 1 行空ける（Changi 通知に準拠）
    ----------------------------------------------------------------------
    """
    fields, changed = [], 0
    for f in flights:
        key = flight_key(f)
        field, sig, kind = describe(f, today, state.get(key))
        new_state[key] = sig
        if kind:
            changed += 1
        fields.append(field)
    # 便と便の間に 1 行空ける（最後の便には付けない）
    for fld in fields[:-1]:
        fld["value"] += "\n\u200b"
    return fields, changed


# @function name:   build_fields
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   strip_new_badges
# @function start
def strip_new_badges(fields: list[dict]) -> list[dict]:
    """
    ----------------------------------------------------------------------
    関数概要：フィールドから 🆕 の印を取り除く
    - 初回実行時（前回状態なし）は全件が新規になるので印を消す
    ----------------------------------------------------------------------
    Args:
        - fields -> list[dict] --- 埋め込みフィールド一覧
    ----------------------------------------------------------------------
    Returns:
        - fields -> list[dict] --- 印を取り除いた新しいフィールド一覧
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    return [{**f, "name": f["name"].replace("🆕 ", ""), "value": f["value"].replace("\n🆕 新しく追加された便", "")} for f in fields]


# @function name:   strip_new_badges
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   fields_to_text
# @function start
def fields_to_text(fields: list[dict]) -> str:
    """
    ----------------------------------------------------------------------
    関数概要：埋め込みフィールド一覧を標準出力用のテキストに変換する
    ----------------------------------------------------------------------
    Args:
        - fields -> list[dict] --- 埋め込みフィールド一覧
    ----------------------------------------------------------------------
    Returns:
        - text -> str --- 表示用テキスト（0 件の場合は '  該当便なし'）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
        - revision       v1.01 (2026/11/11) - 便間の空行（ゼロ幅スペース）を除いて表示
    ----------------------------------------------------------------------
    """
    if not fields:
        return "  該当便なし"
    return "\n".join(f"  {f['name']}\n" + "\n".join("    " + l for l in f["value"].replace("\u200b", "").splitlines()) for f in fields)


# @function name:   fields_to_text
# @function end
# ======================================================================================#


# ======================================================================================#
# Discord 通知
# ======================================================================================#

# ======================================================================================#
# @function name:   _embeds
# @function start
def _embeds(title: str, fields: list[dict], color: int) -> list[dict]:
    """
    ----------------------------------------------------------------------
    関数概要：フィールド一覧を Discord 埋め込みに分割して組み立てる
    - 1 埋め込みあたり 25 フィールド上限のため 24 件ずつ分割
    ----------------------------------------------------------------------
    Args:
        - title -> str --- 埋め込みタイトル
        - fields -> list[dict] --- 埋め込みフィールド一覧
        - color -> int --- 埋め込みの色
    ----------------------------------------------------------------------
    Returns:
        - embeds -> list[dict] --- 埋め込みリスト（0 件の場合は '該当便なし' の 1 件）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if not fields:
        return [{"title": title, "description": "該当便なし", "color": color}]
    return [{"title": title if i == 0 else f"{title} (続き)", "color": color, "fields": fields[i:i + 24]}
            for i in range(0, len(fields), 24)]


# @function name:   _embeds
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   post_discord
# @function start
def post_discord(webhook: str, title: str, period: str, dep_fields: list[dict], arr_fields: list[dict],
                 n_dep: int, n_arr: int, n_changed: int) -> None:
    """
    ----------------------------------------------------------------------
    関数概要：Discord Webhook へ出発便・到着便の埋め込みを送信する
    - 1 メッセージあたり 10 埋め込み上限のため分割送信
    - 429 (Rate Limit) 時は Retry-After 秒待って 1 回再送
    ----------------------------------------------------------------------
    Args:
        - webhook -> str --- Discord Webhook URL
        - title -> str --- メッセージタイトル
        - period -> str --- 対象期間の表示文字列
        - dep_fields -> list[dict] --- 出発便フィールド一覧
        - arr_fields -> list[dict] --- 到着便フィールド一覧
        - n_dep -> int --- 出発便件数
        - n_arr -> int --- 到着便件数
        - n_changed -> int --- 前回からの変化件数
    ----------------------------------------------------------------------
    Returns:
        - なし
    ----------------------------------------------------------------------
    Raises:
        - requests.HTTPError --- 送信に失敗した場合
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
        - revision       v1.01 (2026/11/11) - フッター文言を Changi 通知に準拠
    ----------------------------------------------------------------------
    """
    embeds = _embeds(f"出発便  HKG → NGO  ({n_dep} 件)", dep_fields, _color("DISCORD_COLOR_DEP", 0x3B82F6)) + \
             _embeds(f"到着便  NGO → HKG  ({n_arr} 件)", arr_fields, _color("DISCORD_COLOR_ARR", 0x22C55E))
    embeds[-1]["footer"] = {"text": f"HKT=香港時間 / JST=日本時間 ・ 日付は各空港の現地日付 ・ 推定=標準所要時間からの概算 ・ 🔔=前回通知から変化 ・ 更新 {dt.datetime.now(JST):%m/%d %H:%M} JST"}
    chg = f"  ・ 変化 {n_changed} 件" if n_changed else ""
    for i in range(0, len(embeds), 10):  # 10 embeds/message 上限
        payload = {"embeds": embeds[i:i + 10]}
        if i == 0:
            payload["content"] = f"**{title}**{chg}\n対象期間: {period}"
        r = requests.post(webhook, json=payload, timeout=15)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "2")))
            r = requests.post(webhook, json=payload, timeout=15)
        r.raise_for_status()


# @function name:   post_discord
# @function end
# ======================================================================================#


# ======================================================================================#
# @function name:   load_state
# @function start
def load_state() -> dict:
    """
    ----------------------------------------------------------------------
    関数概要：前回通知状態を JSON ファイルから読み込む
    ----------------------------------------------------------------------
    Args:
        - なし
    ----------------------------------------------------------------------
    Returns:
        - state -> dict --- 前回通知状態（ファイル無し・破損時は空辞書）
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


# @function name:   load_state
# @function end
# ======================================================================================#


# ======================================================================================#
# メイン処理
# ======================================================================================#

# ======================================================================================#
# @function name:   main
# @function start
def main() -> int:
    """
    ----------------------------------------------------------------------
    関数概要：HKG カーゴ便から名古屋発着便を取得して Discord へ通知するメイン処理
    - .env 読み込み・引数解析
    - 出発便・到着便を並列取得
    - 前回状態との比較・標準出力表示
    - Discord 送信（--dry-run / --only-new / --notify-empty で制御）
    - 送信成功時に通知状態を保存
    ----------------------------------------------------------------------
    Args:
        - なし
    ----------------------------------------------------------------------
    Returns:
        - exit_code -> int --- 終了コード
            0: 正常終了（スキップ含む）
            3: Webhook URL 未設定
            4: Discord 送信失敗
    ----------------------------------------------------------------------
    Notes:
        - author         Kidera-Shunsuke
        - revision       v1.00 (2026/11/08) - 新規作成
    ----------------------------------------------------------------------
    """
    # .env 読み込みと所要時間設定の再評価（.env の値を反映）
    global FLIGHT_MIN_HKG_NGO, FLIGHT_MIN_NGO_HKG
    load_dotenv(BASE_DIR / ".env")
    FLIGHT_MIN_HKG_NGO = int(os.environ.get("FLIGHT_MINUTES_HKG_NGO", FLIGHT_MIN_HKG_NGO))
    FLIGHT_MIN_NGO_HKG = int(os.environ.get("FLIGHT_MINUTES_NGO_HKG", FLIGHT_MIN_NGO_HKG))

    # コマンドライン引数
    p = argparse.ArgumentParser(description="HKG カーゴ便から名古屋発着便を Discord へ通知")
    p.add_argument("--date", help="起点日 YYYY-MM-DD（既定: 今日）")
    p.add_argument("--days", type=int, default=3, help="取得する日数（既定 3）")
    p.add_argument("--dry-run", action="store_true", help="Discord へ送信せず表示のみ")
    p.add_argument("--only-new", action="store_true", help="前回通知から変化が無ければ送らない")
    p.add_argument("--notify-empty", action="store_true", help="該当便 0 件でも通知する")
    p.add_argument("--webhook", help="Discord Webhook URL（環境変数より優先）")
    p.add_argument("--reset-state", action="store_true", help="前回状態を無視する（全件を新規扱い）")
    a = p.parse_args()

    # 対象日の決定
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    dates = [(today + dt.timedelta(days=i)).isoformat() for i in range(max(1, a.days))]

    # 出発便・到着便を並列取得
    session = requests.Session()
    session.headers.update(HEADERS)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_dep = ex.submit(fetch_all, session, "dep", dates)
        f_arr = ex.submit(fetch_all, session, "arr", dates)
        dep, arr = f_dep.result(), f_arr.result()
    print(f"取得 {time.perf_counter() - t0:.1f}s")

    # 前回状態との比較・フィールド組み立て
    state = {} if a.reset_state else load_state()
    new_state: dict = {}
    dep_fields, c1 = build_fields(dep, today, state, new_state)
    arr_fields, c2 = build_fields(arr, today, state, new_state)
    n_changed = c1 + c2 if state else 0  # 初回（状態ファイル無し）は変化件数を数えない
    if not state:
        dep_fields, arr_fields = strip_new_badges(dep_fields), strip_new_badges(arr_fields)

    # 標準出力表示
    period = f"{day_label(dates[0], today)} 〜 {day_label(dates[-1], today)}"
    title = f"HKG ⇄ NGO カーゴ便  |  出発 {len(dep)} 件 / 到着 {len(arr)} 件"
    print(title, f"(変化 {n_changed} 件)" if n_changed else "")
    print(f"対象期間: {period}")
    print("[出発便  HKG → NGO]")
    print(fields_to_text(dep_fields))
    print("[到着便  NGO → HKG]")
    print(fields_to_text(arr_fields))

    # 送信スキップ判定
    if a.dry_run:
        return 0
    if not (dep or arr) and not a.notify_empty:
        print("通知対象なしのため送信スキップ（--notify-empty で送信可）")
        return 0
    if a.only_new and state and n_changed == 0:
        print("前回通知から変化なしのためスキップ")
        return 0

    # Discord 送信
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


# @function name:   main
# @function end
# ======================================================================================#


if __name__ == "__main__":
    # 独立実行時の処理
    sys.exit(main())
