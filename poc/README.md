# Codex CLI safety attribution PoC

This PoC runs the condition arms defined in `DESIGN.md` against `codex exec`, scores outputs with a blind LLM judge plus deterministic abstention cross-check, and writes raw outputs, scored rows, a report, and run metadata under `poc/results`.

```bash
python poc/src/run.py --config poc/config.yaml
python poc/src/run.py --config poc/config.yaml --mock
python poc/src/run.py --config poc/config.yaml --limit 8
```
