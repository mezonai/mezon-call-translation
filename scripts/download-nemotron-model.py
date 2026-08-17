"""Download the ONNX INT4 Nemotron streaming model from Hugging Face."""

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


DEFAULT_REPOSITORY = "onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4"
DEFAULT_MODEL_NAME = "nemotron-3.5-asr-streaming-0.6b-onnx-int4"


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "models" / "nemotron-model",
    )
    args = parser.parse_args()

    target = args.output.resolve() / args.model_name
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=args.repository, local_dir=target)
    print(f"Nemotron model downloaded to: {target}")


if __name__ == "__main__":
    main()
