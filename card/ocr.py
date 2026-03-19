import os
import time
import logging
import re
from pathlib import Path
import numpy as np
import cv2  # Added for preprocessing
from PIL import Image

# このモジュールのロガーを初期化
logger = logging.getLogger("kiosk.card.ocr")

# --- ONNX Runtime / LLM のインポート ---
try:
    from llama_cpp import Llama  # type: ignore
    _HAS_LLAMA = True
except Exception:
    _HAS_LLAMA = False

try:
    import onnxruntime as ort  # type: ignore
    _HAS_ONNXRUNTIME = True
except Exception:
    _HAS_ONNXRUNTIME = False

# --- パスと設定 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# フォールバック: PROJECT_ROOT に models ディレクトリが見つからない場合、親ディレクトリを検索
MODELS_ROOT = PROJECT_ROOT
if not (MODELS_ROOT / "models").exists():
    for parent in PROJECT_ROOT.parents:
        if (parent / "models").exists():
            MODELS_ROOT = parent
            break

LLM_GGUF = os.getenv("LLM_GGUF", str(MODELS_ROOT / "models" / "qwen2.5-3b-instruct-q4_0.gguf"))
LLM_CONTEXT = int(os.getenv("LLM_CONTEXT", "2048"))
_default_threads = max(1, os.cpu_count() or 4)
LLM_THREADS = int(os.getenv("LLM_THREADS", str(_default_threads)))
LLM_N_GPU_LAYERS = int(os.getenv("LLM_N_GPU_LAYERS", "0"))
ENABLE_LLM = os.getenv("ENABLE_LLM", "0") == "1"

ONNX_MODEL_PATH = os.getenv("ONNX_MODEL", "model.onnx")
# 相対パスの場合、MODELS_ROOT からの絶対パスにする
if not os.path.isabs(ONNX_MODEL_PATH):
    ONNX_MODEL_PATH = str(MODELS_ROOT / ONNX_MODEL_PATH)

OPENVINO_DEVICE = os.getenv("OPENVINO_DEVICE", "CPU")

_LLM = None
_ORT_SESS = None

from .utils import _torch_has_cuda


def _ocr_profile() -> str:
    return os.getenv("OCR_PROFILE", "quality").strip().lower()


def _profile_defaults(profile: str) -> dict:
    if profile == "quality":
        return {
            "multi_pass_mode": "adaptive",
            "top_ratio": 0.45,
            "upscale": 1.5,
            "contrast_alpha": 1.35,
            "brightness_beta": 10,
            "merge_center_dist": 22.0,
            "min_lines_for_confident": 12,
            "pad_top": 256,
            "pad_side": 256,
            "det_limit_side_len": 1440,
            "det_thresh": 0.15,
            "det_box_thresh": 0.25, # ロゴやノイズの塊を除外するために数値を増加
            "det_unclip_ratio": 2.2, # 標準フォントと大きなフォントのバランスを取った比率
            "text_score": 0.40,
            "min_height": 7,
            "width_height_ratio": 30.0,
            "max_side_len": 2800,
            "min_side_len": 10,
            "det_dilation": True,
        }
    if profile == "balanced":
        return {
            "multi_pass_mode": "adaptive",
            "top_ratio": 0.40,
            "upscale": 2.0,
            "contrast_alpha": 1.25,
            "brightness_beta": 4,
            "merge_center_dist": 18.0,
            "min_lines_for_confident": 10,
            "pad_top": 128,
            "pad_side": 64,
            "det_limit_side_len": 800,
            "det_thresh": 0.30,
            "det_box_thresh": 0.45,
            "det_unclip_ratio": 2.0,
            "text_score": 0.45,
            "min_height": 8,
            "width_height_ratio": 25.0,
            "max_side_len": 2200,
            "min_side_len": 12,
            "det_dilation": True,
        }
    return {
        "multi_pass_mode": "off",
        "top_ratio": 0.40,
        "upscale": 1.3,
        "contrast_alpha": 1.12,
        "brightness_beta": 1,
        "merge_center_dist": 18.0,
        "min_lines_for_confident": 7,
        "pad_top": 0,
        "pad_side": 0,
        "det_limit_side_len": 640,
        "det_thresh": 0.32,
        "det_box_thresh": 0.50,
        "det_unclip_ratio": 1.6,
        "text_score": 0.50,
        "min_height": 10,
        "width_height_ratio": 20.0,
        "max_side_len": 1800,
        "min_side_len": 12,
        "det_dilation": False,
    }

def _pick_ort_providers(openvino_device: str = "AUTO"):
    try:
        import onnxruntime as ort
        avail = set(ort.get_available_providers())
    except Exception:
        avail = set()
    order = []
    if "OpenVINOExecutionProvider" in avail:
        order.append(("OpenVINOExecutionProvider", {"device_type": openvino_device}))
    order.append("CPUExecutionProvider")
    return order

def _llama_default_n_gpu_layers() -> int:
    if "LLM_N_GPU_LAYERS" in os.environ:
        try:
            return int(os.getenv("LLM_N_GPU_LAYERS", "0"))
        except ValueError:
            return 0
    try:
        from llama_cpp import Llama
        info = getattr(Llama, "build_info", lambda: {})()
        flags = ["cuda", "hipblas", "metal", "sycl", "vulkan", "opencl"]
        if any(info.get(k, False) for k in flags):
            return 999
    except Exception:
        pass
    return 0

def _lazy_llm():
    global _LLM
    if not ENABLE_LLM:
        return None
    if _LLM is None:
        if not _HAS_LLAMA:
            return None
        model_path = Path(LLM_GGUF)
        if not model_path.exists():
            return None
        try:
            _LLM = Llama(
                model_path=str(model_path),
                n_ctx=LLM_CONTEXT,
                n_batch=min(512, LLM_CONTEXT),
                n_threads=LLM_THREADS,
                n_gpu_layers=_llama_default_n_gpu_layers(),
                use_mmap=True,
                use_mlock=False,
                verbose=False,
            )
        except Exception as e:
            logger.error(f"[llama] Llama のロードに失敗しました: {e}")
            _LLM = None
    return _LLM

def _lazy_ort():
    global _ORT_SESS
    if _ORT_SESS is None and _HAS_ONNXRUNTIME and Path(ONNX_MODEL_PATH).exists():
        so = ort.SessionOptions()
        providers = _pick_ort_providers(OPENVINO_DEVICE)
        try:
            _ORT_SESS = ort.InferenceSession(
                ONNX_MODEL_PATH,
                sess_options=so,
                providers=providers,
            )
        except Exception as e:
            logger.error(f"ONNX session creation failed: {e}")
    return _ORT_SESS

class RapidReaderWrapper:
    def __init__(self, engine):
        self.engine = engine
        prof = _profile_defaults(_ocr_profile())
        # OCR_MULTI_PASS:
        # - "off" または "0": シングルパスのみ（最速）
        # - "always" または "1": 常に複数パスを実行（最も堅牢、最遅）
        # - "adaptive": 最初のパスの結果が不十分な場合のみ複数パスを実行
        self.multi_pass_mode = os.getenv("OCR_MULTI_PASS", "adaptive").strip().lower()
        self.top_ratio = float(os.getenv("OCR_TOP_RATIO", str(prof["top_ratio"])))
        self.upscale = float(os.getenv("OCR_UPSCALE", str(prof["upscale"])))
        self.contrast_alpha = float(os.getenv("OCR_CONTRAST_ALPHA", str(prof["contrast_alpha"])))
        self.brightness_beta = int(os.getenv("OCR_BRIGHTNESS_BETA", str(prof["brightness_beta"])))
        self.merge_center_dist = float(os.getenv("OCR_MERGE_CENTER_DIST", str(prof["merge_center_dist"])))
        self.min_lines_for_confident = int(os.getenv("OCR_MIN_LINES_CONFIDENT", str(prof["min_lines_for_confident"])))
        self.pad_top = int(os.getenv("OCR_PAD_TOP", str(prof["pad_top"])))
        self.pad_side = int(os.getenv("OCR_PAD_SIDE", str(prof["pad_side"])))

    def _to_rgb_np(self, img):
        if isinstance(img, Image.Image):
            return np.array(img.convert("RGB"))
        return img

    def _run_engine(self, np_img):
        """生の OCR エンジンを実行し、標準化された結果を返します。"""
        result, _ = self.engine(np_img)
        if not result:
            return []
        
        std = []
        for item in result:
            try:
                if len(item) == 2: # (bbox, (text, score))
                    b, (t, s) = item
                    std.append(([[float(x), float(y)] for x, y in b], t, float(s), None))
                elif len(item) >= 3: # (bbox, text, score, [angle])
                    b, t, s = item[0], item[1], item[2]
                    a = item[3] if len(item) > 3 else None
                    std.append(([[float(x), float(y)] for x, y in b], t, float(s), a))
            except:
                continue
        return std

    def _add_border(self, np_img: np.ndarray):
        top = max(0, self.pad_top)
        side = max(0, self.pad_side)
        if top == 0 and side == 0:
            return np_img, 0, 0
        bordered = cv2.copyMakeBorder(
            np_img,
            top,      # top
            top,      # bottom
            side,     # left
            side,     # right
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )
        return bordered, side, top

    def _add_border_with(self, np_img: np.ndarray, top: int, side: int):
        top = max(0, int(top))
        side = max(0, int(side))
        if top == 0 and side == 0:
            return np_img, 0, 0
        bordered = cv2.copyMakeBorder(
            np_img,
            top,
            side,
            side,
            side,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )
        return bordered, side, top

    def _normalize_text(self, s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff]+", "", s, flags=re.UNICODE)
        return s

    def _rescale_bbox(self, bbox, sx: float, sy: float, ox: float = 0.0, oy: float = 0.0):
        return [[max(0, int(round((x / sx) + ox))), max(0, int(round((y / sy) + oy))) ] for x, y in bbox]

    def _rotate_bbox_180(self, bbox, w: int, h: int):
        """座標空間 (w, h) 内で bbox を 180 度回転させます。"""
        # (x, y) -> (w-x, h-y)
        # Bbox shape: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        # 左上の開始点を維持するために:
        # 回転後の p1 = p3 の反転
        # 回転後の p2 = p4 の反転
        # 回転後の p3 = p1 の反転
        # 回転後の p4 = p2 の反転
        pts = [[w - p[0], h - p[1]] for p in bbox]
        return [pts[2], pts[3], pts[0], pts[1]]

    def _enhance(self, np_img: np.ndarray) -> np.ndarray:
        # 1. バイラテラルフィルタを使用して、テキストのエッジを鮮明に保ちながらセンサーノイズを滑らかにします。
        # これはウェブカメラのショットに非常に効果的です (d=7, sigmaColor=35, sigmaSpace=35)
        smooth = cv2.bilateralFilter(np_img, 7, 35, 35)
        gray = cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY)
        
        # 2. CLAHE (Contrast Limited Adaptive Histogram Equalization) を適用
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        gray_clahe = clahe.apply(gray)
        
        # 3. RGB に戻す
        enhanced = cv2.cvtColor(gray_clahe, cv2.COLOR_GRAY2RGB)
        
        # 4. バランスの取れた明るさ/コントラストスケール (白飛びを防止)
        # 画像がすでに明るい場合に詳細を保持するために、0.95 の倍率を使用します。
        alpha_val = max(1.0, self.contrast_alpha * 0.95)
        enhanced = cv2.convertScaleAbs(enhanced, alpha=alpha_val, beta=max(0, self.brightness_beta - 5))
        
        # 5. 精密なシャープ化 (大きなフォントに対しては非常に控えめに保持)
        gray_for_std = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)
        std_dev = np.std(gray_for_std)
        if std_dev > 50:
            # Moderate - High contrast card (Sharp edges)
            kernel = np.array([[0, -0.15, 0], [-0.15, 1.6, -0.15], [0, -0.15, 0]], dtype=np.float32)
        else:
            # アグレッシブ - ぼやけた画像や低コントラストの名刺
            # 文字を鮮明に保ちつつ分離するために 3.8 の重みを使用
            kernel = np.array([[-0.4, -0.4, -0.4], [-0.4, 3.8, -0.4], [-0.4, -0.4, -0.4]], dtype=np.float32)
            
        enhanced = cv2.filter2D(enhanced, -1, kernel)
        return enhanced

    def _merge_results(self, rows):
        merged = []
        for bbox, text, conf, angle_info in rows:
            if not text or not text.strip():
                continue
            key = self._normalize_text(text)
            if not key:
                continue

            x_c = sum(p[0] for p in bbox) / len(bbox)
            y_c = sum(p[1] for p in bbox) / len(bbox)
            hit = None
            for i, it in enumerate(merged):
                if it["key"] != key:
                    continue
                dx = abs(it["x_c"] - x_c)
                dy = abs(it["y_c"] - y_c)
                if dx <= self.merge_center_dist and dy <= self.merge_center_dist:
                    hit = i
                    break

            if hit is None:
                merged.append({
                    "key": key,
                    "bbox": bbox,
                    "text": text,
                    "conf": conf,
                    "x_c": x_c,
                    "y_c": y_c,
                    "angle_info": angle_info,
                })
            elif conf > merged[hit]["conf"]:
                merged[hit].update({"bbox": bbox, "text": text, "conf": conf, "x_c": x_c, "y_c": y_c, "angle_info": angle_info})

        merged.sort(key=lambda t: (t["y_c"], t["x_c"]))
        return [(m["bbox"], m["text"], float(m["conf"]), m["angle_info"]) for m in merged]

    def _needs_extra_passes(self, base_rows, img_h: int) -> bool:
        if not base_rows:
            return True
        if len(base_rows) < self.min_lines_for_confident:
            return True

        top_cut = max(1, int(img_h * self.top_ratio))
        header_rows = []
        for bbox, text, conf, _ in base_rows: # このチェックには角度情報は使用しません
            y_c = sum(p[1] for p in bbox) / len(bbox)
            if y_c <= top_cut and text and text.strip():
                header_rows.append(text.strip().lower())

        header_blob = " | ".join(header_rows)
        if not header_blob:
            return True

        # ヘッダーに強力な会社指標が既にある場合はスキップ
        company_found = re.search(r"\b(company|co\.?|ltd|limited|inc|corp|corporation|llc|jsc|株式会社||会社有限会社|合同会社|公司)\b", header_blob, re.I)
        if company_found:
            return False
            
        # 名刺にテキストはあるがヘッダーに会社名がない疑いがある場合は、強制的に追加パスを実行
        if "@" in header_blob or re.search(r"\d{3,}.?\d{3,}", header_blob) or re.search(r"[\u4e00-\u9fff]{3,}", header_blob):
            return True
            
        # 全体的な情報の豊富さチェック: メール + 少なくとも 1 つの長い数字
        all_text = " ".join(t for _, t, _, _ in base_rows).lower()
        if "@" in all_text and len(re.findall(r"\d", all_text)) >= 10:
            return False
            
        return True

    def _estimate_is_upside_down(self, rows, img_h: int) -> bool:
        """フィールドの相対位置を使用して向きを堅牢に検出します。"""
        if not rows:
            return False
        
        con_y, nam_y = [], []
        cls_180, cls_0 = 0, 0
        mid_y = img_h / 2
        
        for bbox, text, conf, angle_info in rows:
            if not text:
                continue
            # 中心 Y を計算
            if angle_info and isinstance(angle_info, (list, tuple)) and len(angle_info) >= 1:
                label = str(angle_info[0])
                if label in ('1', '180'):
                    cls_180 += 1
                elif label == '0':
                    cls_0 += 1
                
            t_low = text.lower()
            # 連絡先パターン
            is_con = bool(re.search(r"@|\.com|\.vn|tel:|fax:|\d{3,}-\d{3,}", t_low))
            # 名前パターン (通常は日本語文字を含む短いもの)
            is_nam = bool(len(text) <= 8 and re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", text))

            if is_con:
                con_y.append(yc)
                logger.info(f"  [CON] '{text}' y={yc:.1f}")
            if is_nam:
                nam_y.append(yc)
                logger.info(f"  [NAM] '{text}' y={yc:.1f}")

        # 1. 明確な多数決がある場合は CLS を信頼
        if cls_180 > cls_0 or cls_0 > cls_180:
            logger.info(f"[Orientation Logic] CLS votes: 180={cls_180}, 0={cls_0}")
            if cls_180 > cls_0:
                return True
            return False

        # 2. 強力なヒューリスティック: 連絡先情報は名前の下にあるべき (Y が大きい)
        if con_y and nam_y:
            avg_con = sum(con_y) / len(con_y)
            avg_nam = sum(nam_y) / len(nam_y)
            logger.info(f"[Orientation Logic] Heuristic: Contact AvgY={avg_con:.1f}, Name AvgY={avg_nam:.1f}")
            if avg_con < avg_nam: # Contact is above Name -> Reversed
                return True
            else:
                return False

        # 3. Fallback: If we only have contacts, and they are mostly in the top half
        if con_y:
            avg_con = sum(con_y) / len(con_y)
            logger.info(f"[Orientation Logic] Fallback Contact: AvgY={avg_con:.1f} (MidY={mid_y})")
            if avg_con < mid_y:
                return True

        logger.info("[向き判定ロジック] 強力な信号なし。デフォルト: 正位置。")
        return False
        

    def readtext(self, img, detail=0, paragraph=False):
        """リファクタリングされた 2 パス OCR: 向きチェック -> 物理的な回転 (必要な場合) -> 最終結果。"""
        if self.engine is None:
            return []

        t_start = time.time()
        processed_img = self._to_rgb_np(img)
        
        # Target limit for performance
        target_limit = int(os.getenv("OCR_TARGET_LIMIT", "2300"))
        h_f, w_f = processed_img.shape[:2]
        
        # パフォーマンスのためのターゲット制限
        down_sc = 1.0
        if max(h_f, w_f) > target_limit:
            down_sc = target_limit / max(h_f, w_f)
            
        # 2. アップスケーリングのためのスケーリング (小さなテキストの品質向上)
        up_sc = float(self.upscale) if self.upscale > 1.0 else 1.0
        
        # 結合されたスケーリング係数
        total_sc = down_sc * up_sc
        
        if total_sc != 1.0:
            final_img = cv2.resize(processed_img, (int(w_f * total_sc), int(h_f * total_sc)), interpolation=cv2.INTER_LANCZOS4 if up_sc > 1.0 else cv2.INTER_AREA)
            logger.info(f"[OCR] Resizing image: {w_f}x{h_f} -> {final_img.shape[1]}x{final_img.shape[0]} (scale={total_sc:.2f})")
        else:
            final_img = processed_img
        
        # 境界線/強調の追加
        from .utils import deskew_card
        final_img = deskew_card(final_img)
        final_img_enhanced = self._enhance(final_img)
        final_img_padded, pad_x, pad_y = self._add_border(final_img_enhanced)
        
        # --- Pass 1: 検出と向きの判定 ---
        rows_p1 = self._run_engine(final_img_padded)
        if not rows_p1:
            return []
            
        h_p, w_p = final_img_padded.shape[:2]
        is_upside_down = self._estimate_is_upside_down(rows_p1, h_p)
        
        if is_upside_down:
            logger.info("向き: 反転。物理的に回転して Pass 2 を実行します。")
            # 最大品質のために物理的に回転
            final_img_v2 = cv2.rotate(final_img_padded, cv2.ROTATE_180)
            rows_p2 = self._run_engine(final_img_v2)
            if not rows_p2:
                # 何らかの理由で p2 が失敗した場合は p1 にフォールバック
                logger.warning("Pass 2 が失敗しました。Pass 1 の結果にフォールバックします。")
                final_results = rows_p1 
            else:
                final_results = rows_p2
        else:
            logger.info("Orientation: Upright. Optimized skip second pass.")
            final_results = rows_p1

        # Scale back to original image size
        # total_sc was applied to (w_f, h_f) to get final_img dimensions
        # Bboxes from final_results are in final_img_padded space.
        # 1. Subtract padding -> final_img space
        # 2. Divide by total_sc -> original (processed_img) space
        sx = total_sc
        sy = total_sc

        all_rows = [
            (self._rescale_bbox(bbox, sx, sy, -float(pad_x), -float(pad_y)), text, score, angle_info)
            for bbox, text, score, angle_info in final_results
        ]
        
        out = self._merge_results(all_rows)
        
        elapsed = time.time() - t_start
        logger.info(f"[OCR] Two-pass Strategy done in {elapsed:.3f}s. is_upside_down={is_upside_down}")
        
        if detail == 0:
            return [text for _, text, _, _ in out]
        return out

_READER = None

def get_reader():
    global _READER
    if _READER is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            prof = _profile_defaults(_ocr_profile())
            ocr_kwargs = {
                "det_limit_side_len": int(os.getenv("OCR_DET_LIMIT_SIDE_LEN", str(prof["det_limit_side_len"]))),
                "det_thresh": float(os.getenv("OCR_DET_THRESH", str(prof["det_thresh"]))),
                "det_box_thresh": float(os.getenv("OCR_DET_BOX_THRESH", str(prof["det_box_thresh"]))),
                "det_unclip_ratio": float(os.getenv("OCR_DET_UNCLIP_RATIO", str(prof["det_unclip_ratio"]))),
                "text_score": float(os.getenv("OCR_TEXT_SCORE", str(prof["text_score"]))),
                "min_height": int(os.getenv("OCR_MIN_HEIGHT", str(prof["min_height"]))),
                "width_height_ratio": float(os.getenv("OCR_WIDTH_HEIGHT_RATIO", str(prof["width_height_ratio"]))),
                "max_side_len": int(os.getenv("OCR_MAX_SIDE_LEN", str(prof["max_side_len"]))),
                "min_side_len": int(os.getenv("OCR_MIN_SIDE_LEN", str(prof["min_side_len"]))),
                "det_donot_use_dilation": os.getenv("OCR_DET_DILATION", "1" if prof["det_dilation"] else "0") != "1",
            }

            # Raspberry Pi でのパフォーマンス向上のために、利用可能な場合は INT8 量子化モデルを使用
            japan_rec_model_int8 = str(MODELS_ROOT / "models" / "PP-OCRv5_mobile_rec_int8.onnx")
            if os.path.exists(japan_rec_model_int8):
                japan_rec_model = japan_rec_model_int8
            else:
                japan_rec_model = str(MODELS_ROOT / "models" / "PP-OCRv5_mobile_rec.onnx")
            japan_dict = str(MODELS_ROOT / "models" / "ppocr_keys_v5.txt")

            # --- カスタム検出モデル ---
            custom_det_model = str(MODELS_ROOT / "models" / "ch_PP-OCRv4_det_server_infer.onnx")
            if os.path.exists(custom_det_model):
                ocr_kwargs["det_model_path"] = custom_det_model
                logger.info(f"Using custom detection model: {custom_det_model}")

            # --- 向き / 角度分類のサポート ---
            cls_model_path = str(MODELS_ROOT / "models" / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
            use_angle_cls = os.getenv("OCR_USE_ANGLE_CLS", "1") == "1"
            if not os.path.exists(cls_model_path):
                if use_angle_cls:
                    logger.warning(f"Orientation model not found at {cls_model_path}. Disabling angle cls.")
                use_angle_cls = False
            
            if use_angle_cls:
                ocr_kwargs["use_angle_cls"] = True
                ocr_kwargs["cls_model_path"] = cls_model_path
                logger.debug("PaddleOCR Angle Classifer (cls) ENABLED.")
            # --------------------------------------------------

            if os.path.exists(japan_rec_model) and os.path.exists(japan_dict):
                logger.info(f"{japan_rec_model} に日本語 OCR モデルファイルが見つかりました。日本語モデルで RapidOCR を初期化します。")
                try:
                    _rapid_ocr = RapidOCR(
                        rec_model_path=japan_rec_model,
                        rec_keys_path=japan_dict,
                        **ocr_kwargs,
                    )
                except TypeError:
                    fallback_kwargs = {"rec_model_path": japan_rec_model, "rec_keys_path": japan_dict}
                    if "det_model_path" in ocr_kwargs:
                        fallback_kwargs["det_model_path"] = ocr_kwargs["det_model_path"]
                    if "cls_model_path" in ocr_kwargs:
                        fallback_kwargs["cls_model_path"] = ocr_kwargs["cls_model_path"]
                    _rapid_ocr = RapidOCR(**fallback_kwargs)
            else:
                logger.warning(
                    f"日本語 OCR モデルファイルが見つかりません ({japan_rec_model} を確認)。デフォルトのモデルを使用します。"
                )
                try:
                    _rapid_ocr = RapidOCR(**ocr_kwargs)
                except TypeError:
                    fallback_kwargs = {}
                    if "det_model_path" in ocr_kwargs:
                        fallback_kwargs["det_model_path"] = ocr_kwargs["det_model_path"]
                    if "cls_model_path" in ocr_kwargs:
                        fallback_kwargs["cls_model_path"] = ocr_kwargs["cls_model_path"]
                    _rapid_ocr = RapidOCR(**fallback_kwargs)

            _READER = RapidReaderWrapper(_rapid_ocr)
        except Exception as e:
            import traceback
            logger.error(f"RapidOCR init failed: {e}\n{traceback.format_exc()}")
            _READER = RapidReaderWrapper(None)
    return _READER

def warmup_ocr(enable_llm=True, skip_llm_warmup=False):
    """
    モデルを事前に読み込み、初回の呼び出しを高速化します。
    """
    t0 = time.time()
    
    # ONNX warmup
    try:
        _ = _lazy_ort()
    except Exception as e:
        logger.warning(f"ONNX warmup err: {e}")

    # LLM warmup
    try:
        if enable_llm and not skip_llm_warmup:
            llm = _lazy_llm()
            if llm:
                llm.create_completion(prompt="{}", max_tokens=1, temperature=0.0)
    except Exception as e:
        logger.warning(f"LLM warmup err: {e}")

    # RapidOCR warmup
    try:
        reader = get_reader()
        if reader.engine:
            z = np.zeros((32, 32, 3), dtype=np.uint8)
            _ = reader.readtext(z, detail=0)
    except Exception as e:
        logger.warning(f"RapidOCR warmup err: {e}")

    logger.info("OCR のウォームアップが %.3f 秒で終了しました", time.time() - t0)
