from pathlib import Path

SERVICE_NAME = "listelog"
config_dir = Path.home() / f".{SERVICE_NAME}"

vad_model_path = "assets/silero_vad.onnx"
denoiser_model_path = "assets/deepfilter-stream/dfn3-512-v1/denoiser_model.onnx"