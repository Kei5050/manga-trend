---
description: 価格上昇タイトルの急騰理由を調査し、レポートに反映する
---

以下の手順を実行してください。

1. `python scripts/analyze.py` を実行する(データが古い場合は先に `python scripts/collect.py` も実行する)。
2. `data/analysis_latest.json` の `weekly_ranking` および `monthly_ranking` から上昇率トップのタイトルを確認する。
3. 各タイトルについてWebで急騰理由を調査する。確認する観点:
   - アニメ化発表
   - 実写化
   - 作者関連ニュース(訃報・新作発表など)
   - 完結・絶版
   - その他話題化した出来事
4. 判明した理由を `data/analysis_latest.json` の各タイトルエントリに `notes` フィールドとして追記する(理由が不明な場合は追記しない)。
5. `python scripts/report.py` を再実行し、`docs/index.html` に反映する。
6. 調査結果のサマリ(タイトル・上昇率・判明した理由)をチャットで報告する。
