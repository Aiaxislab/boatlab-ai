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

競技ごとに1ページ（いまは競艇だけ。上のタブは将来オートレース・FXなどのダッシュボードを切り替えるため）。上から順に:

- 確率帯ごとの成績：全レースを、朝の予想の「買い目の的中確率」で10%刻みの帯（10%未満〜90%以上）に分け、3連単6点と3連複3点を別々に、件数・的中率・使った金額・戻った金額・収支・回収率を出す。期間は「最新の1日」（結果は翌朝まとめて取り込むので前日分）と「過去1か月」の切り替え。的中率は「レース数で割る」（初期）と「点数で割る」の切り替え
- 設定：毎朝のメールと今日の買い目の、的中確率の範囲（〜%以上〜%未満を10%刻みで。例：10%以上〜20%未満で10%台だけ）と、1日の上限金額（手入力、6桁まで、初期値3,000円）。R2 の `admin/settings.json` の `mailMin`・`mailMax`・`buyMin`・`buyMax`・`buyLimit`
- 毎朝のメール：範囲内のレースだけを、買い方つきで送る（`worker/mail.js`。Worker の Cron が 6:00〜8:30 に30分おきに見て、その日の予想ができていれば1回だけ送る）
- 今日の買い目（購入用）：範囲内のレースを締切の早い順に、1日の上限金額に届くまで選んで表示（`worker/buy.js`）。購入はテレボートで本人が行う。ログイン情報はどこにも保存しない
- そのほか（たたんで表示）：内訳（会場別・レース別・曜日別・月別・日別・会場×レース・予想モデル別・各レース一覧。期間・会場・レース・曜日で絞り込み）、AI学習モデル、サイトの守り、容量ゲージ、CSV
- レースごとの成績は R2 の `admin/races.csv` に毎朝追加。ダッシュボードの「クリア」を押すまで消えない（クリアした日より前のレースは二度と足さない）。いつでもCSVでダウンロード可
- サイトの守り（一時停止）：今の状態、今月の R2 の使用量、一時停止・再開のボタン（下の「サイトの守り」）

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
| `CF_ANALYTICS_TOKEN` | 使用量による自動一時停止に使う。Cloudflare の「My Profile → API Tokens → Create Token → Custom token」で、権限を「Account / Account Analytics / Read」だけにして作ったトークン |

登録・変更したら Actions →「サイトを公開」→ Run workflow で反映します。

### サイトの守り（`worker/guard.js`）

従量課金になる前に止める・嫌がらせ対策。ふつうのお客様は止めないよう、上限は普段の使われ方の数百倍に置いています。

1. **一時保存（キャッシュ）**：PDF（1日）と公開データ（5分）は Cloudflare の一時保存から返す。同じPDFが何度開かれても R2 の読み出しは増えない（URLの `?` 以降は無視）
2. **アクセス回数の制限**：PDF・公開データは1つのIPあたり60秒で120回まで（超えたら60秒お断り）。携帯回線は大勢で1つのIPを共有するので多めにしてある。変えるときは `wrangler.toml` と `guard.js` の `RATE` を両方直す
3. **自動一時停止**：30分ごとに Cloudflare の使用量を見て、今月の R2 読み出しが700万回（無料枠1,000万回の70%）、書き込みが70万回（同100万回の70%）、または直近30分の読み出しが20万回を超えたら、サイト全体を「一時停止中」のページにする。管理者ダッシュボードは止まらない。再開はダッシュボードのボタンだけ。使用量が読めないときは止めない

Workers の無料プランは1日10万リクエストで頭打ち（超えた分はエラーになるだけで請求されない）なので、お金がかかりうるのは主に R2 です。

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
| `worker/buy.js` | 今日買う舟券の選び方（確率の範囲・1日の上限金額） |
| `worker/guard.js` | サイトの守り（一時保存・アクセス回数の制限・自動一時停止） |
| `boatlab/ledger.py` | ダッシュボード用CSVへの追記 |
| `boatlab/ml.py`, `boatlab/train.py` | AI学習モデル（特徴量・学習・検証） |
| `fx/`, `fx/knowledge/` | FXの判定（データ取得・指標計算・ルール表・採点）。競艇とは別 |
| `worker/fx/` | FXのダッシュボード・メール・Claude用の要約URL |
| `.github/workflows/` | 毎朝の自動実行とサイト公開（FXは `fx.yml`） |

## 予想の計算について

各艇の強さを点数にし、順位の確率モデル（プラケット＝ルース）で3連単120通りの確率を出しています。点数の付け方は2種類あります。

- **AI学習モデル**（`boatlab/ml.py`, `boatlab/train.py`）：過去3年分のレース結果から、点数の重みを機械学習で求めます。材料は全国・当地の勝率と2連率、モーター・ボート2連率、級別、年齢、体重、今節の平均スタート、場ごとのコースの有利さ、風の強さ・向きと波でのコースの有利さの変わり方、潮（上げ潮・下げ潮と潮位）。学習ではレースの実際の風・波を使い、毎朝の予想では気象庁予報の風（文章からの目安）と前日の波を使います。
- **簡易モデル**（`boatlab/model.py`）：手で決めた重み。AI学習モデルが無い・不採用のときに使います。

### AIモデルの学習（毎週月曜 3:00）

GitHub Actions の「AIモデルの学習」が週1回動きます（Actions → Run workflow で手動でも可）。学習データ作りは1日分20〜30秒かかるので、1回の実行では3時間（約15か月分）までにして新しい月から作ります。3年分そろえるには、初回に Run workflow を3回（前の回が終わってから）動かしてください。

1. 月ごとの学習データ（1レース1行）を作って R2 の `admin/ml/races/` に置く。2回目からは足りない月だけ作る
2. 直近60日を検証用に取っておき、それより前で学習して、簡易モデルと当たり具合を比べる
3. 「実際の3連単に付けていた確率」が簡易モデルより高ければ採用し、全期間で学び直したモデルを R2 の `admin/ml/model.json` に置く
4. 毎朝のPDF作成は、採用されたモデルがあればそれを使う。どちらのモデルで予想したかはCSVの「モデル」列に残り、ダッシュボードの「予想モデル別」で比べられる

ダッシュボードの「AI学習モデル」で、採用中かどうか、検証期間での比較、確率が当てになるか（確率帯ごとの実際の的中率）、学習した重みを見られます。

## FX（自分用のFXアドバイザー）

管理者ダッシュボードの「FX」タブ（`https://aiaxislab.win/<ADMIN_PATH>/fx/`）。競艇とはデータもコードも分けています。

### 動き

1. 毎朝8:15（日本時間）に GitHub Actions の「FXの判定」が起動し、8:30 まで待ってから12銘柄（主要7ペア・EURJPY・GBPJPY・AUDJPY・XAUUSD・BTCUSD）を判定します。
2. 1分足〜日足の7つの時間足で指標を計算し、`fx/knowledge/interpretation_table.json` のルール表に当てて説明文を集め、`composite_rules.json` の決まりで「方向（▲▲〜▼▼）・確信度・時間で流れが変わるリスク・マジョリティリバース（◯/△/×と％）」を出します。AIは使いません。
3. 判定は R2 の `admin/fx/judge/日付.json` に1日1回だけ置き、後から書き換えません。Worker が 8:00〜10:55 に5分おきに見て、できていればメールを1回送ります。
4. 翌朝のNYクローズ（BTCは24時間後）を過ぎたら答え合わせをして `admin/fx/scores.csv` に足し、的中率を `admin/fx/stats.json` にまとめます。「前日と同じ方向」「日足20EMAの向き」という単純な予想とも比べます。
5. 日中は10:00〜24:00に2時間おきに途中経過を作ります（ダッシュボード用。採点はしません）。

使うデータ：Twelve Data（為替・金、無料枠1日800回）、Coinbase の公開データ（BTC）、Myfxbook のコミュニティアウトルック（大衆の比率、無料枠1日100回）、Forex Factory の指標カレンダー。どれも自分だけが見る前提の使い方です（ダッシュボードはログイン必須、要約URLも人に教えない）。

### Claude に読ませる

GitHub Secrets に `FX_SHARE_PATH` を登録すると、`https://aiaxislab.win/<FX_SHARE_PATH>` に判定と答え合わせの要約（テキスト）が出ます。ログイン不要・検索には載りません。このURLを Claude のプロジェクトに貼って「分析して」と頼めば、API を使わずに読んで解説できます。`/scores.csv`・`/stats.json` も同じURLの下にあります。取得した足データそのものは載せません。

### GitHub Secrets に登録するもの（FX）

| 名前 | 中身 |
|---|---|
| `TWELVEDATA_API_KEY` | Twelve Data の APIキー（無料の Basic プラン）。無いと為替と金は判定できない |
| `MYFXBOOK_EMAIL`, `MYFXBOOK_PASSWORD` | Myfxbook のログイン情報（大衆の比率）。無くても判定はするが、マジョリティリバースは「－」になる |
| `FX_SHARE_PATH` | Claude に読ませる要約のURLの文字列（16文字以上のでたらめな英数字。ADMIN_PATH とは別にする） |
| `FX_MAIL_TO` | 任意。FXのメールの送り先（カンマ区切り）。無ければ `MAIL_TO` に送る。Email Routing で確認済みのアドレスだけに届く |
| `GH_DISPATCH_TOKEN` | 任意。GitHub の fine-grained トークン（このリポジトリの Actions を Read and write だけ）。あると 8:20 に Worker から判定を起動して時刻が正確になり、ダッシュボードの「最新のデータで判定しなおす」も使える |

登録・変更したら Actions →「サイトを公開」→ Run workflow で Worker に反映します。初回は Actions →「FXの判定」→ Run workflow（mode: intraday）で動作を確かめてください。

### 判定の中身を変えるとき

説明文や点数は `fx/knowledge/` の JSON を直すだけで変わります（`interpretation_table.json` の `text`・`bias`、`composite_rules.json` の閾値）。PR を出すと `python -m fx.selftest`（作り物のデータで最後まで動くかの確認）が動きます。
