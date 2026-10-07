"""
Hallucination Filter Service for Whisper Transcripts (Orchestrator Service)

Implements a 4-layer hierarchical filtering pipeline to detect and filter
hallucinated segments produced by Whisper (e.g. YouTube spam, outro text, silence artifacts):
  1. Strong keyword matching (< 0.1ms fast exit)
  2. Rapidfuzz token-set-ratio matching
  3. Semantic Bi-Encoder similarity (via ONNX Runtime INT8 CPU)
  4. Whisper audio metrics correlation (no_speech_prob, avg_logprob, compression_ratio)
"""

import asyncio
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from orchestrator_service.config.application_config import get_config
from orchestrator_service.constants.hallucination_constants import RAW_HALLUCINATIONS, STRONG_KEYWORDS
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def normalize_vn(text: str) -> str:
    """Normalize Vietnamese text: lowercase, remove punctuation, normalize orthography."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[^\w\sàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.replace("đăng kí", "đăng ký")
    return text.strip()


class OnnxEmbeddingEngine:
    """
    Lightweight embedding engine running on ONNX Runtime CPU.
    Automatically normalizes output vectors using L2 norm.
    """

    def __init__(self, model_path: str, tokenizer_path: Optional[str] = None, max_length: int = 128):
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.max_length = max_length
        self._session = None
        self._tokenizer = None
        self._available = False

    def initialize(self) -> bool:
        """Load tokenizer and ONNX inference session."""
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as e:
            logger.warning(f"onnxruntime or transformers not installed. Layer 3 (Semantic ONNX) will be disabled: {e}")
            self._available = False
            return False

        resolved_model_path = self.model_path
        if not os.path.exists(resolved_model_path):
            filename = os.path.basename(resolved_model_path)
            candidates = [
                os.path.join(os.getcwd(), resolved_model_path),
                os.path.abspath(resolved_model_path),
                os.path.join(os.getcwd(), "models", "bi-encoder-model", filename),
                os.path.join(os.getcwd(), "models", filename),
                os.path.join(os.getcwd(), "..", "..", "models", "bi-encoder-model", filename),
                os.path.join(os.getcwd(), "..", "..", "models", filename),
                os.path.join(os.getcwd(), "..", "models", "bi-encoder-model", filename),
            ]
            found = False
            for cand in candidates:
                if os.path.exists(cand):
                    resolved_model_path = os.path.abspath(cand)
                    found = True
                    break

            if not found:
                if "/" in resolved_model_path and not resolved_model_path.endswith(".onnx"):
                    try:
                        from huggingface_hub import hf_hub_download

                        logger.info(f"Downloading ONNX model from Hugging Face Hub: {resolved_model_path} ...")
                        resolved_model_path = hf_hub_download(
                            repo_id=resolved_model_path, filename="vietnamese_bi_encoder_int8_accurate.onnx"
                        )
                    except Exception as ex:
                        logger.warning(f"Failed to download ONNX model from Hugging Face: {ex}")
                        self._available = False
                        return False
                else:
                    logger.warning(f"ONNX model not found at '{resolved_model_path}'. Layer 3 (Semantic ONNX) will be disabled.")
                    self._available = False
                    return False

        # Resolve tokenizer source:
        # Check if tokenizer files exist alongside the ONNX model directory first,
        # otherwise use configured tokenizer_path or default to Hugging Face model repo.
        tok_source = self.tokenizer_path
        model_dir = os.path.dirname(resolved_model_path)
        if model_dir and any(os.path.exists(os.path.join(model_dir, f)) for f in ["tokenizer.json", "vocab.txt", "tokenizer_config.json"]):
            tok_source = model_dir
        elif not tok_source:
            tok_source = "bkai-foundation-models/vietnamese-bi-encoder"

        try:
            logger.info(f"Loading tokenizer: {tok_source}")
            self._tokenizer = AutoTokenizer.from_pretrained(tok_source)

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(4, os.cpu_count() or 1)
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            logger.info(f"Loading ONNX Session: {resolved_model_path}")
            self._session = ort.InferenceSession(
                resolved_model_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._available = True
            logger.info("✅ OnnxEmbeddingEngine successfully initialized.")
            return True
        except Exception as e:
            logger.error(f"Error loading OnnxEmbeddingEngine: {e}", exc_info=True)
            self._available = False
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    def encode(self, texts: List[str]) -> np.ndarray:
        """Encode texts into numpy vector array (N, D)."""
        if not self._available or self._session is None or self._tokenizer is None:
            return np.empty((0, 768), dtype=np.float32)

        if isinstance(texts, str):
            texts = [texts]

        tokens = self._tokenizer(
            texts, padding=True, truncation=True, max_length=self.max_length, return_tensors="np"
        )
        ort_inputs = {
            "input_ids": tokens["input_ids"].astype(np.int64),
            "attention_mask": tokens["attention_mask"].astype(np.int64),
        }
        outputs = self._session.run(None, ort_inputs)
        embeddings = outputs[0]

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return embeddings / norms


@singleton
class HallucinationFilterService:
    """
    Service for detecting and filtering hallucinations from transcript segments in orchestrator.
    Always active and continuously running.
    """

    def __init__(self):
        self._config = get_config().hallucination_filter
        self._similarity_threshold = self._config.similarity_threshold
        self._fuzzy_threshold = self._config.fuzzy_threshold
        self._no_speech_threshold = self._config.no_speech_threshold
        self._avg_logprob_threshold = self._config.avg_logprob_threshold
        self._compression_ratio_threshold = self._config.compression_ratio_threshold

        self._embedding_engine: Optional[OnnxEmbeddingEngine] = None
        self._boh_texts: List[str] = []
        self._boh_embeddings: Optional[np.ndarray] = None
        self._fuzzy_available = False
        self._initialized = False

        self._stats = {
            "checked_segments": 0,
            "filtered_segments": 0,
            "reasons": {},
        }

    def initialize(self) -> None:
        """Initialize resources, load BoH dataset and ONNX model."""
        if self._initialized:
            return

        # 1. Check Rapidfuzz availability
        try:
            from rapidfuzz import fuzz, process

            self._fuzzy_available = True
        except ImportError:
            logger.warning("rapidfuzz is not installed. Layer 2 (Fuzzy matching) will use fallback.")
            self._fuzzy_available = False

        # 2. Build normalized Bag of Hallucinations (BoH) list
        seen = set()
        self._boh_texts = []
        for h in RAW_HALLUCINATIONS:
            norm = normalize_vn(h)
            if len(norm) >= 8 and norm not in seen:
                self._boh_texts.append(norm)
                seen.add(norm)

        # 3. Initialize OnnxEmbeddingEngine
        self._embedding_engine = OnnxEmbeddingEngine(
            model_path=self._config.onnx_model_path,
            tokenizer_path=self._config.tokenizer_path,
        )
        engine_loaded = self._embedding_engine.initialize()

        if engine_loaded and len(self._boh_texts) > 0:
            logger.info(f"Pre-computing BoH vector embeddings ({len(self._boh_texts)} samples)...")
            try:
                self._boh_embeddings = self._embedding_engine.encode(self._boh_texts)
                logger.info(f"BoH vectors successfully pre-computed: shape {self._boh_embeddings.shape}")
            except Exception as e:
                logger.error(f"Failed to pre-compute BoH embeddings: {e}")
                self._boh_embeddings = None
        else:
            logger.info("HallucinationFilterService running in lightweight mode (Keywords, Fuzzy, Whisper metrics).")

        self._initialized = True
        logger.info("✅ HallucinationFilterService is ready and continuously active.")

    def get_stats(self) -> Dict[str, Any]:
        """Return statistics of checked and filtered segments."""
        return dict(self._stats)

    def is_hallucination(
        self,
        text: str,
        no_speech_prob: Optional[float] = None,
        avg_logprob: Optional[float] = None,
        compression_ratio: Optional[float] = None,
        return_detail: bool = False,
    ) -> Any:
        """
        Check whether a text segment is a hallucination via 4 hierarchical layers.
        """

        self._stats["checked_segments"] += 1

        if not text or len(text.strip()) < 8:
            return (False, "too_short", 0.0) if return_detail else False

        norm = normalize_vn(text)
        if len(norm) < 6:
            return (False, "too_short", 0.0) if return_detail else False

        # ---------- LAYER 1: Strong Keyword Guard (fast exit for ~95% clean speech) ----------
        has_strong_keyword = any(kw in norm for kw in STRONG_KEYWORDS)
        if not has_strong_keyword:
            return (False, "no_keyword", 0.0) if return_detail else False

        # ---------- LAYER 2: Fuzzy Matching ----------
        if self._fuzzy_available and self._boh_texts:
            from rapidfuzz import fuzz, process

            fuzzy_result = process.extractOne(
                norm, self._boh_texts, scorer=fuzz.token_set_ratio, score_cutoff=self._fuzzy_threshold
            )
            if fuzzy_result:
                score = float(fuzzy_result[1]) / 100.0
                self._record_filtered("fuzzy")
                return (True, "fuzzy", score) if return_detail else True
        elif self._boh_texts:
            norm_tokens = set(norm.split())
            for boh in self._boh_texts:
                if boh in norm or norm in boh:
                    self._record_filtered("substring")
                    return (True, "substring", 1.0) if return_detail else True
                boh_tokens = set(boh.split())
                overlap = len(norm_tokens & boh_tokens)
                if overlap >= 3 and overlap / min(len(norm_tokens), len(boh_tokens)) >= 0.8:
                    self._record_filtered("token_overlap")
                    return (True, "token_overlap", 0.85) if return_detail else True

        # ---------- LAYER 3: Semantic Cosine Similarity (ONNX) ----------
        max_sim = 0.0
        if (
            self._embedding_engine is not None
            and self._embedding_engine.is_available
            and self._boh_embeddings is not None
            and len(self._boh_embeddings) > 0
        ):
            try:
                query_emb = self._embedding_engine.encode([norm])[0]
                similarities = np.dot(self._boh_embeddings, query_emb)
                max_sim = float(np.max(similarities))

                if max_sim >= self._similarity_threshold:
                    self._record_filtered("semantic")
                    return (True, "semantic", max_sim) if return_detail else True
            except Exception as e:
                logger.debug(f"Failed to calculate embedding similarity: {e}")

        # ---------- LAYER 4: Whisper Audio Metrics Correlation ----------
        suspicious_metric = False
        if no_speech_prob is not None and no_speech_prob > self._no_speech_threshold:
            suspicious_metric = True
        if avg_logprob is not None and avg_logprob < self._avg_logprob_threshold:
            suspicious_metric = True
        if compression_ratio is not None and compression_ratio > self._compression_ratio_threshold:
            suspicious_metric = True

        if suspicious_metric and has_strong_keyword:
            self._record_filtered("keyword+metric")
            return (True, "keyword+metric", max_sim) if return_detail else True

        return (False, "clean", max_sim) if return_detail else False

    def filter_segments(
        self, segments: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter a batch of transcript segment dictionaries.
        Returns: (clean_segments, filtered_segments)
        """
        if not segments:
            return [], []

        clean_segments: List[Dict[str, Any]] = []
        filtered_segments: List[Dict[str, Any]] = []

        for seg in segments:
            text = seg.get("text") or seg.get("content") or ""
            metadata = seg.get("metadata") or {}

            no_speech_prob = metadata.get("no_speech_prob") if isinstance(metadata, dict) else None
            avg_logprob = metadata.get("avg_logprob") if isinstance(metadata, dict) else None
            compression_ratio = metadata.get("compression_ratio") if isinstance(metadata, dict) else None

            is_h, reason, score = self.is_hallucination(
                text=text,
                no_speech_prob=no_speech_prob,
                avg_logprob=avg_logprob,
                compression_ratio=compression_ratio,
                return_detail=True,
            )

            if is_h:
                filtered_info = dict(seg)
                filtered_info["filter_reason"] = reason
                filtered_info["filter_score"] = round(score, 4)
                filtered_segments.append(filtered_info)
            else:
                clean_segments.append(seg)

        return clean_segments, filtered_segments

    async def filter_segments_async(
        self, segments: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Async wrapper to run segment filtering in a worker thread so as not to block
        the orchestrator's event loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.filter_segments, segments)

    def _record_filtered(self, reason: str) -> None:
        """Record filtered hallucination metrics."""
        self._stats["filtered_segments"] += 1
        self._stats["reasons"][reason] = self._stats["reasons"].get(reason, 0) + 1


def get_hallucination_filter_service() -> HallucinationFilterService:
    """Helper function to retrieve the singleton HallucinationFilterService instance."""
    return HallucinationFilterService()
