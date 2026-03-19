import os
import base64
import re
import time
from io import BytesIO
from typing import Optional
import numpy as np
from PIL import Image

def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    inter_x1, inter_y1 = max(ax, bx), max(ay, by)
    inter_x2, inter_y2 = min(ax2, bx2), min(ay2, by2)

    iw, ih = max(0, inter_x2 - inter_x1), max(0, inter_y2 - inter_y1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0

def decode_data_url_to_pil(data_url: str) -> Image.Image:
    """dataURL ('data:image/...;base64,xxx') を取得して PIL.Image に変換します。"""
    if not data_url or "base64," not in data_url:
        raise ValueError("Invalid data URL")
    _, b64 = data_url.split("base64,", 1)
    img_bytes = BytesIO(base64.b64decode(b64))
    img = Image.open(img_bytes)
    return img

def fix_broken_tld(text: str) -> str:
    """
    電子メールのTLDにおける一般的なOCRアーティファクトを修正します。
    例: .co.p -> .co.jp, .cp -> .co.jp
    """
    if not text:
        return text
    
    # 順序が重要：より具体的なものを先に
    # 日本固有
    text = re.sub(r'\.co\.p\b', '.co.jp', text, flags=re.I)
    text = re.sub(r'\.co\.v\b', '.co.vn', text, flags=re.I)
    text = re.sub(r'\.co\.j\b', '.co.jp', text, flags=re.I)
    text = re.sub(r'\.co\.c\b', '.co.jp', text, flags=re.I)
    text = re.sub(r'\.cp\b', '.co.jp', text, flags=re.I)
    text = re.sub(r'\.jp\b\.?$', '.jp', text, flags=re.I) # 末尾のドットを削除
    
    # ベトナム固有
    text = re.sub(r'\.cvn\b', '.co.vn', text, flags=re.I)
    text = re.sub(r'\.co\.n\b', '.co.vn', text, flags=re.I)
    
    return text

def normalize_email_text(text: str) -> str:
    if not text:
        return ""
    # 電子メールに付着している英字ラベル（OCRエラーを含む）を除去
    # 一般的な電子メールラベルパターン: email, e-mail, mail など
    text = re.sub(
        r'(?i)\b(?:e[\s._-]?(?:m|rn|nn)(?:a|o)[il1][il1]?|ma[il1][il1])\s*[:：\s._-]*(?=[A-Z0-9._%+-]+@)',
        '', text
    )
    # 日本語のラベルを除去: メール, Eメール, e-メール
    text = re.sub(
        r'(?i)(?:e[\s._-]?)?メール\s*[:：\s._-]*(?=[A-Z0-9._%+-]+@)',
        '', text
    )
    # 電子メールの一部のように見えるものの前に一般的な電話ラベルがある場合は除去
    text = re.sub(
        r'(?i)\b(?:tel|phone|ph\.?|p\.?|mob(?:ile)?|cell|ＴＥＬ)\s*[:\s._-]*(?=.*@)',
        '', text
    )
    
    # @ 記号の一般的なOCRアーティファクトを処理
    text = re.sub(r'\(a\)', '@', text, flags=re.I)
    text = re.sub(r'[ \t]+[©©][ \t]+', '@', text) # 一部のOCRは @ を著作権記号として認識する
    
    # @, . および , の周囲の空白を詰める
    t = re.sub(r"[ \t]*@[ \t]*", "@", text)
    t = re.sub(r"[ \t]*\.[ \t]*", ".", t)
    t = re.sub(r"[ \t]*,[ \t]*", ",", t)
    
    # OCRは電子メール内の '.' を ',' や '-' や '_' と誤読することが多い
    # ドメインのドットのヒューリスティック修正: "example,co,jp" -> "example.co.jp"
    # 電子メールのシーケンスのように見えるものの中のみ
    t = re.sub(r'(@[a-zA-Z0-9._-]+)[,](com|net|org|biz|info|jp|vn|co|co\.jp|com\.vn)\b', r'\1.\2', t, flags=re.I)
    t = re.sub(r'(@[a-zA-Z0-9._-]+)\.[a-zA-Z0-9._-]+[,](com|jp|vn)\b', r'\1.\2', t, flags=re.I)
    
    # 最終的なクリーンアップ: ドメインのように見えるものにまだコンマがある場合は変換
    # 例: @domain,jp
    t = re.sub(r'(@[a-zA-Z0-9._-]+),([a-zA-Z]{2,4})\b', r'\1.\2', t, flags=re.I)

    # 余分な空白を除去するが、改行は保持
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines()]
    
    # 電子メールまたはその一部のように見える各行にTLD修正を適用
    fixed_lines = []
    for ln in lines:
        if "@" in ln or ln.startswith("www."):
            fixed_lines.append(fix_broken_tld(ln))
        else:
            fixed_lines.append(ln)
            
    return "\n".join(ln for ln in fixed_lines if ln)

def clean_ocr_text(text: str) -> str:
    if not text:
        return ""

    # 空白を詰める
    text = re.sub(r"\s+", " ", text)

    # 雑多な記号を除去するが、文字、数字、および電話用の記号 +()- は保持
    text = re.sub(
        r"[^0-9A-Za-z\u00C0-\u1EF9\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\.\,\;\:\-\+@\/\(\)\[\]\{\}'\"\s]+",
        " ",
        text,
    )

    # 必要に応じて、1文字のトークン（数字以外）を除去
    words = text.split()
    cleaned_words = []
    for w in words:
        w = w.strip()
        if len(w) == 1 and not w.isdigit():
            continue
        cleaned_words.append(w)

    return " ".join(cleaned_words).strip()

def infer_company_from_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    m = re.search(r"@([A-Za-z0-9._\-]+)", email)
    if not m:
        return None
    domain = m.group(1).lower()
    # 一般的な接尾辞を除去
    domain = re.sub(r"\.(co|com|net|jp|vn|asia|biz|info)$", "", domain)
    # ドットの前の部分を抽出（残っている場合）
    domain = domain.split(".")[0]
    if not domain:
        return None
    parts = re.split(r"[-_]", domain)
    # 各部分を大文字化: arktake -> Arktake, sao_mai_soft -> SaoMaiSoft
    return "".join(p.capitalize() for p in parts if p)

def extract_email_domain(email: str) -> str:
    """電子メールのドメイン（@とTLDの間）から会社名を抽出します。"""
    if not email or "@" not in email:
        return ""
    domain_part = email.split("@")[1]
    # TLD（.com, .vn, .co.jpなど）を削除
    domain_clean = re.sub(r"\.(com|vn|jp|cn|co|net|org|edu|gov|info)(\.[a-z]{2,3})?$", "", domain_part, flags=re.I)
    return domain_clean

def format_domain_as_company(domain: str) -> str:
    """ドメインを会社名（大文字）としてフォーマットし、会社の接尾辞を展開します。"""
    # 一般的なTLDを削除
    domain = re.sub(r'\.(com|jp|vn|net|org|co\.jp|com\.vn)$', '', domain, flags=re.I)
    
    # 一般的な会社の接尾辞を展開
    suffix_map = {
        'corp': 'CORPORATION',
        'co': 'CO.',
        'ltd': 'LIMITED',
        'inc': 'INCORPORATED',
        'llc': 'LLC',
        'jsc': 'JSC'
    }
    parts = domain.split('.')
    formatted_parts = []
    for p in parts:
        formatted_parts.append(suffix_map.get(p.lower(), p.upper()))
    return " ".join(formatted_parts)

def _torch_has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False

def _torch_has_directml() -> bool:
    try:
        import torch_directml
        return torch_directml.is_available()
    except Exception:
        return False

def should_skip_noise_line(text: str) -> bool:
    """
    スキップすべきノイズパターンを検出するための集中ロジック。
    """
    from card.constants import (
        FAX_HINT_RE, URL_RE, URL_HINT_RE, TAX_HINT_RE, 
        PHONE_LABEL_RE, EMAIL_LABEL_RE, MISC_NOISE_RE
    )
    if not text or not text.strip():
        return True
    return any(r.search(text) for r in [
        FAX_HINT_RE, URL_RE, URL_HINT_RE, TAX_HINT_RE, 
        PHONE_LABEL_RE, EMAIL_LABEL_RE, MISC_NOISE_RE
    ])

def deskew_card(img: np.ndarray) -> np.ndarray:
    """コンテンツの直線に基づいて画像の傾きを補正（デスクイー）します。"""
    if img is None:
        return img
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 輪郭を見つけるために二値化
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # 「前景」であるすべてのポイントを取得
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return img
    
    # cv2.minAreaRect(coords) は (中心(x, y), (幅, 高さ), 角度) を返す
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    
    # minAreaRect からの角度は [-90, 0) の範囲にある
    # 回転を適切に処理するために修正
    if angle < -45:
        angle = 90 + angle
    
    # まっすぐにするために回転させる。負の場合は正に回転させる必要がある。
    if abs(angle) > 0.5 and abs(angle) < 20: # 顕著だが極端でない場合のみデスクイーを実行
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        img = cv2.warpAffine(img, M, (w, h), 
                             flags=cv2.INTER_CUBIC, 
                             borderMode=cv2.BORDER_CONSTANT,
                             value=(255, 255, 255))
    return img

_UNDISTORT_MAPS = {}

def undistort_image(img: np.ndarray, calibration_file: str = "camera_calibration.json") -> np.ndarray:
    """
    魚眼レンズ補正パラメータを使用して画像の歪みを補正します。
    test_runtime_batch.py から同期されたロジック:
    - 画像の実際のサイズに合わせてカメラ行列 K を自動的にスケールします。
    - カメラ行列 K を新しい行列として保持します（画像を直線的に自然に見せるため）。
    """
    if img is None:
        return img
        
    import cv2
    import json
    global _UNDISTORT_MAPS
    
    h, w = img.shape[:2]
    map_key = (w, h)
    
    if map_key not in _UNDISTORT_MAPS:
        # キャリブレーションファイルを探す
        if not os.path.exists(calibration_file):
            alt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), calibration_file)
            if os.path.exists(alt_path):
                calibration_file = alt_path
            else:
                return img
        
        try:
            with open(calibration_file, 'r') as f:
                calib = json.load(f)
            
            K = np.array(calib['K'])
            D = np.array(calib['D'])
            calib_shape = calib.get('img_shape', [1080, 1920]) # [H, W]
            calib_h, calib_w = calib_shape[0], calib_shape[1]
            
            # 現在の画像サイズがキャリブレーションサイズと異なる場合、カメラ行列 K をスケール
            if (w, h) != (calib_w, calib_h):
                scale_x = w / calib_w
                scale_y = h / calib_h
                K[0, 0] *= scale_x
                K[0, 2] *= scale_x
                K[1, 1] *= scale_y
                K[1, 2] *= scale_y
            
            DIM = (w, h)
            # K 自体を newCameraMatrix として使用（test_runtime_batch.py のロジックと同様）
            # これにより、画像が「真っ直ぐ」になり、歪みがなくなります（balance=1.0 は不要）
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(K, D, np.eye(3), K, DIM, cv2.CV_16SC2)
            _UNDISTORT_MAPS[map_key] = (map1, map2)
        except Exception as e:
            if 'logger' in globals() or 'logger' in locals():
                logger.warning(f"歪み補正マップの初期化中にエラーが発生しました: {e}")
            return img
            
    map1, map2 = _UNDISTORT_MAPS[map_key]
    undistorted_img = cv2.remap(img, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    
    return undistorted_img
