# BoatLab AI

「BoatLab AI ― AI分析 競艇情報 ｜ by AI Axis Lab」のサイトと、毎朝の予想PDFを自動で作る仕組みです。

- `https://aiaxislab.win/` … 運営元 AI Axis Lab のサイト（運営者情報・利用規約・プライバシーポリシー・お問い合わせ）
- `https://boat.aiaxislab.win/` … BoatLab AI（本日の予想PDF・過去30日）

## 毎日の動き

1. 毎朝5:30（日本時間）に GitHub Actions の「毎朝のPDF作成」が動きます。
2. 前日までの競走成績を取り込み、これまでの予想の答え合わせをして、管理者ダッシュボード用のCSVに1レースずつ記録します。
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

## 管理者ダッシュボード（自分だけが見るページ）

的中率・回収率は一般公開せず、`https://aiaxislab.win/<ADMIN_PATH>/` の管理者ダッシュボードだけで見ます。

- 全体・会場別・レース別（1R〜12R）・曜日別・月別・日別・会場×レース・各レース一覧。期間・会場・レース・曜日で絞り込み
- レースごとの成績は R2 の `admin/races.csv` に毎朝追加。ダッシュボードの「クリア」を押すまで消えない（クリアした日より前のレースは二度と足さない）。いつでもCSVでダウンロード可
- 容量ゲージ（サイト全体・CSV・PDF。R2 無料枠10GBに対する割合）
- 予想的中確率別（10%台〜）の実際の的中率・回収率。確率の高いレースほど本当に当たるかを確かめる
- 毎朝のメール：買い目の的中確率が基準（初期値50%。ダッシュボードで20〜80%から選べる。R2 の `admin/settings.json`）を超えたレースだけを、買い方つきで送る（`worker/mail.js`。Worker の Cron が 6:00〜8:30 に30分おきに見て、その日の予想ができていれば1回だけ送る）

守り方：
1. Cloudflare Access（Zero Trust の無料プラン）で、そのURLを自分のメールアドレスだけがログインできるようにする
2. Worker でも Access の証明書（JWT）を確かめ、無い・違うときは普通の「ページが見つかりません」を返す
3. URLの文字列（ADMIN_PATH）はリポジトリに書かず GitHub Secrets に置く。公開ページ・robots・サイトマップのどこからもリンクしない

GitHub Secrets に登録するもの（ADMIN_PATH と ACCESS_TEAM がそろうまでダッシュボードは表示されません）：

| 名前 | 中身 |
|---|---|
| `ADMIN_PATH` | ダッシュボードのURLの文字列（16文字以上のでたらめな英数字） |
| `ACCESS_TEAM` | Zero Trust のチーム名（`xxxx.cloudflareaccess.com` の `xxxx`） |
| `ACCESS_AUD` | 任意。Access アプリケーションの「Application Audience (AUD) Tag」（一覧の「…」→ Copy as JSON の `aud`）。Access アプリを2つ以上作るときは必ず登録 |
| `MAIL_TO` | 毎朝のメールの送り先。カンマ区切りで複数可（空白なし）。Cloudflare の Email Routing →「Destination addresses」で確認済みのアドレスだけに届く |

登録・変更したら Actions →「サイトを公開」→ Run workflow で反映します。

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
| `worker/admin.js`, `worker/admin.html` | 管理者ダッシュボード |
| `boatlab/ledger.py` | ダッシュボード用CSVへの追記 |
| `boatlab/ml.py`, `boatlab/train.py` | AI学習モデル（特徴量・学習・検証） |
| `.github/workflows/` | 毎朝の自動実行とサイト公開 |

## 予想の計算について

各艇の強さを点数にし、順位の確率モデル（プラケット＝ルース）で3連単120通りの確率を出しています。点数の付け方は2種類あります。

- **AI学習モデル**（`boatlab/ml.py`, `boatlab/train.py`）：過去3年分のレース結果から、点数の重みを機械学習で求めます。材料は全国・当地の勝率と2連率、モーター・ボート2連率、級別、年齢、体重、今節の平均スタート、場ごとのコースの有利さ、風の強さ・向きと波でのコースの有利さの変わり方、潮（上げ潮・下げ潮と潮位）。学習ではレースの実際の風・波を使い、毎朝の予想では気象庁予報の風（文章からの目安）と前日の波を使います。
- **簡易モデル**（`boatlab/model.py`）：手で決めた重み。AI学習モデルが無い・不採用のときに使います。

### AIモデルの学習（毎週月曜 3:00）

GitHub Actions の「AIモデルの学習」が週1回動きます（Actions → Run workflow で手動でも可。初回は過去3年分のデータを取りに行くので1〜2時間かかります）。

1. 月ごとの学習データ（1レース1行）を作って R2 の `admin/ml/races/` に置く。2回目からは足りない月だけ作る
2. 直近60日を検証用に取っておき、それより前で学習して、簡易モデルと当たり具合を比べる
3. 「実際の3連単に付けていた確率」が簡易モデルより高ければ採用し、全期間で学び直したモデルを R2 の `admin/ml/model.json` に置く
4. 毎朝のPDF作成は、採用されたモデルがあればそれを使う。どちらのモデルで予想したかはCSVの「モデル」列に残り、ダッシュボードの「予想モデル別」で比べられる

ダッシュボードの「AI学習モデル」で、採用中かどうか、検証期間での比較、確率が当てになるか（確率帯ごとの実際の的中率）、学習した重みを見られます。
