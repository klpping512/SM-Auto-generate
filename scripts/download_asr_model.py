"""Download the configured local faster-whisper model without storing credentials."""
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET = PROJECT_ROOT / "data" / "models" / "faster-whisper-small"


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="Systran/faster-whisper-small",
        local_dir=TARGET,
        allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    )
    print(f"ASR model ready: {TARGET}")


if __name__ == "__main__":
    main()
