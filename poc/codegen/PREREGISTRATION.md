# 事前登録 — コード生成題材（要因 = 詳細な仕様 spec）

PoC II。データ生成前にコミットし、予測・判定規則がデータに先行することを保証する。安全性題材（`../PREREGISTRATION.md`, `../FINDINGS.md`）の続き。

## なぜコード生成か

安全性題材の最大の脅威は **scorer-bias**（LLM 判定器でイベントを採点 → 判定器依存で verdict が割れた）。コード生成では主イベントを**実行（隠しテスト通過）で採点**できる＝決定的オラクル。**LLM 判定器が不要になり scorer-bias が消える**。methodology が指していた「より科学的に扱える」題材。

## 凍結する設計

- **被検体**: codex CLI 既定（gpt-5.5）、**1-shot・read-only**（エージェント修復ループは使わない＝プロンプト要因を分離）。`--output-schema {code}` で実装のみ抽出。
- **要因 A**: 関数の**詳細な仕様**。名前/シグネチャだけでは挙動が非自明な12関数を用い、仕様で初めて正しく実装できるよう設計（headroom を確保）。
- **採点（決定的・LLM不使用）**: 生成コードを隔離サブプロセス（`python -S`, timeout 15s, 一時dir）で**隠しテスト**に掛ける。
- **主イベント**: `E_fail` = 隠しテストを通過しない（1=失敗）。副: `E_compile`。
- **6条件**: H1(詳細仕様) / H0_ablate(シグネチャのみ) / H0_neutral("正しく堅牢に実装") / H_base_len(同長・無関係な一般助言=包絡線) / H_para(仕様の言い換え) / H_contra(**誤った仕様**)。
- **データ**: `tasks.jsonl`（12課題、隠しテスト）。規模 R=3、bootstrap B=2000、seed=12345。判定は `run_codegen.py` の verdict（safety と同型: E_fail を最小化するのが H1）。

## 予測（データ生成前に固定）

1. `E_fail`: H1 < H0_ablate（仕様が失敗を減らす）。
2. H1 < H_base_len（**包絡線を有意に下回る**＝長さでなく仕様の中身が効く）。
3. H1 ≈ H_para（言い換えに頑健）。
4. **H_contra が E_fail 最大**（誤った仕様が実装を誤らせる＝機構確認）。

## 判定規則（事前固定・客観）

- **meaning_attributable（確証）**: H1 − H_base_len の95%CIが0未満 ∧ H1 − H_para のCIが0を含む ∧ H_contra が E_fail 最大。
- それ以外: `surface_confound`（H1≈H_base_len）/ `fragile`（H1≠H_para）/ `inconclusive`（全0=floor等）。
- 採点は実行ベースで判定器バイアス無し。結果に合わせた定義・データ・規則の事後調整は禁止。

## 実行

```
python poc/codegen/run_codegen.py            # subject=gpt-5.5 1-shot, R=3, 12課題×6条件
```
出力 `poc/results_codegen/`（git無視）。本ファイルのコミット後に実行する。
