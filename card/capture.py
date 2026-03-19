import os
import time
import logging
import numpy as np
from card.utils import deskew_card

logger = logging.getLogger("kiosk.card.capture")

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

try:
    import onnxruntime as ort  # type: ignore
    _HAS_ORT = True
except Exception:
    ort = None
    _HAS_ORT = False


# Raspberry Pi でのパフォーマンス向上のために、利用可能な場合は INT8 量子化モデルを使用
_yolo_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "models", "card", "best.onnx"
)
_yolo_int8 = _yolo_path.replace(".onnx", "_int8.onnx")
_DEFAULT_MODEL = _yolo_int8 if os.path.exists(_yolo_int8) else _yolo_path


class CardYoloCapture:
    """
    YOLOv8 ONNX モデルを使用して名刺 (business card) を検出します。

    自動キャプチャを行うには、名刺が `required_stable` フレーム連続で出現する必要があります。
    返される画像は、品質を維持するためにリサイズされません。
    """

    def __init__(
        self,
        model_path: str = _DEFAULT_MODEL,
        required_stable: int = 3,
        conf_threshold: float = 0.93,
        cooldown: float = 3.0,
        infer_size: int = 640,
    ):
        self.required_stable = required_stable
        self.conf_threshold = conf_threshold
        self.cooldown = cooldown
        self.infer_size = infer_size

        self.stable_count = 0
        self.last_capture_time = 0.0
        self._session = None

        if not _HAS_ORT:
            logger.warning("onnxruntime が利用できません – CardYoloCapture は無効になります。")
            return
        if cv2 is None:
            logger.warning("OpenCV が利用できません – CardYoloCapture は無効になります。")
            return

        try:
            import platform
            if platform.machine() in ("aarch64", "armv7l"):
                providers = ["CPUExecutionProvider"]
            else:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                model_path, sess_options=sess_opts, providers=providers
            )
            used = self._session.get_providers()
            logger.info("CardYoloCapture loaded model: %s | providers: %s", model_path, used)
        except Exception as e:
            logger.error("ONNX モデルをロードできませんでした: %s", e)
            self._session = None

    # ------------------------------------------------------------------
    def _preprocess(self, frame_bgr: np.ndarray):
        """
        フレームを infer_size×infer_size のレターボックス形式にリサイズし、
        float32 NCHW の blob と、bbox を元の画像にマッピングするためのスケール/パッド引数を返します。
        """
        orig_h, orig_w = frame_bgr.shape[:2]
        s = self.infer_size
        scale = min(s / orig_w, s / orig_h)
        new_w, new_h = int(orig_w * scale), int(orig_h * scale)
        pad_x = (s - new_w) // 2
        pad_y = (s - new_h) // 2

        resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((s, s, 3), 114, dtype=np.uint8)
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

        # BGR → RGB, HWC → NCHW, [0-255] → [0.0-1.0]
        blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob[None])  # (1, 3, H, W)
        return blob, scale, pad_x, pad_y

    def _postprocess(self, output: np.ndarray, scale: float, pad_x: int, pad_y: int,
                     orig_w: int, orig_h: int):
        """
        YOLOv8 の出力をパースします (shape [1, 5+nc, 8400]) → list[dict(x1,y1,x2,y2,conf)]
        `output` はクラス数に応じて shape (1,5,8400) または (1,4+nc,8400) になる可能性があります。
        NMS は不要です。「名刺があるかどうか」だけが必要なため、信頼度が最大の予測のみを取得します。
        """
        # YOLOv8 検出出力: (1, 4+nc, num_anchors)
        preds = output[0]          # (4+nc, num_anchors)
        num_anchors = preds.shape[-1]

        # 4行目以降からクラススコアを取得
        scores = preds[4:, :]      # (nc, num_anchors)
        class_ids = np.argmax(scores, axis=0)      # (num_anchors,)
        confs = scores[class_ids, np.arange(num_anchors)]  # (num_anchors,)

        mask = confs >= self.conf_threshold
        if not mask.any():
            return []

        boxes_xywh = preds[:4, mask].T   # (N, 4) 推論座標での cx, cy, w, h
        confs = confs[mask]

        results = []
        for (cx, cy, bw, bh), conf in zip(boxes_xywh, confs):
            # キャンバス座標から元の座標へ変換
            x1 = (cx - bw / 2 - pad_x) / scale
            y1 = (cy - bh / 2 - pad_y) / scale
            x2 = (cx + bw / 2 - pad_x) / scale
            y2 = (cy + bh / 2 - pad_y) / scale

            x1 = max(0, min(orig_w, x1))
            y1 = max(0, min(orig_h, y1))
            x2 = max(0, min(orig_w, x2))
            y2 = max(0, min(orig_h, y2))

            if x2 > x1 and y2 > y1:
                results.append({
                    "x1": int(x1), "y1": int(y1),
                    "x2": int(x2), "y2": int(y2),
                    "conf": float(conf),
                })
        return results

    # ------------------------------------------------------------------
    def detect(self, frame_bgr: np.ndarray):
        """
        カメラからの BGR フレームを分析します。

        Returns
        -------
        triggered : bool
            名刺が要求されたフレーム数安定して保持され、クールダウン期間外の場合に True を返します。
        crop : np.ndarray | None
            元の座標で取得された BGR 画像（リサイズなし）。トリガーされていない場合は None。
        bbox : dict | None
            最も良い検出結果の {x1,y1,x2,y2,conf}、または None。
        """
        if self._session is None or cv2 is None:
            return False, None, None

        orig_h, orig_w = frame_bgr.shape[:2]
        blob, scale, pad_x, pad_y = self._preprocess(frame_bgr)

        try:
            inp_name = self._session.get_inputs()[0].name
            raw = self._session.run(None, {inp_name: blob})
        except Exception as e:
            logger.warning("ONNX inference error: %s", e)
            self.stable_count = 0
            return False, None, None

        detections = self._postprocess(raw[0], scale, pad_x, pad_y, orig_w, orig_h)

        if not detections:
            # 名刺が見つからない → カウントをリセット
            self.stable_count = 0
            return False, None, None

        # 最も信頼度の高い検出結果を取得
        best = max(detections, key=lambda d: d["conf"])
        self.stable_count += 1

        logger.debug(
            "Card detected conf=%.3f stable=%d/%d bbox=(%d,%d,%d,%d)",
            best["conf"], self.stable_count, self.required_stable,
            best["x1"], best["y1"], best["x2"], best["y2"],
        )

        now = time.time()
        if self.stable_count < self.required_stable:
            return False, None, best

        if now - self.last_capture_time < self.cooldown:
            return False, None, best

        # トリガー！
        self.last_capture_time = now
        self.stable_count = 0  # キャプチャ後にリセット

        # クリッピングを避けるために、要求通りにクロップではなくフルフレームを返す
        img_out = frame_bgr.copy()

        logger.info(
            "Card AUTO-CAPTURED (FULL FRAME) bbox=(%d,%d,%d,%d) conf=%.3f",
            best["x1"], best["y1"], best["x2"], best["y2"], best["conf"],
        )
        return True, img_out, best

    def reset(self):
        """カウント状態をリセットして検出サイクルを再開します。"""
        self.stable_count = 0
        self.last_capture_time = 0.0


# 後方互換性のためのエイリアス
CardAutoCapture = CardYoloCapture
