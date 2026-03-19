import os
import time
import logging

logger = logging.getLogger("kiosk.card.yolo")

def init_yolo():
    try:
        from ultralytics import YOLO # type: ignore
        YOLO_MODEL_PATH = os.getenv("YOLO_MODEL", "yolov8n.pt")
        t0 = time.time()
        model = YOLO(YOLO_MODEL_PATH)
        device = "cpu"
        model = model.to(device)
        logger.info("Loaded YOLO model: %s on device=%s in %.3fs", YOLO_MODEL_PATH, device, time.time() - t0)
        return model, True
    except Exception as e:
        logger.warning(f"YOLO not available: {e}")
        return None, False
