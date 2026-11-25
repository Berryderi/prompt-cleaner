"""Makes the repo root importable when running `python -m unittest discover -s tests`."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# tests/samples.jsonl is your own (git-ignored) set; the shipped synthetic set is the fallback.
_PRIVATE = Path(__file__).with_name("samples.jsonl")
SAMPLES_PATH = _PRIVATE if _PRIVATE.exists() else Path(__file__).with_name("samples.example.jsonl")


def load_samples() -> list[dict]:
    with SAMPLES_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
