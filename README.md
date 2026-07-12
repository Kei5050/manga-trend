# manga-trend

eBayのMangaカテゴリで価格が上昇しているタイトルを発見し、週次レポートをGitHub Pagesで公開する。

## セットアップ

1. eBay Developer Programでアプリケーションを作成し、Client ID / Client Secretを取得する。
2. `.env.example` を `.env` にコピーし、キーを設定する。
3. 依存関係をインストールする。

   ```bash
   pip install -r requirements.txt
   ```

4. 手動実行する場合:

   ```bash
   python scripts/collect.py
   python scripts/analyze.py
   python scripts/report.py
   ```

## GitHub Actions / Pages setup

1. リポジトリの Settings > Secrets and variables > Actions で以下を登録する:
   - `EBAY_CLIENT_ID`
   - `EBAY_CLIENT_SECRET`
2. Settings > Pages で Source を「Deploy from a branch」、Branch を `main` / `docs` に設定する。
3. `.github/workflows/weekly.yml` が毎週月曜0:00 UTCに自動実行される(手動実行も `workflow_dispatch` から可能)。

## テスト

```bash
python -m unittest discover -s scripts -p "test_*.py"
```

## `/trend` コマンド

Claude Code上で `/trend` を実行すると、価格上昇タイトルの急騰理由(アニメ化・実写化など)をWeb調査し、レポートに反映する。
