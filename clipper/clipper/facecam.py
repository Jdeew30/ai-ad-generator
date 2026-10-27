from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

from .config import ASSETS_DIR
from .media import MediaInfo

log = logging.getLogger(__name__)

MODEL = ASSETS_DIR / "models" / "face_detection_yunet_2023mar.onnx"

OUT_W, OUT_H = 1080, 1920
SPLIT_TOP = 768  # facecam panel height in the split layout; the gameplay gets the rest
FULLCAM_FACE = 0.2  # face taller than this share of the frame: the camera *is* the stream


@dataclass(frozen=True)
class FaceStats:
    cx: float  # median face centre, fractions of the frame
    cy: float
    w: float  # median face size, fractions of the frame
    h: float
    hits: float  # share of sampled frames where the face was found at that spot


Box = tuple[int, int, int, int]  # x, y, w, h in source pixels


@dataclass(frozen=True)
class LayoutPlan:
    kind: str  # "split" | "crop" | "blur"
    face_box: Box | None = None  # split: facecam panel source
    game_box: Box | None = None  # split: gameplay panel source
    crop_box: Box | None = None  # crop: whole-frame source
    face_band: tuple[int, int] | None = None  # crop: rows of the output covered by the face (text avoids them)
    reason: str = ""


Face = tuple[float, float, float, float]  # cx, cy, w, h as fractions of the frame


def faces_per_frame(frames: list[Path], *, min_score: float = 0.75) -> list[tuple[Path, Face | None]]:
    """Largest face of each frame (YuNet), or None."""
    import cv2

    try:  # silence the DNN backend's "targets are not supported" warning
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:
        pass
    results: list[tuple[Path, Face | None]] = []
    detector = None
    for frame_path in frames:
        image = cv2.imread(str(frame_path))
        if image is None:
            continue
        height, width = image.shape[:2]
        if detector is None:
            detector = cv2.FaceDetectorYN.create(str(MODEL), "", (width, height), min_score, 0.3, 50)
        detector.setInputSize((width, height))
        _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            results.append((frame_path, None))
            continue
        x, y, w, h = (float(v) for v in max(faces, key=lambda f: f[2] * f[3])[:4])
        results.append((frame_path, ((x + w / 2) / width, (y + h / 2) / height, w / width, h / height)))
    return results


def detect_faces(frames: list[Path], *, min_score: float = 0.75) -> list[Face]:
    """Largest face of each frame where one was found."""
    return [face for _, face in faces_per_frame(frames, min_score=min_score) if face is not None]


def best_frame(frames: list[Path]) -> Path | None:
    """The frame showing the biggest face (thumbnails with a face get more clicks), else the middle one."""
    if not frames:
        return None
    scored = [(face[2] * face[3], path) for path, face in faces_per_frame(frames) if face is not None]
    return max(scored)[1] if scored else frames[len(frames) // 2]


def face_stats(faces: list[Face], samples: int) -> FaceStats | None:
    """A stable face: found in at least half the frames, at roughly the same place."""
    if samples <= 0 or len(faces) < max(2, samples // 2):
        return None
    cx = statistics.median(f[0] for f in faces)
    cy = statistics.median(f[1] for f in faces)
    near = [f for f in faces if abs(f[0] - cx) <= 0.08 and abs(f[1] - cy) <= 0.1]
    hits = len(near) / samples
    if hits < 0.5:
        return None
    return FaceStats(
        cx=statistics.median(f[0] for f in near),
        cy=statistics.median(f[1] for f in near),
        w=statistics.median(f[2] for f in near),
        h=statistics.median(f[3] for f in near),
        hits=hits,
    )


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def _fit_box(cx: float, cy: float, w: float, h: float, width: int, height: int) -> Box:
    """A w x h box centred on (cx, cy), shrunk and shifted to stay inside the frame (even values)."""
    scale = min(1.0, width / w, height / h)
    box_w = min(_even(w * scale), width - width % 2)
    box_h = min(_even(h * scale), height - height % 2)
    x = int(min(max(cx - box_w / 2, 0), width - box_w)) // 2 * 2
    y = int(min(max(cy - box_h / 2, 0), height - box_h)) // 2 * 2
    return (x, y, box_w, box_h)


def _aspect_box(target_aspect: float, cx: float, width: int, height: int) -> Box:
    """Tallest box of the target aspect (w/h), horizontally centred on cx."""
    if width / height > target_aspect:
        h = height
        w = height * target_aspect
    else:
        w = width
        h = width / target_aspect
    return _fit_box(cx, height / 2, w, h, width, height)


def plan_layout(info: MediaInfo, faces: FaceStats | None, *, requested: str = "auto", facecam: list[float] | None = None) -> LayoutPlan:
    width, height = info.width, info.height
    if width <= 0 or height <= 0:
        return LayoutPlan("blur", reason="dimensions inconnues")
    if info.aspect <= 0.8:  # already vertical: just fill the frame
        return LayoutPlan("crop", crop_box=_aspect_box(OUT_W / OUT_H, width / 2, width, height), reason="source verticale")

    top_aspect = OUT_W / SPLIT_TOP
    bottom_aspect = OUT_W / (OUT_H - SPLIT_TOP)

    def split(face_box: Box, reason: str) -> LayoutPlan:
        game = _aspect_box(bottom_aspect, width / 2, width, height)
        return LayoutPlan("split", face_box=face_box, game_box=game, reason=reason)

    if facecam is not None and requested in {"auto", "split"}:
        fx, fy, fw, fh = facecam
        box_w, box_h = max(fw * width, 2.0), max(fh * height, 2.0)
        if box_w / box_h > top_aspect:  # grow the short side to the panel's aspect
            box_h = box_w / top_aspect
        else:
            box_w = box_h * top_aspect
        box = _fit_box((fx + fw / 2) * width, (fy + fh / 2) * height, box_w, box_h, width, height)
        return split(box, "facecam configurée")
    if requested == "blur":
        return LayoutPlan("blur", reason="demandé dans la config")

    if faces is None:
        if requested in {"split", "crop"}:
            log.info("Aucun visage stable détecté : mise en page « blur » au lieu de « %s »", requested)
        return LayoutPlan("blur", reason="aucun visage stable")

    face_px_h = faces.h * height
    if requested == "crop" or (requested == "auto" and faces.h >= FULLCAM_FACE):
        box = _aspect_box(OUT_W / OUT_H, faces.cx * width, width, height)
        scale = OUT_H / box[3]
        top = (faces.cy * height - face_px_h / 2 - box[1]) * scale
        bottom = (faces.cy * height + face_px_h / 2 - box[1]) * scale
        band = (max(0, int(top)), min(OUT_H, int(bottom)))
        return LayoutPlan("crop", crop_box=box, face_band=band, reason=f"caméra plein cadre (visage {faces.h:.0%} de la hauteur)")
    # Facecam overlay: frame head and shoulders, face a bit above the panel centre.
    box_h = face_px_h * 2.6
    box_w = box_h * top_aspect
    box_w = min(box_w, width * 0.5)
    box_h = box_w / top_aspect
    box = _fit_box(faces.cx * width, faces.cy * height + box_h * 0.08, box_w, box_h, width, height)
    return split(box, f"facecam détectée (visage {faces.h:.0%} de la hauteur)")
