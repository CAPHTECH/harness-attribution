# hattr — Harness Attribution tool

`poc/` で実証した手法を、config 駆動で任意の (被検体 × 要因 × 課題 × イベント) に適用できる再利用ツールにしたもの。設計の詳細は [`DESIGN.md`](./DESIGN.md)。

## 使い方

```bash
# 研究を実行（生成→採点→分析→レポート）
python -m hattr.cli run hattr/examples/codegen.study.yaml [--limit N] [--mock]

# 簡易検査（3条件・R=1・bootstrapなし。結論ではなくトリアージ）
python -m hattr.cli run hattr/examples/codegen.study.yaml --screen [--limit N] [--mock]

# 事前登録（config を凍結ハッシュ化。timestamp は明示引数）
python -m hattr.cli preregister hattr/examples/codegen.study.yaml --timestamp 2026-06-05T00:00:00Z

# envelope/paraphrase を生成器モデルで自動生成し study に書き戻す（単一文字列 variant のみ。
# 生成→人間レビュー→preregister の流れ。既存値は上書きしない）
python -m hattr.cli autogen STUDY.yaml --timestamp 2026-06-05T00:00:00Z [--generator-model M]
```

出力は `results_dir`（既定 `hattr_runs/<name>/`、git無視）に `report.md` / `scored.csv` / `run_meta.json` / `raw/`。依存は標準ライブラリ + numpy + pyyaml のみ。

## 構成可能な要素

| 種別 | 選択肢 |
| --- | --- |
| **被検体（SubjectAdapter）** | `codex`（codex exec, cloud／`--oss` ローカル）, `openai_compat`（OpenAI互換HTTP＝ollama/OpenAI/任意エンドポイント） |
| **採点（Scorer）** | `execution`（隔離subprocessで隠しテスト実行＝決定的, scorer-bias 無し。`sandbox: seatbelt`(既定)で macOS Seatbelt によりネットワーク遮断・workdir 外書込遮断・resource 制限。`sandbox-exec` 不在なら明確エラー）, `llm_judge`（別モデルで盲検判定＋決定的クロスチェック）, `regex` |
| **条件** | 固定6種 H1/H0_ablate/H0_neutral/H_base_len(包絡線)/H_para/H_contra を `base_prompt`＋`variants` で定義 |
| **分析** | primary/secondary イベント, polarity(minimize/maximize), baselines, 課題クラスタ bootstrap CI, verdict(meaning_attributable/surface_confound/fragile/inconclusive), tradeoff_flag。`--screen` 時は factual/ablate/envelope の3条件だけを R=1・bootstrapなしで実行し、verdict ではなく triage(no_headroom/candidate_signal/surface_only/inconclusive) を出す |

## ガードレール（規律を既定化＝本ツールの中核価値）

実行時に自動チェックして report 冒頭・stderr に出す。`poc/` の研究で律速になった失敗モードを既定で警告する:

- **judge==subject**: 判定器が被検体と同一モデル → scorer-bias の恐れ（実証済み）
- **envelope 欠如**: 包絡線ベースライン未設定 → 長さ効果を分離できない
- **headroom**: 全条件で primary がほぼ同値（floor/ceiling）→ 識別力なし、課題難度/被検体を見直せ
- **secondary 欠如**（llm_judge）: 副作用（tradeoff）を見逃す恐れ
- **error 率**: 暗黙の打ち切りを防ぐ

## 例

- `examples/codegen.study.yaml` — コード生成 × 詳細仕様、execution スコアラー（`poc/codegen` を再現）
- `examples/safety.study.yaml` — system prompt × 安全性、llm_judge スコアラー（`poc` 安全性実験を再現）

背景と知見は [`../poc/FINDINGS.md`](../poc/FINDINGS.md) / [`../poc/PREREGISTRATION.md`](../poc/PREREGISTRATION.md) / [`../poc/codegen/PREREGISTRATION.md`](../poc/codegen/PREREGISTRATION.md)。
