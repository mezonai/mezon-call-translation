"""Cache-aware Nemotron streaming recognition for one client."""

import json
import re
from pathlib import Path
from typing import Tuple

import numpy as np
import onnxruntime_genai as og


_LOCALE_TAG_RE = re.compile(r"<[^>]+>")
_UTTERANCE_PREFIX_RE = re.compile(r"^[\s.,!?;:…]+")


class NemotronModel:
    """Load the heavyweight runtime model once and create client streams."""

    def __init__(self, model_path: str):
        self.model_path = Path(model_path)
        config_path = self.model_path / "genai_config.json"
        with config_path.open(encoding="utf-8") as config_file:
            model_config = json.load(config_file)["model"]

        self.sample_rate = int(model_config["sample_rate"])
        self.chunk_samples = int(model_config["chunk_samples"])
        self.runtime_model = og.Model(str(self.model_path))
        if self.runtime_model.type != "nemotron_speech":
            raise RuntimeError(
                f"Unexpected model type {self.runtime_model.type!r}; "
                "expected 'nemotron_speech'"
            )

    def create_stream(self, language_id: int, empty_piece_limit: int) -> "NemotronStream":
        return NemotronStream(self, language_id, empty_piece_limit)


class NemotronStream:
    """Persistent processor, decoder, and endpoint state for one client."""

    def __init__(
        self,
        model: NemotronModel,
        language_id: int,
        empty_piece_limit: int,
    ):
        if empty_piece_limit < 1:
            raise ValueError("empty_piece_limit must be at least 1")

        self.processor = og.StreamingProcessor(model.runtime_model)
        self.processor.set_option("use_vad", "false")
        self.tokenizer = og.Tokenizer(model.runtime_model)
        self.tokenizer_stream = self.tokenizer.create_stream()
        self.generator = og.Generator(
            model.runtime_model,
            og.GeneratorParams(model.runtime_model),
        )
        self.generator.set_runtime_option("lang_id", str(language_id))

        self.empty_piece_limit = empty_piece_limit
        self.empty_piece_count = 0
        self.current_text = ""

    def _decode(self, inputs) -> str:
        """ Return the transcribed text after each 560ms audio"""
        if inputs is None:
            return ""

        self.generator.set_inputs(inputs)
        piece = ""
        while not self.generator.is_done():
            self.generator.generate_next_token()
            tokens = self.generator.get_next_tokens()
            if len(tokens) > 0:
                piece += self.tokenizer_stream.decode(tokens[0])
        return piece

    def _clean_piece(self, piece: str) -> str:
        """Remove mistaken leading punctuation like , . <en-es>"""
        piece = _LOCALE_TAG_RE.sub("", piece)
        if not self.current_text:
            piece = _UTTERANCE_PREFIX_RE.sub("", piece)
        return piece

    def process(self, samples: np.ndarray) -> Tuple[str, bool]:
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        piece = self._clean_piece(self._decode(self.processor.process(samples)))

        if piece == "":
            self.empty_piece_count += 1
        else:
            self.current_text += piece
            self.empty_piece_count = 0

        text = self.current_text.strip()
        is_final = bool(text) and self.empty_piece_count >= self.empty_piece_limit

        if is_final:
            # Keep the cache-aware processor alive. Only the utterance state resets.
            self.current_text = ""
            self.empty_piece_count = 0

        return text, is_final
