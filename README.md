# Sayvors Voice AI — Arabic Dialect TTS Fine-Tuning Pipeline

Research pipeline to fine-tune **Qwen3-TTS-12Hz-1.7B-Base** on Arabic + dialect data
for the Sayvors platform voice API.

## Project structure

```
data/           # raw + processed audio + JSONL manifests (not committed)
src/            # pipeline code (download, preprocess, normalize, manifest, train, infer)
configs/        # smoke.yaml (local CPU) and full.yaml (vast.ai GPU)
tests/          # pytest data pipeline tests
checkpoints/    # model checkpoints (not committed)
logs/           # training logs (not committed)
```

## Quick start

1. **Install dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Drop one Arabic wav** into `data/raw/`

3. **Run smoke test** (local CPU)
   ```powershell
   python -m src.smoke_test
   ```

4. **Run tests**
   ```powershell
   pytest tests/ -q
   ```

5. **Download all datasets** (requires HF token if gated)
   ```powershell
   python -m src.download --all
   ```

6. **Full training** — deploy to vast.ai GPU VM and run `src.train_qwen` with `configs/full.yaml`

## Datasets

All datasets from the Excel audit are included.
