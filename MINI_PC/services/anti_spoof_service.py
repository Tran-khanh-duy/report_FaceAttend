"""
services/anti_spoof_service.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Wrapper to bridge the new LivenessDetector into the existing Edge Box pipeline.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import logging
from config import anti_spoof_config
from core.liveness_detector import LivenessDetector

logger = logging.getLogger(__name__)

class AntiSpoofServiceWrapper:
    """
    Acts as a singleton bridge between the global `anti_spoof_config` and the 
    new `LivenessDetector` module.
    """
    
    def __init__(self):
        self.available = False
        self.detector = None
        
        if not anti_spoof_config.enabled:
            logger.info("ℹ️ [AntiSpoof] Disabled in config.")
            return

        try:
            # Instantiate the production-ready liveness detector
            self.detector = LivenessDetector.from_config(anti_spoof_config)
            self.available = self.detector.available
        except Exception as e:
            logger.error(f"❌ [AntiSpoof] Failed to initialize LivenessDetector: {e}")

    def is_real(self, frame, bbox, track_id="default"):
        """
        Public API called by `headless_processor.py`.
        
        Args:
            frame: Full BGR frame (numpy ndarray).
            bbox: Bounding box tuple (x1, y1, x2, y2).
            track_id: Unique identifier for the face across frames (student_id).
            
        Returns:
            Tuple (is_real: bool, spoof_score: float)
        """
        if not self.available or self.detector is None:
            return True, 1.0  # Fail-open
            
        return self.detector.predict(frame, bbox, track_id=track_id)
        
    def reset_track(self, track_id):
        """Reset history for a track when a face is lost."""
        if self.detector:
            self.detector.reset_track(track_id)

# Create singleton instance
anti_spoof_service = AntiSpoofServiceWrapper()
