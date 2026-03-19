import os
import sys
import argparse
import re

# 他のインポートの前に環境変数を設定
os.environ["OCR_PROFILE"] = "quality"
os.environ["OCR_DET_THRESH"] = "0.15"
os.environ["OCR_DET_BOX_THRESH"] = "0.25"
os.environ["OCR_TEXT_SCORE"] = "0.40"
os.environ["OCR_DET_UNCLIP_RATIO"] = "2.2"

import cv2
import time
import numpy as np
import traceback
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 入力と出力の設定
INPUT_DIR = PROJECT_ROOT / "test_images_registrations"
OUTPUT_BASE_DIR = PROJECT_ROOT / "tests" / "output" / "batch_registrations"

OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)

from card.capture import CardYoloCapture
from card.ocr import get_reader
from card.processor import parse_bcard_fields

def standardize_ocr_results(raw_rows):
    """さまざまな OCR フォーマットを [bbox, text, score, angle_info] に安全に変換します"""
    std = []
    if not raw_rows: return std
    for item in raw_rows:
        try:
            if len(item) == 2: # (bbox, (text, score))
                b, (t, s) = item
                std.append((b, t, float(s), None))
            elif len(item) >= 3: # (bbox, text, score, [angle])
                b, t, s = item[0], item[1], item[2]
                a = item[3] if len(item) > 3 else None
                std.append((b, t, float(s), a))
        except: continue
    return std

def draw_ocr_results(img_np, ocr_results, title, font=None, mode='both'):
    vis_pil = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(vis_pil)
    for item in ocr_results:
        # 標準: (bbox, text, score, angle_info)
        box, text, conf = item[0], item[1], item[2]
        b = np.array(box).astype(int)
        draw.polygon([tuple(p) for p in b], outline="red", width=2)
        if mode != 'det':
            l_val = f"({conf:.2f})" if isinstance(conf, (int, float)) else f"({conf})"
            label = f"{text} {l_val}"
            if font: draw.text(tuple(b[0]), label, fill="blue", font=font)
            else: draw.text(tuple(b[0]), label, fill="blue")
    if mode == 'both':
        if font: draw.text((10, 10), title, fill="red", font=font)
        else: draw.text((10, 10), title, fill="red")
    return cv2.cvtColor(np.array(vis_pil), cv2.COLOR_RGB2BGR)

def create_rec_montage(img_np, ocr_results, font=None):
    if not ocr_results: return None
    snippets = []
    max_w = 0
    for item in ocr_results:
        box, text = item[0], item[1]
        b = np.array(box).astype(int)
        x_min, y_min = np.min(b, axis=0)
        x_max, y_max = np.max(b, axis=0)
        h, w = img_np.shape[:2]
        x_min, y_min = max(0, x_min), max(0, y_min)
        x_max, y_max = min(w, x_max), min(h, y_max)
        if x_max > x_min and y_max > y_min:
            snippet = img_np[y_min:y_max, x_min:x_max].copy()
            snippet = cv2.copyMakeBorder(snippet, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=[0,0,0])
            snippets.append((snippet, text))
            max_w = max(max_w, snippet.shape[1])
    if not snippets: return None
    snippets = snippets[:50]
    total_h = sum(s[0].shape[0] for s in snippets) + (len(snippets) * 10) + 40
    canvas_w = max_w + 600
    canvas = np.full((total_h, canvas_w, 3), 255, dtype=np.uint8)
    current_y = 20
    canvas_pil = Image.fromarray(canvas)
    draw = ImageDraw.Draw(canvas_pil)
    for snip, txt in snippets:
        snip_h, snip_w = snip.shape[:2]
        snip_rgb = cv2.cvtColor(snip, cv2.COLOR_BGR2RGB)
        canvas_pil.paste(Image.fromarray(snip_rgb), (20, current_y))
        if font: draw.text((snip_w + 50, current_y + snip_h//4), f"-> {txt}", fill="black", font=font)
        else: draw.text((snip_w + 50, current_y + snip_h//4), f"-> {txt}", fill="black")
        current_y += snip_h + 10
    return cv2.cvtColor(np.array(canvas_pil), cv2.COLOR_RGB2BGR)

def simulate_enhance(img_np):
    try:
        smooth = cv2.bilateralFilter(img_np, 7, 35, 35)
        gray = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        gray_clahe = clahe.apply(gray)
        enhanced = cv2.cvtColor(gray_clahe, cv2.COLOR_GRAY2BGR)
        enhanced = cv2.convertScaleAbs(enhanced, alpha=1.35, beta=5)
        kernel = np.array([[-0.4, -0.4, -0.4], [-0.4, 3.8, -0.4], [-0.4, -0.4, -0.4]], dtype=np.float32)
        enhanced = cv2.filter2D(enhanced, -1, kernel)
        return enhanced
    except: return img_np

def estimate_is_upside_down(std_rows, img_h):
    """キーフィールドの相対位置を使用した堅牢な方向判定ロジック。"""
    if not std_rows: return False
    
    con_y, nam_y = [], []
    cls_180, cls_0 = 0, 0
    mid_y = img_h / 2
    
    for bbox, text, conf, angle_info in std_rows:
        if not text: continue
        yc = sum(p[1] for p in bbox) / 4
        
        # 角度分類フラグ
        if angle_info and isinstance(angle_info, (list, tuple)):
            if str(angle_info[0]) in ('1', '180'): cls_180 += 1
            elif str(angle_info[0]) == '0': cls_0 += 1
            
        t_low = text.lower()
        if re.search(r"@|\.com|\.vn|tel:|fax:|\d{3,}-\d{3,}", t_low):
            con_y.append(yc)
        if len(text) <= 8 and re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", text):
            nam_y.append(yc)

    print(f"    [Orientation Logic] CLS(180={cls_180}, 0={cls_0}) Contacts(N={len(con_y)}) Names(N={len(nam_y)})")

    if cls_180 > cls_0: return True
    # 0度と180度で判定

    if con_y and nam_y:
        avg_con = sum(con_y) / len(con_y)
        avg_nam = sum(nam_y) / len(nam_y)
        # 標準: 連絡先は名前の下にある (Y 値が大きい)
        if avg_con < avg_nam: # Contact is ABOVE Name -> Reversed
            print(f"    [ヒューリスティック] 連絡先 (y={avg_con:.1f}) が名前 (y={avg_nam:.1f}) の上にあります -> 逆さま")
            return True
        else:
            print(f"    [ヒューリスティック] 連絡先 (y={avg_con:.1f}) が名前 (y={avg_nam:.1f}) の下にあります -> 正転")
            return False

    # フォールバック: 上半分に連絡先がある場合は非常に疑わしい
    if con_y:
        top_con = sum(1 for y in con_y if y < mid_y)
        if top_con > len(con_y) / 2: return True

    return False

def process_single_image(img_path, detector, reader, font):
    name = Path(img_path).stem
    out_dir = OUTPUT_BASE_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    img_bgr = cv2.imread(str(img_path))
    if img_bgr is None: return False, "Read fail"
        
    triggered, _, bbox = detector.detect(img_bgr)
    if bbox:
        crop_img = img_bgr[bbox["y1"]:bbox["y2"], bbox["x1"]:bbox["x2"]].copy()
    else:
        crop_img = img_bgr.copy()
        
    enhanced_img = simulate_enhance(crop_img)
    cv2.imwrite(str(out_dir / "step2_enhanced_img.jpg"), enhanced_img)
    
    # --- パス 1: 方向検出 ---
    p1_raw = reader.engine(enhanced_img)
    if isinstance(p1_raw, tuple): p1_raw = p1_raw[0]
    p1_std = standardize_ocr_results(p1_raw)
    
    if not p1_std:
        return False, "OCR Pass 1 found 0 lines"

    h_e, w_e = enhanced_img.shape[:2]
    is_upside_down = estimate_is_upside_down(p1_std, h_e)
    
    vis_img = enhanced_img
    final_results = p1_std
    pass_info = "Single-pass (Upright)"

    if is_upside_down:
        print(f"  [方向] 逆さまを検出しました。パス 2 (物理的な回転) を実行します...")
        vis_img = cv2.rotate(enhanced_img, cv2.ROTATE_180)
        p2_raw = reader.engine(vis_img)
        if isinstance(p2_raw, tuple): p2_raw = p2_raw[0]
        final_results = standardize_ocr_results(p2_raw)
        pass_info = "Double-pass (Rotated)"
    else:
        print(f"  [方向] 正転を検出しました。最適化: 2 パス目をスキップします。")

    # 最終的なキャンバスを保存
    cv2.imwrite(str(out_dir / "step3_final_canvas.jpg"), vis_img)

    # 可視化
    vis_det = draw_ocr_results(vis_img, final_results, "DETECTION ONLY", font, mode='det')
    cv2.imwrite(str(out_dir / "step4_det_only.jpg"), vis_det)
    
    vis_rec = create_rec_montage(vis_img, final_results, font)
    if vis_rec is not None: cv2.imwrite(str(out_dir / "step5_rec_snippets.jpg"), vis_rec)
        
    vis_final = draw_ocr_results(vis_img, final_results, f"FINAL OCR ({pass_info})", font, mode='both')
    cv2.imwrite(str(out_dir / "step6_final_ocr.jpg"), vis_final)
    
    # 結果の概要
    all_text = "\n".join([r[1] for r in final_results])
    fields, _ = parse_bcard_fields(all_text, final_results)
    with open(out_dir / "fields.txt", "w", encoding="utf-8") as f:
        f.write(f"Detected Orientation: {'Upside-down' if is_upside_down else 'Upright'}\n")
        f.write(f"Processing Mode: {pass_info}\n")
        for k, v in fields.items(): f.write(f"{k}: {v}\n")
    return True, f"Lines: {len(final_results)} ({pass_info})"

def main():
    parser = argparse.ArgumentParser(description="信頼性の高い 2 パス OCR バッチビジュアライザー。")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()

    image_paths = sorted(list(INPUT_DIR.glob("*.jpeg")) + list(INPUT_DIR.glob("*.jpg")) + list(INPUT_DIR.glob("*.png")))
    total_found = len(image_paths)
    if total_found == 0: return

    s, e = args.start, (args.end if args.end is not None else total_found)
    process_paths = image_paths[s:e]
    
    print(f"範囲 [{s}:{e}] を処理中 (標準化された OCR ロジック)。")
    
    import card.ocr
    card.ocr._READER = None 
    detector = CardYoloCapture(required_stable=1, cooldown=0)
    print("Initializing OCR Reader...")
    reader = get_reader()
    
    font = None
    try:
        if os.name == 'nt': font = ImageFont.truetype("msgothic.ttc", 18)
    except: pass

    batch_summary = []
    for i, img_path in enumerate(process_paths):
        print(f"[{s+i+1}/{total_found}] Processing {img_path.name}...")
        try:
            success, msg = process_single_image(img_path, detector, reader, font)
            batch_summary.append(f"{img_path.name}: {msg}")
        except:
            batch_summary.append(f"{img_path.name}: ERROR")
            traceback.print_exc()

    summary_file = OUTPUT_BASE_DIR / f"summary_raw_{s}_{e}.txt"
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(f"Batch Summary - {time.ctime()}\n\n")
        f.write("\n".join(batch_summary))

if __name__ == "__main__":
    main()
