# manga-trend: eBay漫画トレンドリサーチエージェント

## プロジェクトの目的
eBayのMangaカテゴリで「価格が上昇しているタイトル」を発見する。
週次でデータを収集・蓄積し、前週比・前月比の価格上昇率ランキングを
スマホで見られる静的HTMLレポートとして GitHub Pages に公開する。

## 全体アーキテクチャ

```
manga-trend/
├── CLAUDE.md            # このファイル
├── .env                 # eBay APIキー(gitignore対象)
├── .env.example         # キー名のテンプレート
├── data/
│   └── trend.db         # SQLite(リポジトリにコミットする)
├── scripts/
│   ├── collect.py       # ①収集係
│   ├── analyze.py       # ②分析係
│   └── report.py        # ③レポート係(HTML生成)
├── docs/
│   └── index.html       # GitHub Pages公開ページ(生成物)
├── .github/workflows/
│   └── weekly.yml       # 週次自動実行
└── requirements.txt
```

役割は厳密に分離すること:
- collect.py は eBay API にのみ触れ、DBに書くだけ。分析しない。
- analyze.py は DB のみ読み、`data/analysis_latest.json` を出力。APIに触らない。
- report.py は analysis JSON のみ読み、`docs/index.html` を生成。

## ① collect.py(収集係)

- eBay Browse API を使用(環境変数 `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`、
  OAuth client credentials フローでトークン取得)
- 対象: Comics & Graphic Novels 配下の Manga(カテゴリIDは実装時にAPIで確認)
- 処理:
  1. カテゴリを人気順で検索し、上位30タイトルを取得
  2. タイトル正規化: シリーズ名+巻数/セット+言語 で `title_key` を生成
     (例: `one_piece_vol1_en`, `chainsaw_man_set1-11_en`)。
     正規化ロジックは独立した関数にし、ユニットテストを書く
  3. **過去に一度でも上位30に入った全タイトル**(titlesテーブル全件)についても
     毎回価格を取得する(ランク圏外になっても追跡を継続するため)
  4. 各タイトルの 中央値価格 / 最安値 / 出品数 / 今週のランク(圏外はNULL)を
     snapshots に1行ずつ記録
- 外れ値対策: 価格は平均でなく中央値を使う。出品1件のみのタイトルはフラグを立てる
- APIレート制限を考慮し、リクエスト間に適切なsleepを入れる
- 冪等性: 同じ日に再実行しても重複行ができないよう UPSERT する

## ② analyze.py(分析係)

- 入力: data/trend.db のみ
- 出力: data/analysis_latest.json
- 計算内容:
  - 前週比上昇率: 直近スナップショット vs 約7日前(±2日で最近傍を採用)
  - 前月比上昇率: 直近 vs 約30日前(±5日で最近傍)
  - 新規ランクイン: 今週初めて上位30に入った title_key のリスト
  - 各タイトルの価格推移履歴(グラフ用に全スナップショット)
- 比較対象が存在しないタイトルは該当ランキングから除外(エラーにしない)
- 出品数が極端に少ないもの(2件未満)はランキング下部に分離するか注記

## ③ report.py(レポート係)

- 入力: data/analysis_latest.json
- 出力: docs/index.html(単一ファイル、外部依存はCDNのChart.jsのみ)
- **スマホファースト**で設計:
  - viewport設定、1カラム、カード型リスト
  - タブ: 「前週比」「前月比」「NEW」の3つ
  - 各カード: タイトル名 / 現在価格 / 上昇率(%と金額) / 出品数
  - カードをタップすると展開し、価格推移の折れ線グラフ(Chart.js)を表示
  - データはJSONとしてHTML内に埋め込む(fetch不要、サーバー不要)
  - 生成日時をヘッダに表示
- ダークテーマ推奨、日本語UI

## DBスキーマ(SQLite: data/trend.db)

```sql
CREATE TABLE IF NOT EXISTS titles (
  title_key    TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  first_seen   TEXT NOT NULL          -- ISO日付
);

CREATE TABLE IF NOT EXISTS snapshots (
  title_key        TEXT NOT NULL REFERENCES titles(title_key),
  snap_date        TEXT NOT NULL,     -- ISO日付
  median_price     REAL,
  min_price        REAL,
  listing_count    INTEGER,
  rank_in_category INTEGER,           -- 圏外はNULL
  PRIMARY KEY (title_key, snap_date)
);
```

## GitHub Actions(.github/workflows/weekly.yml)

- スケジュール: 毎週月曜 00:00 UTC(cron)+ 手動実行(workflow_dispatch)
- ステップ: checkout → Python setup → pip install →
  collect.py → analyze.py → report.py →
  data/trend.db と docs/index.html を commit & push
- eBay APIキーは GitHub Secrets(`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`)から
  環境変数として渡す
- GitHub Pages は docs/ フォルダ公開の設定を使う(README にセットアップ手順を書く)

## /trend コマンド(Claude Codeカスタムコマンド)

`.claude/commands/trend.md` を作成し、以下の手順を定義する:
1. analyze.py を実行(必要なら collect.py も)
2. 上昇率トップのタイトルについて、Webで急騰理由を調査
   (アニメ化発表・実写化・作者関連ニュース・完結・絶版など)
3. 判明した理由を analysis JSON の `notes` フィールドに追記し、
   report.py を再実行してHTMLに反映
4. 結果サマリをチャットで報告

## 実装の進め方(この順で)

1. スキャフォールド: ディレクトリ、requirements.txt、.env.example、DBスキーマ作成
2. collect.py — まず**モックレスポンス**でDB書き込みまでテスト。
   タイトル正規化のユニットテストを書く
3. analyze.py — テスト用に日付をずらしたダミースナップショットを投入して検証
4. report.py — ダミーデータでHTMLを生成し、スマホ幅(375px)での表示を確認
5. eBay API 実接続(ユーザーからAPIキーを受け取ってから)
6. GitHub Actions と Pages 設定、README整備
7. /trend カスタムコマンド作成

## 制約・注意

- eBay公開APIでは過去の落札価格履歴は取れない。時系列は自前蓄積が前提。
  蓄積開始から1週間後に前週比、1ヶ月後に前月比が有効になる旨をHTMLに注記する
- Marketplace Insights API(落札データ)は承認制。承認が取れたら
  collect.py に sold_count / sold_price を追加する拡張余地を残しておく
- スクレイピングは行わない(規約リスク)。公式APIのみ使用
- data/trend.db はコミットする(Actionsだけで完結させるため)。
  .env は必ず .gitignore に入れる
- コードにはコメントを日本語で書く
