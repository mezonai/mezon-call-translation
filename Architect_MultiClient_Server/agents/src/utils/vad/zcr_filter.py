import numpy as np
import librosa
from dataclasses import dataclass
from typing import Tuple, Optional
from src.utils.error_handling import VADError, ErrorContext, ErrorSeverity, with_error_handling
from src.services.metrics_service import MetricsService

@dataclass
class ZCRStats:
    """Statistics for ZCR analysis"""
    zcr: float
    zcr_ma: float
    energy: float
    is_speech: bool
    confidence: float

class EnhancedZCRFilter:
    """Enhanced Zero Crossing Rate Filter with better error handling and monitoring"""
    
    def __init__(self, 
                 zcr_thresh: Tuple[float, float] = (0.02, 0.2),
                 ma_window: int = 8,
                 analysis_duration_ms: int = 30):
        """
        Initialize ZCR Filter
        
        Args:
            zcr_thresh: (low, high) thresholds for zero crossing rate
            energy_thresh: minimum energy threshold
            ma_window: size of moving average window
            analysis_duration_ms: duration of analysis window in milliseconds
        """
        self.zcr_thresh = zcr_thresh
        self.analysis_duration_ms = analysis_duration_ms
        self.ma_window = max(ma_window, 3)
        self.history = []
        
        # Metrics
        self.metrics = MetricsService.get_instance()
        
        # Validate configuration
        if not self._validate_config():
            raise VADError(
                "Invalid ZCR Filter configuration",
                ErrorContext.create(
                    "ZCRFilter",
                    "init",
                    ErrorSeverity.HIGH,
                    {
                        "zcr_thresh": zcr_thresh,
                        "ma_window": ma_window
                    }
                )
            )
    
    def _validate_config(self) -> bool:
        """Validate filter configuration"""
        if not (0 <= self.zcr_thresh[0] < self.zcr_thresh[1] <= 1.0):
            return False
        if not (0 <= self.energy_thresh <= 1.0):
            return False
        if self.ma_window < 3:
            return False
        return True
    
    @with_error_handling("ZCRFilter", "check", ErrorSeverity.MEDIUM, VADError)
    def check(self, chunk: np.ndarray) -> Tuple[bool, ZCRStats]:
        """
        Check if audio chunk contains speech
        
        Returns:
            Tuple of (is_speech, stats)
        """
        if len(chunk) == 0:
            raise VADError(
                "Empty audio chunk",
                ErrorContext.create(
                    "ZCRFilter",
                    "check",
                    ErrorSeverity.LOW,
                    {"chunk_size": 0}
                )
            )

        # Calculate ZCR
        frame_length = min(len(chunk), 1024)
        zcr = np.mean(librosa.feature.zero_crossing_rate(chunk, frame_length=frame_length)[0])
        

        # Update moving average
        self.history.append(zcr)
        if len(self.history) > self.ma_window:
            self.history.pop(0)
        zcr_ma = np.mean(self.history)

        # Make speech decision
        is_speech = (
            self.zcr_thresh[0] <= zcr_ma <= self.zcr_thresh[1]
        )

        # Calculate confidence
        confidence = self._calculate_confidence(zcr, zcr_ma)
        
        # Track metrics
        self._update_metrics(zcr, zcr_ma, is_speech, confidence)
        
        stats = ZCRStats(
            zcr=zcr,
            zcr_ma=zcr_ma,
            is_speech=is_speech,
            confidence=confidence
        )
        
        return is_speech, stats
    
    def _calculate_confidence(self, zcr: float, zcr_ma: float) -> float:
        """Calculate confidence score for the decision"""
        if len(self.history) < 2:
            return 0.5
            
        # Calculate ZCR stability
        zcr_std = np.std(self.history)
        max_std = 0.1
        stability = max(0, 1 - (zcr_std / max_std))
        
        # Calculate distance from thresholds
        lower, upper = self.zcr_thresh
        if lower <= zcr_ma <= upper:
            # In speech range
            mid_point = (lower + upper) / 2
            distance_from_center = abs(zcr_ma - mid_point) / (upper - lower) * 2
            boundary_confidence = 1 - distance_from_center
        else:
            # Outside speech range
            if zcr_ma < lower:
                boundary_confidence = min(1.0, (lower - zcr_ma) / lower)
            else:
                boundary_confidence = min(1.0, (zcr_ma - upper) / upper)
        
        return (stability + boundary_confidence) / 2
    
    def _update_metrics(self, zcr: float, zcr_ma: float, energy: float, 
                       is_speech: bool, confidence: float):
        """Update metrics"""
        self.metrics.track("vad.zcr", zcr)
        self.metrics.track("vad.zcr_ma", zcr_ma)
        self.metrics.track("vad.energy", energy)
        self.metrics.track("vad.is_speech", 1 if is_speech else 0)
        self.metrics.track("vad.confidence", confidence)
    
    def reset(self):
        """Reset filter state"""
        self.history.clear()
