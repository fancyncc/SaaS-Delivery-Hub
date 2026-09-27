import pytest

from scripts.download_rag_models import weight_files


def test_download_prefers_safe_weights_and_ignores_exported_models():
    assert weight_files(["pytorch_model.bin", "model.safetensors", "onnx/model.safetensors"]
                        ) == ["model.safetensors"]
    assert weight_files(["pytorch_model-00001-of-00002.bin", "pytorch_model-00002-of-00002.bin",
                         "onnx/model.onnx", "optimizer.bin"]
                        ) == ["pytorch_model-00001-of-00002.bin", "pytorch_model-00002-of-00002.bin"]
    with pytest.raises(ValueError, match="no supported inference weights"):
        weight_files(["config.json", "onnx/model.onnx", "optimizer.bin"])
