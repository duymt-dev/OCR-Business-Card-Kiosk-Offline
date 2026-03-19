import os
import sys
import cv2
import time
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 出力ディレクトリを作成
OUTPUT_DIR = PROJECT_ROOT / "tests" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

from card.capture import CardYoloCapture
from card.ocr import get_reader
from card.processor import parse_bcard_with_bbox, parse_bcard_fields

def draw_ocr_results(img_np, ocr_results, title, font=None, mode='both'):
    """
    OCR 結果を描画するためのユーティリティ。
    mode 'det': ボックスのみ
    mode 'rec': ボックス + テキスト
    mode 'both': ボックス + テキスト + タイトル
    """
    vis_pil = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(vis_pil)
    
    for item in ocr_results:
        box = item[0]
        text = item[1]
        conf = item[2]
        b = np.array(box).astype(int)
        
        # ポリゴンを描画 (検出出力)
        draw.polygon([tuple(p) for p in b], outline="red", width=2)
        
        if mode != 'det':
            # テキストを描画 (認識出力)
            label = f"{text} ({conf:.2f})"
            if font:
                draw.text(tuple(b[0]), label, fill="blue", font=font)
            else:
                draw.text(tuple(b[0]), label, fill="blue")
            
    # タイトルを追加
    if mode == 'both':
        if font:
            draw.text((10, 10), title, fill="red", font=font)
        else:
            draw.text((10, 10), title, fill="red")
        
    return cv2.cvtColor(np.array(vis_pil), cv2.COLOR_RGB2BGR)

def create_rec_montage(img_np, ocr_results, font=None):
    """認識エンジンに送信されたテキストスニペットのモンタージュを作成します。"""
    if not ocr_results:
        return None
        
    snippets = []
    max_w = 0
    for item in ocr_results:
        box = item[0]
        text = item[1]
        # クロップ用のバウンディング矩形を取得
        b = np.array(box).astype(int)
        x_min, y_min = np.min(b, axis=0)
        x_max, y_max = np.max(b, axis=0)
        
        # 境界内にあることを確認
        h, w = img_np.shape[:2]
        x_min, y_min = max(0, x_min), max(0, y_min)
        x_max, y_max = min(w, x_max), min(h, y_max)
        
        if x_max > x_min and y_max > y_min:
            snippet = img_np[y_min:y_max, x_min:x_max].copy()
            # スニペットに小さな境界線を追加
            snippet = cv2.copyMakeBorder(snippet, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=[0,0,0])
            snippets.append((snippet, text))
            max_w = max(max_w, snippet.shape[1])

    if not snippets:
        return None

    # スニペットを垂直方向に並べる
    total_h = sum(s[0].shape[0] for s in snippets) + (len(snippets) * 20)
    canvas_w = max_w + 400
    canvas = np.full((total_h, canvas_w, 3), 255, dtype=np.uint8)
    
    current_y = 10
    canvas_pil = Image.fromarray(canvas)
    draw = ImageDraw.Draw(canvas_pil)
    
    for snip, txt in snippets:
        snip_h, snip_w = snip.shape[:2]
        # スニペットを貼り付け
        snip_rgb = cv2.cvtColor(snip, cv2.COLOR_BGR2RGB)
        canvas_pil.paste(Image.fromarray(snip_rgb), (10, current_y))
        
        # 隣にテキストを描画
        if font:
            draw.text((snip_w + 30, current_y + snip_h//4), f"-> {txt}", fill="black", font=font)
        else:
            draw.text((snip_w + 30, current_y + snip_h//4), f"-> {txt}", fill="black")
            
        current_y += snip_h + 10
        
    return cv2.cvtColor(np.array(canvas_pil), cv2.COLOR_RGB2BGR)

def simulate_enhance(img_np):
    smooth = cv2.bilateralFilter(img_np, 7, 35, 35)
    gray = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)
    enhanced = cv2.cvtColor(gray_clahe, cv2.COLOR_GRAY2BGR)
    enhanced = cv2.convertScaleAbs(enhanced, alpha=1.35, beta=5)
    kernel = np.array([[-0.4, -0.4, -0.4], [-0.4, 3.8, -0.4], [-0.4, -0.4, -0.4]], dtype=np.float32)
    enhanced = cv2.filter2D(enhanced, -1, kernel)
    return enhanced

def main():
    img_path = r"d:\bamboo_nissin\test_images\WIN_20260226_15_18_31_Pro.jpg"
    print(f"Loading {img_path}")
    frame_bgr = cv2.imread(img_path)
    if frame_bgr is None:
        print("Image not found!")
        return
        
    font = None
    try:
        if os.name == 'nt':
            font = ImageFont.truetype("msgothic.ttc", 18)
    except:
        pass

    print("Step 1: YOLO Detection")
    detector = CardYoloCapture(required_stable=1, cooldown=0)
    triggered, img_out, bbox = detector.detect(frame_bgr)
    
    if not bbox:
        print("名刺が検出されませんでした。画像全体を使用します。")
        crop_img = frame_bgr.copy()
    else:
        vis_yolo = frame_bgr.copy()
        cv2.rectangle(vis_yolo, (bbox["x1"], bbox["y1"]), (bbox["x2"], bbox["y2"]), (0, 255, 0), 3)
        cv2.imwrite(str(OUTPUT_DIR / "step1_yolo.jpg"), vis_yolo)
        crop_img = frame_bgr[bbox["y1"]:bbox["y2"], bbox["x1"]:bbox["x2"]].copy()

    # --- 強化パス (高画質) ---
    print("\nStep 2: Enhanced Pre-processing")
    enhanced_img = simulate_enhance(crop_img)
    cv2.imwrite(str(OUTPUT_DIR / "step2_enhanced_img.jpg"), enhanced_img)
    
    print("Step 3: Running OCR (Quality Profile)")
    import card.ocr
    card.ocr._READER = None 
    os.environ["OCR_PROFILE"] = "quality"
    os.environ["OCR_DET_THRESH"] = "0.15"
    os.environ["OCR_DET_BOX_THRESH"] = "0.25"
    
    reader = get_reader()
    # detail=1 は [bbox, text, conf, (angle_info...)] を返します
    results = reader.readtext(enhanced_img, detail=1)
    
    print(f"ステップ 4: 検出 (DET) の可視化 - {len(results)} 個のボックスが見つかりました")
    vis_det = draw_ocr_results(enhanced_img, results, "DETECTION ONLY (Boxes)", font, mode='det')
    cv2.imwrite(str(OUTPUT_DIR / "step4_det_only.jpg"), vis_det)
    
    print("Step 5: Visualizing Recognition (REC) - Text Snippets")
    vis_rec = create_rec_montage(enhanced_img, results, font)
    if vis_rec is not None:
        cv2.imwrite(str(OUTPUT_DIR / "step5_rec_snippets.jpg"), vis_rec)
        
    print("Step 6: Final Result (DET + REC)")
    vis_final = draw_ocr_results(enhanced_img, results, "FINAL OCR (DET + REC)", font, mode='both')
    cv2.imwrite(str(OUTPUT_DIR / "step6_final_ocr.jpg"), vis_final)

    # フィールドの出力
    all_text = "\n".join([r[1] for r in results])
    fields, _ = parse_bcard_fields(all_text, results)
    print("\n--- 抽出されたフィールド ---")
    for k, v in fields.items():
        print(f"{k:<15}: {v}")

    print("\n完了！結果は tests/output/ にあります")
    print("- step4_det_only.jpg: Just the text box detection results")
    print("- step5_rec_snippets.jpg: The text snippets read by recognition engine")
    print("- step6_final_ocr.jpg: Combined output with bounding boxes and text labels")

if __name__ == "__main__":
    main()
