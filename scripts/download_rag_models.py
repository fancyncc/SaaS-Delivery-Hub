"""Explicit one-time download into the same cache used by offline inference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings


def main():
    from huggingface_hub import snapshot_download

    settings = get_settings()
    for kind in ("embedding", "reranker"):
        name = getattr(settings, f"{kind}_local_model")
        if Path(name).is_dir():
            print(f"{kind}: using existing local model directory")
            continue
        snapshot_download(repo_id=name, revision=getattr(settings, f"{kind}_revision"),
            cache_dir=settings.retrieval_model_cache,
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
            ignore_patterns=["onnx/*", "openvino/*"])
        print(f"{kind}: downloaded to configured cache")


if __name__ == "__main__":
    main()
