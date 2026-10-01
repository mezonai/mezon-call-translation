"""
Parakeet TDT FP16 ASR Engine for High-Throughput Non-Realtime Speech-to-Text.

This module provides:
1. Local Model Resolution & Integrity Validation:
   - Resolves model files from local storage (`models/parakeet-model`).
   - Ensures zero dependency on runtime internet access or Hugging Face Hub downloads,
     mirroring the offline architecture of the Gipformer fallback service.
2. 2-Stage Dynamic Calibration:
   - Warm-up phase and expansion rate measurement (MB/sec per audio second) to dynamically
     calculate safe batching capacity within GPU memory limits (GPU_MEM_LIMIT_MB).
3. Pre-emptive VRAM Protection & Layer 2 OOM Recovery:
   - Evaluates incoming audio durations before admitting them into a GPU batch.
   - Safely evicts the last item back to the queue and retries immediately if an OOM occurs.
4. Natural Sentence-Level Timestamp Segmentation:
   - Groups subword tokens by acoustic pause boundaries (>= 0.6s), terminal punctuation,
     and maximum word limits into clean subtitle/transcript segments.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import onnx_asr
import onnxruntime as rt
import soundfile as sf
import torch

from non_realtime_stt_service.config import get_config
from non_realtime_stt_service.service.parakeet.async_job_service import JobStatus, get_async_job_service
from non_realtime_stt_service.utils.audio_converter import cleanup_temp_files
from non_realtime_stt_service.utils.decorator import singleton

logger = logging.getLogger(__name__)

# ==============================================================================
# Constants & Memory Calibration Defaults
# ==============================================================================

# CUDA and ONNX Runtime memory management constants
CUDA_DRIVER_OVERHEAD_MB: int = 350       # Memory reserved for CUDA driver handles & cuDNN context
MIN_SAFE_RATE_MB_PER_SEC: float = 4.8    # Baseline FastConformer subsampling workspace (MB/audio-second)
SAFE_BUDGET_RATIO: float = 0.90          # 10% safety buffer against physical GPU VRAM ceiling
DEFAULT_MODEL_BASE_MB: float = 1400.0    # Baseline weights footprint for Parakeet 0.6B FP16

# Required model files for Parakeet TDT FP16 ASR (must exist in models/parakeet-model)
PARAKEET_REQUIRED_FILES: Tuple[str, ...] = (
    "config.json",
    "decoder_joint-model.fp16.onnx",
    "encoder-model.fp16.onnx",
    "nemo128.onnx",
    "vocab.txt",
)


# ==============================================================================
# 1. Local Model Resolution & Validation Helpers
# ==============================================================================

def resolve_parakeet_model_path(model_path: str | Path) -> Path:
    """
    Resolve the filesystem directory containing Parakeet model artifacts.

    Resolution Strategy:
    1. If `model_path` is already an absolute path, verify and return it.
    2. Otherwise, check relative to the project root directory (4 levels up from this file).
    3. Check under `{project_root}/models/{model_path}`.
    4. Check relative to the current working directory (`Path.cwd()`).

    Args:
        model_path: Relative or absolute path specified in configuration or .env.

    Returns:
        Path: Resolved absolute or normalized filesystem Path.
    """
    resolved = Path(model_path)
    if resolved.is_absolute():
        return resolved

    # Locate project root (non_realtime_stt_service/service/parakeet -> project_root is 4 parents up)
    project_root = Path(__file__).resolve().parents[4]

    if (project_root / resolved).is_dir():
        return project_root / resolved
    elif (project_root / "models" / resolved).is_dir():
        return project_root / "models" / resolved
    elif (Path.cwd() / resolved).is_dir():
        return Path.cwd() / resolved
    else:
        # Default to project_root / resolved for deterministic error reporting
        return project_root / resolved


def validate_parakeet_model_files(model_dir: Path) -> Dict[str, Path]:
    """
    Verify that the Parakeet model directory and all essential artifacts exist locally.

    Args:
        model_dir: Path to directory containing Parakeet ONNX model files.

    Returns:
        Dict mapping artifact names to their full file Paths.

    Raises:
        FileNotFoundError: If the directory or any required ONNX/config file is missing.
    """
    if not model_dir.is_dir():
        raise FileNotFoundError(
            f"Parakeet model directory not found: '{model_dir}'. "
            f"Please run 'bash scripts/download-parakeet-model.sh' to download the model locally."
        )

    resolved_files: Dict[str, Path] = {}
    missing_files: List[str] = []

    for filename in PARAKEET_REQUIRED_FILES:
        file_path = model_dir / filename
        if not file_path.is_file():
            missing_files.append(filename)
        else:
            resolved_files[filename] = file_path

    if missing_files:
        raise FileNotFoundError(
            f"Parakeet model directory '{model_dir}' is incomplete. Missing files: {missing_files}. "
            f"Please run 'bash scripts/download-parakeet-model.sh --force' to re-download the model."
        )

    return resolved_files


# ==============================================================================
# 2. Segment-Level Timestamp Extraction
# ==============================================================================

def extract_segments(tokens: list, timestamps: list) -> List[Dict[str, Any]]:
    """
    Convert raw subword tokens and model timestamps into readable, sentence-like segments.

    Splitting Rules:
    1. Acoustic speech pause >= 0.6s between consecutive words.
    2. Terminal punctuation marks (. ? ! ...).
    3. Maximum word count window of 15 words per segment to optimize UI subtitle rendering.

    Args:
        tokens: Subword token strings emitted by the Parakeet CTC/TDT decoder.
        timestamps: Token-level timestamp float values in seconds.

    Returns:
        List of segment dictionaries containing 'start', 'end', and 'text'.
    """
    if not tokens or not timestamps:
        return []

    words: List[Dict[str, Any]] = []
    curr_word = ""
    curr_start: Optional[float] = None

    # Step 1: Reassemble SentencePiece subwords into complete lexical words
    for tok, t in zip(tokens, timestamps):
        # SentencePiece subwords usually begin with   or standard space
        is_new_word = tok.startswith(" ") or tok.startswith(" ") or (not curr_word)
        if is_new_word:
            if curr_word and curr_start is not None:
                words.append({
                    "word": curr_word.replace(" ", "").replace(" ", "").strip(),
                    "start": round(curr_start, 2),
                    "end": round(t, 2),
                })
            curr_word = tok
            curr_start = t
        else:
            curr_word += tok

    # Append remaining trailing word
    if curr_word and curr_start is not None:
        words.append({
            "word": curr_word.replace(" ", "").replace(" ", "").strip(),
            "start": round(curr_start, 2),
            "end": round(curr_start + 0.25, 2),
        })

    # Step 2: Aggregate words into semantic subtitle segments
    segments: List[Dict[str, Any]] = []
    if words:
        seg_words = [words[0]]
        for w in words[1:]:
            pause = w["start"] - seg_words[-1]["end"]
            prev_word = seg_words[-1]["word"]
            is_sentence_end = prev_word.endswith((".", "?", "!", "..."))

            if pause >= 0.6 or is_sentence_end or len(seg_words) >= 15:
                segments.append({
                    "start": seg_words[0]["start"],
                    "end": seg_words[-1]["end"],
                    "text": " ".join(x["word"] for x in seg_words),
                })
                seg_words = [w]
            else:
                seg_words.append(w)

        if seg_words:
            segments.append({
                "start": seg_words[0]["start"],
                "end": seg_words[-1]["end"],
                "text": " ".join(x["word"] for x in seg_words),
            })

    return segments


# ==============================================================================
# 3. Parakeet TDT FP16 ASR Engine
# ==============================================================================

@singleton
class ParakeetEngine:
    """
    Parakeet TDT FP16 ASR Engine with Dynamic Batching and VRAM Pre-emptive Protection.

    Key Architectural Principles:
    - Fully Offline Loading: Reads ONNX weights, tokenizer, and configurations directly
      from `models/parakeet-model` with zero Hugging Face runtime dependency.
    - Two-Stage Hardware Calibration:
        * Phase 1: 3-second warm-up audio to initialize ONNX Runtime memory arenas.
        * Phase 2: 15-second audio to calculate workspace expansion rate (MB/sec).
    - Pre-emptive VRAM Admission: Estimates candidate batch memory before submission to GPU.
    - Layer-2 OOM Protection: Safely evicts the last item back to queue and retries upon OOM.
    - Component Health Reporting: Exposes health status for system monitoring and FastAPI.
    """

    def __init__(self) -> None:
        self._config = get_config().parakeet
        self.model: Any = None
        self._model_dir: Optional[Path] = None
        self.request_queue: asyncio.Queue = asyncio.Queue()
        self.batch_sequence_counter: int = 0
        self.in_flight_converting: int = 0
        self.worker_task: Optional[asyncio.Task] = None
        self.is_initialized: bool = False

        self.calibration_info: Dict[str, Any] = {
            "vram_rate_mb_per_sec": MIN_SAFE_RATE_MB_PER_SEC,
            "max_effective_duration_sec": 500.0,
            "model_base_vram_mb": DEFAULT_MODEL_BASE_MB,
            "gpu_mem_limit_mb": self._config.gpu_mem_limit_mb,
        }

    # --------------------------------------------------------------------------
    # Memory Calibration & VRAM Estimation
    # --------------------------------------------------------------------------

    def calibrate_specs(
        self,
        model_instance: Any,
        target_limit_mb: int,
        free_before_load_bytes: int = 0,
        free_after_load_bytes: int = 0,
    ) -> Dict[str, Any]:
        """
        Calibrate runtime batching budget based on GPU_MEM_LIMIT_MB and a 2-stage dummy test:
        - Phase 1: 3s warm-up to instantiate execution context and memory pools.
        - Phase 2: 15s measurement to compute expansion workspace per effective audio second.
        """
        if not torch.cuda.is_available():
            logger.info("ℹ️ [CALIBRATION] PyTorch CUDA extension not loaded; applying baseline VRAM specs.")
            return {
                "vram_rate_mb_per_sec": MIN_SAFE_RATE_MB_PER_SEC,
                "max_effective_duration_sec": 500.0,
                "model_base_vram_mb": DEFAULT_MODEL_BASE_MB,
                "gpu_mem_limit_mb": target_limit_mb,
            }

        # 1. Base model memory footprint
        if free_before_load_bytes and free_after_load_bytes and free_before_load_bytes > free_after_load_bytes:
            loaded_mb = (free_before_load_bytes - free_after_load_bytes) / (1024 ** 2)
            model_base_vram_mb = round(loaded_mb * 1.05, 1)
        else:
            model_base_vram_mb = DEFAULT_MODEL_BASE_MB

        # 2. Two-stage dummy calibration
        # Phase 1: 3s warm-up
        dummy_warm = np.zeros(16000 * 3, dtype=np.float32)
        try:
            model_instance.recognize(dummy_warm)
        except Exception as e:
            logger.warning(f"⚠️ [CALIBRATION] Warm-up warning: {e}")

        free_after_warmup = torch.cuda.mem_get_info()[0]
        if free_before_load_bytes and free_before_load_bytes > free_after_warmup:
            actual_base_mb = round(((free_before_load_bytes - free_after_warmup) / (1024 ** 2)) * 1.05, 1)
            model_base_vram_mb = max(model_base_vram_mb, actual_base_mb)

        # Phase 2: 15s measurement
        free_pre_meas = torch.cuda.mem_get_info()[0]
        dummy_meas = np.zeros(16000 * 15, dtype=np.float32)
        try:
            model_instance.recognize(dummy_meas)
        except Exception as e:
            logger.warning(f"⚠️ [CALIBRATION] Measurement warning: {e}")
        free_post_meas = torch.cuda.mem_get_info()[0]

        if free_pre_meas > free_post_meas:
            delta_mb = (free_pre_meas - free_post_meas) / (1024 ** 2)
            measured_rate = round(delta_mb / 12.0, 2)
            rate_mb_per_sec = max(measured_rate, MIN_SAFE_RATE_MB_PER_SEC)
        else:
            rate_mb_per_sec = MIN_SAFE_RATE_MB_PER_SEC

        # 3. Compute safe effective ceiling T_eff with safety buffer
        raw_budget_mb = max(target_limit_mb - model_base_vram_mb, 200.0)
        safe_budget_mb = round(raw_budget_mb * SAFE_BUDGET_RATIO, 1)
        max_effective_duration_sec = round(safe_budget_mb / rate_mb_per_sec, 1)

        logger.info(
            f"📊 [SYSTEM CALIBRATION] Base={model_base_vram_mb:.1f}MB, Rate={rate_mb_per_sec:.2f}MB/s, "
            f"T_eff Ceiling={max_effective_duration_sec:.1f}s"
        )

        return {
            "vram_rate_mb_per_sec": rate_mb_per_sec,
            "max_effective_duration_sec": max_effective_duration_sec,
            "model_base_vram_mb": model_base_vram_mb,
            "gpu_mem_limit_mb": target_limit_mb,
        }

    def estimate_batch_vram_mb(self, batch_items: List[Dict[str, Any]]) -> float:
        """Estimate total VRAM footprint for candidate batch (Base + Batch * D_max * Rate)."""
        base = self.calibration_info.get("model_base_vram_mb", DEFAULT_MODEL_BASE_MB)
        if not batch_items:
            return base
        b = len(batch_items)
        d_max = max(item.get("duration", 0.0) for item in batch_items)
        eff_duration = b * d_max
        rate = self.calibration_info.get("vram_rate_mb_per_sec", MIN_SAFE_RATE_MB_PER_SEC)
        return base + (eff_duration * rate)

    def can_fit_in_batch(self, current_batch: List[Dict[str, Any]], candidate_item: Dict[str, Any]) -> bool:
        """Pre-emptively check if candidate item fits within safe effective duration ceiling."""
        candidate = (current_batch or []) + [candidate_item]
        b = len(candidate)
        d_max = max(item.get("duration", 0.0) for item in candidate)
        effective_duration = b * d_max
        max_allowed = self.calibration_info.get("max_effective_duration_sec", 500.0)
        return effective_duration <= max_allowed

    # --------------------------------------------------------------------------
    # Lifecycle: Initialize, Shutdown & Health
    # --------------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Initialize ONNX Runtime model from local folder, memory arenas, and background worker.
        """
        if not self._config.enabled:
            logger.info("Parakeet ASR engine is disabled in configuration.")
            return

        # 1. Resolve and validate local model artifacts
        model_dir = resolve_parakeet_model_path(self._config.model_path)
        logger.info(f"🔥 [PARAKEET] Resolving local model directory: {model_dir}")
        validate_parakeet_model_files(model_dir)
        self._model_dir = model_dir

        t0 = time.time()
        ort_mem_limit_mb = max(self._config.gpu_mem_limit_mb - CUDA_DRIVER_OVERHEAD_MB, 500)

        # 2. Configure execution providers and memory allocators
        provider_options = [{
            "device_id": str(self._config.device_id),
            "cudnn_conv_algo_search": "DEFAULT",
            "arena_extend_strategy": "kSameAsRequested",
            "cudnn_conv_use_max_workspace": "0",
            "do_copy_in_default_stream": "1",
        }]

        if self._config.gpu_mem_limit_mb:
            provider_options[0]["gpu_mem_limit"] = str(int(ort_mem_limit_mb) * 1024 * 1024)

        sess_options = rt.SessionOptions()
        sess_options.log_severity_level = 3
        # Share BFCArena pool across subgraphs to eliminate duplicate memory buffers
        sess_options.add_session_config_entry("session.use_env_allocators", "1")

        free_before_load = torch.cuda.mem_get_info()[0] if torch.cuda.is_available() else 0

        # Check CUDA availability directly in ONNX Runtime
        has_cuda = "CUDAExecutionProvider" in rt.get_available_providers()
        providers = ["CUDAExecutionProvider"] if has_cuda else ["CPUExecutionProvider"]
        prov_opts = provider_options if has_cuda else [{}]

        if has_cuda:
            logger.info(f"⚡ [PARAKEET] GPU Acceleration active! Using provider: CUDAExecutionProvider (Device ID: {self._config.device_id})")
        else:
            logger.warning("⚠️ [PARAKEET] CUDAExecutionProvider not found in ONNX Runtime. Falling back to CPU.")

        # 3. Load model purely from local artifacts with FP16 precision
        self.model = onnx_asr.load_model(
            None,
            path=str(model_dir),
            quantization="fp16",
            sess_options=sess_options,
            providers=providers,
            provider_options=prov_opts,
        )
        if hasattr(self.model, "asr"):
            self.model.asr.use_low_precision = True

        # 4. Calibrate memory parameters
        free_after_load = torch.cuda.mem_get_info()[0] if torch.cuda.is_available() else 0
        self.calibration_info = self.calibrate_specs(
            self.model, self._config.gpu_mem_limit_mb, free_before_load, free_after_load
        )

        # 5. Start dynamic batch processor background task
        self.worker_task = asyncio.create_task(self._batch_processor())
        self.is_initialized = True
        logger.info(f"✅ [PARAKEET] Model loaded from '{model_dir}' and calibrated in {time.time() - t0:.2f}s!")

    async def shutdown(self) -> None:
        """Cancel background batch worker and release system resources."""
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        self.is_initialized = False
        self.model = None
        logger.info("🛑 [PARAKEET] Parakeet ASR engine shut down.")

    def health_status(self) -> Dict[str, Any]:
        """
        Return component health status for system monitoring and FastAPI health checks.
        Analogous to GipformerService.health_status().
        """
        model_dir = resolve_parakeet_model_path(self._config.model_path)
        has_cuda = "CUDAExecutionProvider" in rt.get_available_providers()
        is_ready = self.is_initialized and (self.model is not None)

        if not self._config.enabled:
            return {
                "status": "healthy",
                "enabled": False,
                "message": "Parakeet engine is disabled in configuration",
            }

        if not is_ready:
            try:
                validate_parakeet_model_files(model_dir)
                return {
                    "status": "healthy",
                    "initialized": False,
                    "model_dir": str(model_dir),
                    "cuda_available": has_cuda,
                    "message": "Model files present; engine not yet started",
                }
            except Exception as e:
                return {
                    "status": "unhealthy",
                    "initialized": False,
                    "model_dir": str(model_dir),
                    "error": str(e),
                }

        return {
            "status": "healthy",
            "initialized": True,
            "model_dir": str(model_dir),
            "cuda_available": has_cuda,
            "queue_size": self.request_queue.qsize(),
            "calibration": self.calibration_info,
        }

    # --------------------------------------------------------------------------
    # Batch Processing & Execution with OOM Protection
    # --------------------------------------------------------------------------

    async def _process_batch(self, batch: List[Dict[str, Any]]) -> None:
        """
        Execute GPU inference on a batch with Layer 2 OOM Protection and auto-retry.
        """
        self.batch_sequence_counter += 1
        current_batch_id = self.batch_sequence_counter
        current_items = list(batch)
        job_service = get_async_job_service()

        # Update all async jobs to 'inprogress'
        for item in current_items:
            if item.get("is_async"):
                await job_service.update_status(item["job_id"], JobStatus.INPROGRESS)

        while current_items:
            files = [item["file_path"] for item in current_items]
            results_list = [None] * len(current_items)
            t_start = time.time()

            try:
                def _transcribe():
                    return self.model.with_timestamps().recognize(files)

                hypotheses = await asyncio.to_thread(_transcribe)
                batch_duration = time.time() - t_start
                logger.info(
                    f"⚡ [GPU - FP16] Batch #{current_batch_id} ({len(current_items)} items) "
                    f"completed in {batch_duration:.3f}s ({batch_duration / len(current_items):.3f}s/audio)"
                )

                for i, hyp in enumerate(hypotheses):
                    tokens = getattr(hyp, "tokens", None)
                    timestamps = getattr(hyp, "timestamps", None)
                    segments = extract_segments(tokens, timestamps)

                    results_list[i] = {
                        "status": "success",
                        "data": {
                            "text": hyp.text,
                            "segments": segments,
                        },
                        "batch_id": current_batch_id,
                        "batch_size": len(current_items),
                        "batch_proc_time": round(batch_duration, 3),
                    }

                # Dispatch results to sync events & async Redis store
                for i, item in enumerate(current_items):
                    item["result"] = results_list[i]
                    if item.get("is_async"):
                        await job_service.update_status(
                            item["job_id"],
                            status=JobStatus.DONE,
                            result=results_list[i]["data"],
                            duration=item.get("duration", 0.0),
                            processing_time=round(batch_duration, 3),
                        )
                    if item.get("event"):
                        item["event"].set()
                    cleanup_temp_files(*(item.get("temp_paths") or []))
                return

            except Exception as e:
                err_msg = str(e).lower()
                is_oom = any(s in err_msg for s in (
                    "out of memory", "cuda failure", "failed to allocate",
                    "is smaller than requested bytes", "bfc_arena", "allocaterawinternal"
                ))

                if is_oom and len(current_items) > 1:
                    # Layer 2 OOM Protection: Evict last item back to head of queue
                    evicted_item = current_items.pop()
                    self.request_queue._queue.appendleft(evicted_item)
                    logger.warning(
                        f"⚠️ [VRAM WARNING] Memory ceiling reached! Evicted 1 request to next batch. "
                        f"Retrying immediately with batch size = {len(current_items)}..."
                    )
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    continue
                else:
                    logger.error(f"Batch inference failed: {e}", exc_info=True)
                    error_desc = "VRAM allocation limit exceeded" if is_oom else str(e)
                    for item in current_items:
                        item["result"] = {"status": "error", "message": error_desc}
                        if item.get("is_async"):
                            await job_service.update_status(
                                item["job_id"],
                                status=JobStatus.FAILED,
                                error_message=error_desc,
                            )
                        if item.get("event"):
                            item["event"].set()
                        cleanup_temp_files(*(item.get("temp_paths") or []))
                    return

    # --------------------------------------------------------------------------
    # Background Batch Dispatcher Loop
    # --------------------------------------------------------------------------

    async def _batch_processor(self) -> None:
        """Background worker: dynamic batch collection with adaptive waiting window."""
        while True:
            try:
                # 1. Await next incoming request
                first_item = await self.request_queue.get()
                batch = [first_item]
                wait_deadline = time.time() + self._config.max_wait_time
                batch_full = False

                # 2. Adaptive gathering window for concurrent arrivals
                while len(batch) < self._config.max_batch_size and not batch_full:
                    while len(batch) < self._config.max_batch_size and not self.request_queue.empty():
                        candidate = self.request_queue._queue[0]
                        if self.can_fit_in_batch(batch, candidate):
                            batch.append(self.request_queue.get_nowait())
                        else:
                            batch_full = True
                            break

                    if batch_full or len(batch) >= self._config.max_batch_size:
                        break

                    now = time.time()
                    if now >= wait_deadline and self.in_flight_converting == 0 and self.request_queue.empty():
                        break

                    # Extend window slightly if requests are currently converting in FFmpeg
                    if self.in_flight_converting > 0 and (wait_deadline - now) < 0.1:
                        wait_deadline = min(wait_deadline + 0.15, now + 0.4)

                    await asyncio.sleep(0.02)

                logger.info(f"🚀 [Dynamic Batching] Processing batch #{self.batch_sequence_counter + 1} with {len(batch)} items")
                await self._process_batch(batch)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in Parakeet batch worker: {e}", exc_info=True)


def get_parakeet_engine() -> ParakeetEngine:
    """Convenience getter for singleton ParakeetEngine instance."""
    return ParakeetEngine()
