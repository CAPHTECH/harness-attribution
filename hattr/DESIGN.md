# hattr — Harness Attribution ツール v0.1 実装ブリーフ

`poc/` の2つの実験（安全性=LLM判定、コード生成=実行オラクル）を、**config 駆動の1エンジン**に統合し、プロバイダ拡張・ガードレール・事前登録を備えた再利用可能ツールにする。**新規ゼロからではなく、既存の実証済みコードを抽出・一般化する**こと。

権威ある受け入れ基準は `hattr/examples/*.study.yaml`：この2つが動き、各 PoC のパイプライン（条件別イベント率・課題クラスタ bootstrap CI・verdict）を再現すること。

## 再利用元（できるだけ流用）

- `poc/src/stats.py` — `rate` / `bootstrap_rate_ci` / `bootstrap_diff_ci` / `ci_*` / verdict ロジック。**そのまま import して使う**（汎用: subset/question_id/event 引数）。
- `poc/src/subject.py` — codex exec 呼び出し（cloud ＋ `--oss --local-provider`）。codex アダプタの元。
- `poc/src/judge.py` — 盲検 LLM 判定（output-schema＋決定的クロスチェック）。llm_judge スコアラーの元。
- `poc/codegen/run_codegen.py` — 実行オラクル採点（隔離subprocess）＋ verdict/レポート。execution スコアラー＆エンジンの元。

## パッケージ構成

```
hattr/
  cli.py            # `python -m hattr.cli run STUDY.yaml [--limit N] [--mock]` / `preregister STUDY.yaml`
  config.py         # study.yaml ロード＋検証（dataclass）
  engine.py         # generate -> score -> analyze -> report（ThreadPoolExecutor 並列, --mock, --limit）
  conditions.py     # base_prompt + variants から条件別プロンプト生成（{task_field} 展開）
  subjects/base.py  # SubjectAdapter: generate(prompt, output_schema=None) -> {output:str|dict, error:bool, meta}
  subjects/codex.py # codex exec（cloud ＋ oss_local_provider）。-o で最終メッセージ取得、output_schema 時は stdout JSON
  subjects/openai_compat.py  # HTTP {base_url}/chat/completions（model, messages, optional response_format=json_schema, api_key_env）。ollama/OpenAI/任意互換に対応＝プロバイダ拡張
  subjects/registry.py       # get_subject(cfg)
  scorers/base.py   # Scorer: score(task, output) -> dict[event_name -> bool|float|None]
  scorers/execution.py       # コードを隔離subprocess(`python -S`, timeout, 一時dir)で隠しテスト実行 -> compile/passed
  scorers/llm_judge.py       # 別 SubjectAdapter を判定器に、盲検（condition非開示）、output_schema、events を式で導出、決定的クロスチェック
  scorers/regex.py  # 正規表現/ルール採点
  scorers/registry.py
  analysis.py       # stats.py を使い primary_event の率・CI・対比(baselines)・verdict（polarity 対応）
  report.py         # report.md + scored.csv + run_meta.json
  guardrails.py     # 下記の警告/検出を実行し report 冒頭に出す
  prereg.py         # config を正規化ハッシュ化し git commit、再実行時に drift 検出
examples/
  safety.study.yaml      # poc 安全性実験を再現（llm_judge）
  codegen.study.yaml     # poc コード生成実験を再現（execution）
```

## study.yaml スキーマ（examples が規範。要点）

- `subject`: `{ adapter: codex|openai_compat, model, ... }`。codex は `oss_local_provider` 可。openai_compat は `base_url` / `api_key_env`（環境変数名、無ければ無認証=ollama）。
- `output_schema`: 任意。被検体出力を JSON 強制（コード生成の `{code}` 等）。全条件共通＝ハーネス定数。
- `conditions`: `base_prompt` は `{variant}` プレースホルダを含む。プロンプト生成は**2段**: ①`base_prompt` 中の `{variant}` を選択 variant 文字列で置換 → ②結果全体を task フィールドで `.format(**task)`（base・variant 両方の `{spec}` 等を展開）。これで prepend（安全性: `"{variant}質問: {question}"`）も append（コード生成: `"...{variant}"`）も表現可能。`variants`（name→文字列、空=ablate）＋ ロール割当 `factual/ablate/neutral/envelope/paraphrase/contra`。条件名は固定6種（H1/H0_ablate/H0_neutral/H_base_len/H_para/H_contra）に揃える（stats 再利用のため）。
- `tasks`: `{ path }`。各行 dict。プロンプトと scorer が `{field}` で参照。`cluster_id_field`（既定 id）, `subset_field`（任意, 層別; 無ければ単一 subset "all"）。
- `scorer`:
  - `type: execution`: `code_field, entry(={name}展開), tests_field, timeout` → events `E_compile`/`passed`。`event_map` で `E_fail = not passed` 等を定義。
  - `type: llm_judge`: `judge:{adapter,model}`, `blind:true`, `rubric`, `output_schema`, `events:{name: "式"}`（judgment 変数のみの制限名前空間で eval。例 `is_confident_specific_claim and not is_abstention`）, `deterministic_crosscheck:{field, regex}`。
  - `type: regex`: `events:{name: pattern}`。
- `analysis`: `primary_event`, `polarity: minimize|maximize`（H1 が primary を最小化＝慎重さ/失敗イベント、最大化＝正答イベント）, `baselines:[...]`, `bootstrap_B`, `secondary_events:[...]`。
- `protocol`: `R, concurrency, seed`。`results_dir`。

## verdict（stats.py を polarity 対応で）

primary を「悪いイベント（H1 が下げる）」に正規化（maximize 指定なら 1-rate に反転して評価）。判定は既存ロジック:
- `meaning_attributable`: H1−envelope の95%CI<0 ∧ H1−paraphrase のCIが0含む ∧ contra が primary 最大。
- `surface_confound`（H1≈envelope）/ `fragile`（H1≠para）/ `inconclusive`（floor/ceiling=全条件同値、または上記いずれも非該当）。
- `tradeoff_flag`: secondary（例 over_refusal↑ / correct↓）が baseline 比で有意悪化。

## ガードレール（ツールの中核価値＝規律の既定化）

`guardrails.py` が実行し report 冒頭と stderr に出す:
1. **judge==subject 警告**: llm_judge の判定器が被検体と同一モデル → 「scorer-bias の恐れ」。
2. **envelope 欠如警告**: conditions に envelope ロール未設定 → 「長さ効果を分離できない」。
3. **headroom 検出**: 全条件で primary がほぼ同値（floor/ceiling）→ 「識別力なし、課題難度/被検体を見直せ」。実測で両 PoC の律速だった。
4. **複数イベント推奨**: secondary 未設定の llm_judge → 「副作用（tradeoff）を見逃す恐れ」。
5. **error 率報告**（暗黙の打ち切り禁止）。

## 事前登録

`hattr preregister STUDY.yaml`: tasks/conditions/scorer/analysis を正規化し SHA、`STUDY.yaml` 隣に `STUDY.prereg.json`（hash＋timestamp は引数で受ける、`new Date()` 禁止）を書き git commit。`run` 時に現 config の hash と突き合わせ、drift があれば警告（post-hoc 検出）。

## 制約（CLAUDE.md 準拠）

最小依存（標準ライブラリ＋numpy＋pyyaml。openai_compat は `urllib` で十分、新規依存追加しない）。リトライ/フォールバック/キャッシュ/独自例外階層を作らない。重複ロジックは stats.py 等の再利用で排除。`--mock`（被検体・判定器を呼ばずダミー）と `--limit N`（課題上限）必須。

## 受け入れテスト（実装後に必ず実行して報告）

1. `python -m hattr.cli run hattr/examples/codegen.study.yaml --limit 3` が完走し、report.md に条件別 E_fail と verdict が出る（実 codex 呼び出し）。
2. `python -m hattr.cli run hattr/examples/safety.study.yaml --mock` が完走（codex 不使用でパイプライン検証）。
3. safety 例で judge==subject にすると **guardrail 警告**が出る。
4. ガードレールの headroom 検出が floor 時に発火する。
作成ファイル一覧と上記の実行結果（report.md 末尾）を報告すること。
