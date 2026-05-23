"""
core/liveness_detector.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Production-Ready Anti-Spoofing Module — LivenessDetector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Root Causes Fixed:
  1. TIGHT CROP  → Replaced with get_contextual_crop() (scale_factor=2.7)
                   Gives the model phone bezels, paper edges, screen glare.
  2. SINGLE-FRAME → Replaced with a collections.deque(maxlen=10) sliding window.
                   Final verdict is the moving average, not a single noisy score.
  3. SLOW PyTorch → Wrapped in ONNX Runtime (InferenceSession) for max CPU speed.
                   Falls back to the original PyTorch MiniFASNet if ONNX is absent.

Public API (drop-in replacement for the old anti_spoof_service):
  detector = LivenessDetector(model_dir, threshold=0.85)
  is_real, score = detector.predict(frame, bbox, track_id)

  - frame    : full BGR OpenCV frame (numpy ndarray)
  - bbox     : (x1, y1, x2, y2) from InsightFace/SCRFD detector
  - track_id : any hashable key that identifies a face across frames
               (e.g. student_id string, or a ByteTrack integer ID)

Integration in _recognize_loop (headless_processor.py):
  Replace:
      is_real, spoof_score = anti_spoof_service.is_real(frame, res.bbox)
  With:
      is_real, spoof_score = liveness_detector.predict(frame, res.bbox, res.student_id)
"""

import os
import sys
import cv2
import numpy as np
import logging
from collections import deque
from pathlib import Path
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MiniFASNet model filename convention:
#   "<scale>_<H>x<W>_<ModelType>.pth"  e.g. "2.7_80x80_MiniFASNetV2.pth"
# ─────────────────────────────────────────────────────────────────────────────
_MINIFAS_INPUT_H = 80
_MINIFAS_INPUT_W = 80


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — Contextual Face Cropping
# ─────────────────────────────────────────────────────────────────────────────
def get_contextual_crop(
    frame: np.ndarray,
    bbox: Tuple[int, int, int, int],
    scale_factor: float = 2.7,
    out_size: Tuple[int, int] = (_MINIFAS_INPUT_W, _MINIFAS_INPUT_H),
) -> Optional[np.ndarray]:
    """
    Expand a tight face bounding box by `scale_factor` and crop from the full
    frame, then resize to `out_size` for model inference.

    WHY scale_factor=2.7?
      This is the default scale used by the original Silent-Face-Anti-Spoofing
      research paper. At this scale the model sees:
        - The face itself
        - The phone/tablet bezel surrounding a displayed photo
        - Screen glare artefacts on printed paper
        - Colour/lighting inconsistencies that indicate a flat medium

    Args:
        frame       : Full BGR frame (H x W x 3 uint8).
        bbox        : (x1, y1, x2, y2) pixel coords from face detector.
        scale_factor: How much to expand the bounding box (default 2.7).
        out_size    : (width, height) to resize the crop before inference.

    Returns:
        Resized BGR crop (uint8) or None if the bbox is degenerate.
    """
    if frame is None or frame.ndim < 2:
        return None

    src_h, src_w = frame.shape[:2]
    x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])

    # Convert (x1,y1,x2,y2) → (x, y, w, h) for centred-scale calculation
    box_w = x2 - x1
    box_h = y2 - y1

    if box_w <= 0 or box_h <= 0:
        return None

    # Clamp scale so we never exceed the frame boundary
    effective_scale = min(scale_factor,
                          (src_w - 1) / max(box_w, 1),
                          (src_h - 1) / max(box_h, 1))

    new_w = box_w * effective_scale
    new_h = box_h * effective_scale

    cx = x1 + box_w / 2.0
    cy = y1 + box_h / 2.0

    lx = int(max(0, cx - new_w / 2))
    ly = int(max(0, cy - new_h / 2))
    rx = int(min(src_w - 1, cx + new_w / 2))
    ry = int(min(src_h - 1, cy + new_h / 2))

    if rx <= lx or ry <= ly:
        return None

    crop = frame[ly:ry, lx:rx]
    if crop.size == 0:
        return None

    return cv2.resize(crop, out_size, interpolation=cv2.INTER_LINEAR)


# ─────────────────────────────────────────────────────────────────────────────
# ONNX Runtime inference wrapper (fast CPU path)
# ─────────────────────────────────────────────────────────────────────────────
class _OnnxModel:
    """
    Wraps an ONNX export of MiniFASNet for CPU-optimised inference via
    onnxruntime.InferenceSession.

    Expected ONNX output: softmax probabilities [batch, 3]
      index 0 = spoof score
      index 1 = real score (we use this)
    """

    def __init__(self, onnx_path: str):
        import onnxruntime as ort  # imported lazily — no crash if absent

        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = 2      # Keep CPU footprint small
        sess_opts.inter_op_num_threads = 2
        sess_opts.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        self.session = ort.InferenceSession(
            onnx_path,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        logger.info(f"✅ [LivenessDetector] ONNX model loaded: {Path(onnx_path).name}")

    def infer(self, img_bgr: np.ndarray) -> float:
        """
        Run one forward pass.

        Args:
            img_bgr: (80 x 80 x 3) uint8 BGR crop.

        Returns:
            real_score in [0, 1].  Higher = more likely real.
        """
        # Normalise to float32 in [0, 1], channels-first (NCHW)
        x = img_bgr.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))          # HWC → CHW
        x = np.expand_dims(x, axis=0)            # add batch dim → (1, 3, 80, 80)

        outputs = self.session.run(None, {self.input_name: x})
        probs = outputs[0][0]  # shape (3,) — softmax already applied

        # MiniFASNet output convention: [spoof, real, unsure]
        # We return the "real" probability (index 1).
        real_score = float(probs[1]) if len(probs) >= 2 else float(probs[0])
        return real_score


# ─────────────────────────────────────────────────────────────────────────────
# PyTorch MiniFASNet fallback
# ─────────────────────────────────────────────────────────────────────────────
class _TorchModel:
    """
    Fallback: original PyTorch MiniFASNet from the Silent-Face repo.
    Used when an ONNX export is not available.
    """

    def __init__(self, model_path: str, silent_face_root: str):
        # Add the Silent-Face src/ to sys.path so imports resolve
        src_dir = os.path.join(silent_face_root, "src")
        if src_dir not in sys.path:
            sys.path.insert(0, silent_face_root)

        import torch
        import torch.nn.functional as F
        from src.anti_spoof_predict import AntiSpoofPredict
        from src.utility import parse_model_name, get_kernel
        from src.model_lib.MiniFASNet import (
            MiniFASNetV1, MiniFASNetV2,
            MiniFASNetV1SE, MiniFASNetV2SE,
        )
        from src.data_io import transform as trans

        self._torch = torch
        self._F = F
        self._trans = trans
        self._model_path = model_path

        model_name = os.path.basename(model_path)
        h_in, w_in, model_type, _ = parse_model_name(model_name)
        kernel = get_kernel(h_in, w_in)

        _MAP = {
            "MiniFASNetV1":   MiniFASNetV1,
            "MiniFASNetV2":   MiniFASNetV2,
            "MiniFASNetV1SE": MiniFASNetV1SE,
            "MiniFASNetV2SE": MiniFASNetV2SE,
        }
        self.device = torch.device("cpu")
        self.model = _MAP[model_type](conv6_kernel=kernel).to(self.device)

        state = torch.load(model_path, map_location=self.device)
        # Strip "module." prefix if saved from DataParallel
        from collections import OrderedDict
        clean = OrderedDict()
        for k, v in state.items():
            clean[k[7:] if k.startswith("module.") else k] = v
        self.model.load_state_dict(clean)
        self.model.eval()
        logger.info(f"✅ [LivenessDetector] PyTorch fallback loaded: {model_name}")

    def infer(self, img_bgr: np.ndarray) -> float:
        from src.data_io import transform as trans
        transform = trans.Compose([trans.ToTensor()])
        tensor = transform(img_bgr).unsqueeze(0).to(self.device)
        with self._torch.no_grad():
            out = self._F.softmax(self.model(tensor), dim=1).cpu().numpy()[0]
        # Same convention: index 1 = real probability
        return float(out[1]) if len(out) >= 2 else float(out[0])


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 + 3 — LivenessDetector  (Temporal Smoothing + SOTA Model)
# ─────────────────────────────────────────────────────────────────────────────
class LivenessDetector:
    """
    Production-ready Anti-Spoofing module.

    Key design decisions:
      • Contextual Crop  : scale_factor=2.7 captures surrounding medium context.
      • Temporal Window  : deque(maxlen=10) per track_id smooths per-frame noise.
      • Strict Threshold : avg_score > 0.85 before declaring "Real".
      • Dual-Model       : tries ONNX first for CPU speed; falls back to PyTorch.
      • Per-Track State  : separate deque per face ID = multi-face safe.
      • Thread-Safe Read : deque reads/appends are GIL-protected in CPython.

    Usage:
        detector = LivenessDetector.from_config(anti_spoof_config)
        is_real, avg_score = detector.predict(frame, bbox, track_id="student_007")
    """

    def __init__(
        self,
        model_dir: str,
        silent_face_root: Optional[str] = None,
        scale_factor: float = 2.5,
        window_size: int = 15,
        real_threshold: float = 0.92,
        spoof_threshold: float = 0.2,
    ):
        """
        Args:
            model_dir         : Directory containing .onnx or .pth model files.
            silent_face_root  : Path to Silent-Face-Anti-Spoofing-master/
                                (needed only for the PyTorch fallback path).
            scale_factor      : Contextual crop expansion (default 2.5).
            window_size       : Sliding window depth (default 15 frames).
            real_threshold    : Minimum moving-average score to declare Real (> 0.92).
            spoof_threshold   : Maximum score to confirm a definitive Spoof (< 0.2).
        """
        self.scale_factor    = scale_factor
        self.window_size     = window_size
        self.real_threshold  = real_threshold
        self.spoof_threshold = spoof_threshold
        self.available       = False

        # TASK 2: Per-track score history → {track_id: deque(maxlen=window_size)}
        self._score_history: Dict[Any, deque] = {}

        # TASK 3: Load model (ONNX preferred, PyTorch fallback)
        self._model = self._load_model(model_dir, silent_face_root)
        if self._model is not None:
            self.available = True
            logger.info(
                f"🚀 [LivenessDetector] Ready | scale={scale_factor} "
                f"| window={window_size} | threshold={real_threshold}"
            )
        else:
            logger.error("❌ [LivenessDetector] No model loaded — detector disabled.")

    # ── Factory ──────────────────────────────────────────────────────────────
    @classmethod
    def from_config(cls, cfg) -> "LivenessDetector":
        """
        Build a LivenessDetector from an AntiSpoofConfig dataclass.

        Example:
            from config import anti_spoof_config
            detector = LivenessDetector.from_config(anti_spoof_config)
        """
        base = Path(__file__).parent.parent
        silent_root = str(base / "Silent-Face-Anti-Spoofing-master")
        return cls(
            model_dir=str(cfg.model_dir),
            silent_face_root=silent_root,
            real_threshold=float(cfg.threshold),
        )

    # ── Model Loader ─────────────────────────────────────────────────────────
    def _load_model(self, model_dir: str, silent_face_root: Optional[str]):
        """
        Priority:
          1. Any .onnx file in model_dir  (fast ONNX Runtime)
          2. Any .pth  file in model_dir  (PyTorch fallback)
        Loads only the FIRST matching file found (single-model inference).
        """
        if not model_dir or not os.path.isdir(model_dir):
            logger.error(f"[LivenessDetector] model_dir not found: {model_dir}")
            return None

        # ── ONNX path ────────────────────────────────────────────────────────
        onnx_files = sorted(Path(model_dir).glob("*.onnx"))
        if onnx_files:
            try:
                return _OnnxModel(str(onnx_files[0]))
            except ImportError:
                logger.warning(
                    "[LivenessDetector] onnxruntime not installed. "
                    "Run: pip install onnxruntime  — trying PyTorch fallback."
                )
            except Exception as e:
                logger.warning(f"[LivenessDetector] ONNX load failed ({e}) — trying PyTorch.")

        # ── PyTorch fallback ─────────────────────────────────────────────────
        pth_files = sorted(Path(model_dir).glob("*.pth"))
        if not pth_files:
            logger.error(f"[LivenessDetector] No .onnx or .pth model in: {model_dir}")
            return None

        if not silent_face_root or not os.path.isdir(silent_face_root):
            logger.error(
                "[LivenessDetector] silent_face_root required for PyTorch path. "
                f"Got: {silent_face_root}"
            )
            return None

        try:
            return _TorchModel(str(pth_files[0]), silent_face_root)
        except Exception as e:
            logger.exception(f"[LivenessDetector] PyTorch fallback failed: {e}")
            return None

    # ── TASK 2: Sliding Window Management ────────────────────────────────────
    def _get_deque(self, track_id: Any) -> deque:
        """Return (or create) the score history deque for a given track_id."""
        if track_id not in self._score_history:
            self._score_history[track_id] = deque(maxlen=self.window_size)
        return self._score_history[track_id]

    def reset_track(self, track_id: Any) -> None:
        """
        Clear the score history for a specific face track.
        Call this when a tracked face disappears from the frame.

        Example (in _recognize_loop):
            if face_no_longer_visible:
                liveness_detector.reset_track(res.student_id)
        """
        self._score_history.pop(track_id, None)

    def reset_all(self) -> None:
        """Clear ALL per-track histories. Call when a session ends."""
        self._score_history.clear()

    # ── Core Prediction ──────────────────────────────────────────────────────
    def predict(
        self,
        frame: np.ndarray,
        bbox: Tuple[int, int, int, int],
        track_id: Any = "default",
    ) -> Tuple[bool, bool, float]:
        """
        Run liveness prediction with temporal smoothing.

        Steps:
          1. get_contextual_crop() → expanded crop (Task 1)
          2. Model inference       → single-frame real_score in [0, 1]
          3. Append to deque       → sliding window (Task 2)
          4. Compute moving average → avg_score
          5. avg_score > threshold → True (Real) / False (Spoof)

        Args:
            frame    : Full BGR frame from OpenCV (numpy uint8).
            bbox     : (x1, y1, x2, y2) from face detector/tracker.
            track_id : Unique face identifier (student_id, ByteTrack ID, etc.)

        Returns:
            (is_real: bool, is_definitive_spoof: bool, avg_score: float)
              is_real            = True  → Real face, allow attendance.
              is_real            = False → Possible spoof, block attendance.
              is_definitive_spoof = True → Confirmed spoof (avg < 0.2), trigger Telegram.
              avg_score                  → Moving-average confidence (for logging).
        """
        if not self.available or self._model is None:
            # Fail-open: if detector is unavailable, allow the face through.
            return True, False, 1.0

        history = self._get_deque(track_id)

        try:
            # ── TASK 1: Contextual crop ───────────────────────────────────
            crop = get_contextual_crop(
                frame, bbox,
                scale_factor=self.scale_factor,
                out_size=(_MINIFAS_INPUT_W, _MINIFAS_INPUT_H),
            )
            if crop is None:
                # Bad bbox — keep previous verdict, don't poison the window
                if history:
                    avg = sum(history) / len(history)
                    is_real = avg > self.real_threshold
                    is_definitive_spoof = avg < self.spoof_threshold
                    return is_real, is_definitive_spoof, avg
                return True, False, 1.0   # No data yet → fail-open

            # ── TASK 3: Model inference ───────────────────────────────────
            real_score = self._model.infer(crop)

            # ── TASK 2: Append to sliding window ─────────────────────────
            history.append(real_score)

            # ── Compute moving average and apply strict threshold ─────────
            avg_score = sum(history) / len(history)

            # CRITICAL FIX: Always use the avg_score to determine is_real,
            # even during warmup. The previous code returned is_real=True
            # unconditionally during warmup, allowing spoofs to slip through
            # the 3-frame accumulation gate and get checked in before the
            # window was full enough to detect them.
            #
            # Now: if the current avg is below the real_threshold, is_real=False
            # immediately, blocking attendance from frame 1.
            #
            # is_definitive_spoof (for Telegram alerts) still requires a
            # minimum of 5 frames to avoid false alarms on brief glitches.
            is_real = avg_score > self.real_threshold

            window_full_enough = len(history) >= max(3, self.window_size // 3)
            is_definitive_spoof = (avg_score < self.spoof_threshold) and window_full_enough

            logger.debug(
                f"[LivenessDetector] track='{track_id}' | "
                f"frame_score={real_score:.3f} | avg={avg_score:.3f} | "
                f"window={len(history)}/{self.window_size} | "
                f"is_real={is_real} | definitive_spoof={is_definitive_spoof}"
            )

            return is_real, is_definitive_spoof, avg_score

        except Exception as e:
            logger.error(f"[LivenessDetector] predict() error for track '{track_id}': {e}")
            # On any crash → fail-open so attendance isn't silently broken
            return True, False, 1.0
