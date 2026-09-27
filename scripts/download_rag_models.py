"""Explicit one-time download into the same cache used by offline inference."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings


def weight_files(filenames):
    """Prefer safetensors; pinned BGE-M3 also needs its PyTorch BIN fallback."""
    files = [name for name in filenames if not name.startswith(("onnx/", "openvino/"))]
    safe = [name for name in files if name.endswith(".safetensors")]
    if safe:
        return safe
    legacy = [name for name in files
              if name.startswith("pytorch_model") and name.endswith(".bin") and "/" not in name]
    if not legacy:
        raise ValueError("Pinned model has no supported inference weights")
    return legacy


def main():
    from huggingface_hub import HfApi, snapshot_download

    settings = get_settings()
    for kind in ("embedding", "reranker"):
        name = getattr(settings, f"{kind}_local_model")
        if Path(name).is_dir():
            print(f"{kind}: using existing local model directory")
            continue
        revision = getattr(settings, f"{kind}_revision")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError(f"{kind}: pin a verified 40-character model commit before downloading")
        info = HfApi().model_info(name, revision=revision)
        if info.sha != revision:
            raise ValueError(f"{kind}: model metadata does not match the pinned revision")
        weights = weight_files([entry.rfilename for entry in info.siblings])
        snapshot = snapshot_download(repo_id=name, revision=revision,
            cache_dir=settings.retrieval_model_cache,
            allow_patterns=["*.json", "*.txt", "*.model", *weights],
            ignore_patterns=["onnx/*", "openvino/*"])
        if any(not (Path(snapshot) / file).is_file() for file in weights):
            raise RuntimeError(f"{kind}: downloaded snapshot is missing required weights")
        print(f"{kind}: downloaded to configured cache")


if __name__ == "__main__":
    main()
