import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import cv2  # type: ignore

    _HAS_CV2 = True
except Exception:
    cv2 = None
    _HAS_CV2 = False

try:
    from .scrfd import SCRFD

    _HAS_SCRFD = True
except Exception as e:
    SCRFD = None
    _HAS_SCRFD = False
    _SCRFD_IMPORT_ERROR = e
else:
    _SCRFD_IMPORT_ERROR = None

logger = logging.getLogger("kiosk")

_SCRFD_FACE_DETECTOR = None
_SCRFD_MODEL_PATH = Path("models/face/scrfd_2.5g_bnkps.onnx")


def _get_scrfd_face_detector():
    global _SCRFD_FACE_DETECTOR

    if not _HAS_SCRFD:
        logger.warning(f"SCRFD import failed: {_SCRFD_IMPORT_ERROR}")
        return None

    if _SCRFD_FACE_DETECTOR is None:
        try:
            if not _SCRFD_MODEL_PATH.exists():
                logger.warning(f"SCRFD model file not found: {_SCRFD_MODEL_PATH}")
                return None

            _SCRFD_FACE_DETECTOR = SCRFD(model_file=str(_SCRFD_MODEL_PATH))
            _SCRFD_FACE_DETECTOR.prepare(
                0,
                nms_thresh=0.85,
                input_size=(640, 640),
            )
        except Exception as e:
            logger.warning(f"Failed to init SCRFD FaceDetector: {e}")
            _SCRFD_FACE_DETECTOR = None

    return _SCRFD_FACE_DETECTOR


def save_face_image(
    reg_folder: Path,
    face_image_data: Optional[str],
    save_image_func,
) -> Optional[str]:
    """データ URL から顔画像を保存します。"""
    if not isinstance(face_image_data, str) or "base64," not in face_image_data:
        return None

    try:
        filename = save_image_func(reg_folder, "face", face_image_data)
        return filename
    except Exception as e:
        logger.warning(f"Failed to save face image: {e}")
        return None


def detect_persons_in_frame(
    frame_bytes: bytes,
    yolo_model,
    has_yolo: bool,
    yolo_device: str = "cpu",
) -> tuple[Optional[dict], list[dict], Optional[dict], float]:
    """YOLO を使用してフレーム内の人物を検出します。"""
    if not _HAS_CV2:
        logger.warning("OpenCV is not available")
        return None, [], None, 0.0

    frame_size = None
    boxes = []
    best = None
    t_yolo_start = time.time()

    try:
        bytes_arr = np.frombuffer(frame_bytes, np.uint8)
        img_bgr = cv2.imdecode(bytes_arr, cv2.IMREAD_COLOR)

        if img_bgr is None:
            logger.warning("Failed to decode frame")
            return None, [], None, time.time() - t_yolo_start

        h, w = img_bgr.shape[:2]
        frame_size = {"w": w, "h": h}

        if has_yolo and yolo_model is not None:
            res = yolo_model(
                img_bgr, conf=0.35, iou=0.45, device=yolo_device, verbose=False
            )[0]

            for b in res.boxes:
                if int(b.cls[0]) != 0:
                    continue

                x1, y1, x2, y2 = [int(round(v)) for v in b.xyxy[0].tolist()]

                x1 = max(0, min(x1, w - 1))
                y1 = max(0, min(y1, h - 1))
                x2 = max(0, min(x2, w - 1))
                y2 = max(0, min(y2, h - 1))

                bw = max(0, x2 - x1)
                bh = max(0, y2 - y1)

                if bw <= 0 or bh <= 0:
                    continue

                conf = float(b.conf[0]) if b.conf is not None else 1.0
                area = bw * bh

                box = {
                    "x": x1,
                    "y": y1,
                    "w": bw,
                    "h": bh,
                    "conf": conf,
                    "area": area,
                }
                boxes.append(box)

            if boxes:
                best = max(boxes, key=lambda d: d["area"])

    except Exception as e:
        logger.warning(f"Error in detect_persons_in_frame: {e}")

    t_yolo = time.time() - t_yolo_start
    return frame_size, boxes, best, t_yolo


def detect_faces_in_frame(
    frame_bytes: bytes,
) -> tuple[Optional[dict], list[dict], Optional[dict], float]:
    """SCRFD ONNX を使用してフレーム内の顔を検出します。"""
    if not _HAS_CV2:
        return None, [], None, 0.0

    t0 = time.time()
    frame_size = None
    boxes = []
    best = None

    try:
        bytes_arr = np.frombuffer(frame_bytes, np.uint8)
        img_bgr = cv2.imdecode(bytes_arr, cv2.IMREAD_COLOR)

        if img_bgr is None:
            return None, [], None, time.time() - t0

        h, w = img_bgr.shape[:2]
        frame_size = {"w": w, "h": h}

        detector = _get_scrfd_face_detector()
        if detector is None:
            return frame_size, [], None, time.time() - t0

        det, _, bboxes, _ = detector.detect_tracking(
            img_bgr,
            thresh=0.3,
            input_size=(640, 640),
            max_num=0,
        )

        confs = (
            det[:, 4].detach().cpu().numpy()
            if det is not None and hasattr(det, "shape") and det.shape[0] > 0
            else []
        )

        for idx, bbox in enumerate(bboxes):
            x1 = int(bbox[0])
            y1 = int(bbox[1])
            x2 = int(bbox[2])
            y2 = int(bbox[3])

            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(0, min(x2, w - 1))
            y2 = max(0, min(y2, h - 1))

            bw = max(0, x2 - x1)
            bh = max(0, y2 - y1)

            if bw <= 0 or bh <= 0:
                continue

            area = bw * bh
            conf = float(confs[idx]) if idx < len(confs) else 1.0

            boxes.append(
                {
                    "x": x1,
                    "y": y1,
                    "w": bw,
                    "h": bh,
                    "conf": conf,
                    "area": area,
                }
            )

        if boxes:
            best = max(boxes, key=lambda d: d["area"])

    except Exception as e:
        logger.warning(f"Error in detect_faces_in_frame (SCRFD): {e}")

    return frame_size, boxes, best, time.time() - t0


def draw_boxes_on_frame(
    frame_bytes: bytes,
    boxes: list[dict],
    best_box: Optional[dict] = None,
    box_color=(0, 255, 0),
    best_color=(0, 0, 255),
) -> Optional[bytes]:
    """フレームにボックスを描画し、JPEG バイトを返します。"""
    if not _HAS_CV2:
        return None

    try:
        bytes_arr = np.frombuffer(frame_bytes, np.uint8)
        img = cv2.imdecode(bytes_arr, cv2.IMREAD_COLOR)

        if img is None:
            return None

        for box in boxes:
            x = box["x"]
            y = box["y"]
            w = box["w"]
            h = box["h"]
            conf = box.get("conf", 0)

            color = best_color if box is best_box else box_color

            cv2.rectangle(
                img,
                (x, y),
                (x + w, y + h),
                color,
                2,
            )

            label = f"{conf:.2f}"
            cv2.putText(
                img,
                label,
                (x, y - 5 if y > 10 else y + 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

        success, buffer = cv2.imencode(".jpg", img)
        if not success:
            return None

        return buffer.tobytes()

    except Exception as e:
        logger.warning(f"draw_boxes_on_frame error: {e}")
        return None