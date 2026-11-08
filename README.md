# HKG ⇄ 名古屋 カーゴ便 Discord 通知

香港国際空港（HKG）の貨物便 API から、名古屋（NGO）発着の便を今日〜明後日の 3 日分抽出して
Discord へ通知します。表示は Changi / 仁川 の通知と共通の表形式です。

## セットアップ
```bat
pip install requests
copy .env.example .env     （DISCORD_WEBHOOK_URL を記入。Webhook URL はコードに書かない）
python hkg_nagoya_flights.py --dry-run
python hkg_nagoya_flights.py
```

## 表示
```
▌出発便  HKG → NGO  (2 件)                       ← 青
▌ 🔔 🛫 LD 216
▌ HKG → NGO → ANC                 HKG → NGO
▌ `🛫 HKG 04:35 HKT`              `🛫 HKG 07:35 HKT`
▌ `🛬 NGO 09:17 JST (推定)`       `🛬 NGO 12:15 JST (推定)`
▌ 出発済 04:37 ←定刻              定刻
▌
▌到着便  NGO → HKG  (1 件)                       ← 緑
▌ 🛬 KZ 9
▌ NGO → HKG
▌ `🛫 NGO 19:00 JST (推定)`
▌ `🛬 HKG 22:10 HKT`
▌ 定刻
```
日ごとに `━━ 今日 11/08 (日) ━━` の見出し枠を入れ、1 便 = 1 枠を縦に並べます（横並びなし）。
各枠: 見出し = 便名（🔔 = 前回から変化 / 🆕 = 新規）、1 行目 = 経路、2〜3 行目 = 出発/到着の日付・時刻（各空港の現地日時、NGO は日本時間）、4 行目 = 状態。
- 列: 出発地 出発時刻 | 便名 | 到着地 到着時刻 | 状態（HKG は香港時間、NGO は日本時間）
- 状態: `定刻` / `出発済 HH:MM` / `到着済 HH:MM`（実績）/ `変更 HH:MM`（変更後予定）/ `遅延` / `欠航`
- `推定` =（HKG→NGO 220 分 / NGO→HKG 250 分 ＋ 時差 1h。`.env` で変更可）
- **前回通知からの変化**: 見出し 🔔 = 実績・予定・状態が変わった便（状態欄に `←前回値`）、🆕 = 新しく現れた便
  - 前回状態は `notified_state.json`（送信成功時のみ更新）
  - `--only-new`: 変化が 0 件なら送信しない（1 時間ごとの定期実行向け）
  - `--reset-state`: 全件を新規扱いにする
- 色は `.env` の `DISCORD_COLOR_DEP` / `DISCORD_COLOR_ARR` で変更可

旧版は `hkg_nagoya_flights.py.bak_20261108`（Webhook URL が直書きされているので、不要なら削除してください）。
