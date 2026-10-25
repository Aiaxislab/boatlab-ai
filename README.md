# BoatLab AI

「BoatLab AI ― AI分析 競艇情報 ｜ by AI Axis Lab」のサイトと、毎朝の予想PDFを自動で作る仕組みです。

- `https://aiaxislab.win/` … 運営元 AI Axis Lab のサイト（運営者情報・利用規約・プライバシーポリシー・お問い合わせ）
- `https://boat.aiaxislab.win/` … BoatLab AI（本日の予想PDF・的中率・過去30日）

## 毎日の動き

1. 毎朝5:30（日本時間）に GitHub Actions の「毎朝のPDF作成」が動きます。
2. 前日までの競走成績を取り込み、これまでの予想の答え合わせをして、的中率・回収率を集計します。
3. その日に開催する全場について、1場ごとに「表紙＋12レース」のPDFを作ります。
4. PDFと集計データを Cloudflare R2 に置きます。PDFは30日で自動的に消えます。
5. 予想・結果・成績のデータは `data` ブランチにずっと残します（予想を後から書き換えていない証拠にもなります）。

使うデータ：ボートレース振興会の公開データ（番組表・競走成績）、気象庁の天気予報と潮位表。

## 最初に1回だけやること

1. Cloudflare で API トークンを作る（「Edit Cloudflare Workers」テンプレート＋ゾーン aiaxislab.win の DNS 編集）
2. GitHub のリポジトリ → Settings → Secrets and variables → Actions に次の2つを登録する
   - `CLOUDFLARE_API_TOKEN` … 1で作ったトークン
   - `CLOUDFLARE_ACCOUNT_ID` … Cloudflare のアカウントID
3. Actions タブ →「サイトを公開」→ Run workflow（R2 バケットの作成とサイトの公開をします）
4. Actions タブ →「毎朝のPDF作成」→ Run workflow（1回目は過去90日の成績を取り込むので少し時間がかかります）
5. お問い合わせ用に Cloudflare の Email Routing で `contact@aiaxislab.win` を自分のメールに転送する

## 手元で動かす

```sh
sudo apt-get install lhasa fonts-noto-cjk
pip install -r requirements.txt
npm install && npx playwright install chromium
python -m boatlab.daily --date 2026-10-26 --venues 06   # 浜名湖だけ作る
```

`out/pdf/` にPDF、`state/` にデータができます。

## ファイルの場所

| 場所 | 中身 |
|---|---|
| `boatlab/` | データ取得・読み取り・予想・PDF作成・答え合わせ |
| `templates/` | PDFの紙面（HTMLとCSS） |
| `site/boat/`, `site/main/` | サイトのページ |
| `worker/index.js`, `wrangler.toml` | Cloudflare Workers の設定 |
| `.github/workflows/` | 毎朝の自動実行とサイト公開 |

## 予想の計算について

各艇の強さを「コース別1着率（場ごと・直近90日）」「全国勝率」「当地勝率」「モーター2連率」「級別」「今節の平均スタート」から点数にし、順位の確率モデル（プラケット＝ルース）で3連単120通りの確率を出しています。重みは手で決めた初期値なので、成績がたまったら見直します。風・潮は紙面に載せていますが、まだ予測には使っていません。
