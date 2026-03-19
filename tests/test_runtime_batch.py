import argparse
import os
import sys
import time
import cv2
import numpy as np
import traceback
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 入力と出力の設定
INPUT_DIR = PROJECT_ROOT / "test_images_registrations"
OUTPUT_BASE_DIR = PROJECT_ROOT / "tests" / "runtime_output"
OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)

from card.ocr import get_reader
from card.processor import parse_bcard_with_bbox

def draw_ocr_results(img_np, ocr_results, title, font=None):
    """画像上にバウンディングボックスとテキストを描画します。"""
    vis_pil = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(vis_pil)
    
    for item in ocr_results:
        # 標準: (bbox, text, score, angle_info)
        box, text, conf = item[0], item[1], item[2]
        b = np.array(box).astype(int)
        draw.polygon([tuple(p) for p in b], outline="red", width=2)
        
        label = f"{text} ({conf:.2f})"
        if font:
            draw.text(tuple(b[0]), label, fill="blue", font=font)
        else:
            draw.text(tuple(b[0]), label, fill="blue")
            
    if font:
        draw.text((10, 10), title, fill="red", font=font)
    else:
        draw.text((10, 10), title, fill="red")
        
    return cv2.cvtColor(np.array(vis_pil), cv2.COLOR_RGB2BGR)

def process_single_image(img_path, reader, font):
    name = Path(img_path).stem
    out_dir = OUTPUT_BASE_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    
    img_bgr = cv2.imread(str(img_path))
    if img_bgr is None:
        return False, "Read fail"
        
    # reader のために BGR を PIL に変換 (application.py のスタイル)
    img_pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    
    # 1. application.py と同じ処理を実行
    t0 = time.time()
    
    if reader.engine is None:
        return False, "Reader engine is NONE"
        
    np_img = np.array(img_pil.convert("RGB"))
    ocr_results = reader.readtext(np_img, detail=1, paragraph=False)
    print(f"  - Raw OCR lines found: {len(ocr_results)}")
    fields, normalized_ocr = parse_bcard_with_bbox(ocr_results, return_full_results=True)
    
    elapsed = time.time() - t0
    
    # 2. 可視化 (リクエスト通り 1 つの画像のみ)
    # 注: normalized_ocr は処理済み画像の座標系を使用します
    # 可視化のために、画像サイズが変更されている場合は reader が使用した画像を再現する必要がある場合があります
    
    # より単純な可視化を、元の画像 (または可能な場合はリサイズされた画像) に対して行います
    # 実際、reader.readtext は内部的にリサイズを行う可能性があります。
    # 「step6_final_ocr.jpg」に忠実にするために、キャンバス上に描画します。
    
    vis_img = draw_ocr_results(np_img, normalized_ocr, f"RUNTIME OCR - {name}", font)
    cv2.imwrite(str(out_dir / "final_ocr.jpg"), vis_img)
    
    # 3. フィールドを txt に保存
    with open(out_dir / "fields.txt", "w", encoding="utf-8") as f:
        f.write(f"Source: {img_path.name}\n")
        f.write(f"Time: {elapsed:.3f}s\n")
        f.write("-" * 20 + "\n")
        for k, v in fields.items():
            f.write(f"{k}: {v}\n")
        
        # 4. OCR 候補を追加
        f.write("\n" + "=" * 20 + " OCR CANDIDATES " + "=" * 20 + "\n")
        for i, item in enumerate(normalized_ocr, 1):
            # item は (bbox, text, score, angle_info) です
            txt = item[1]
            conf = item[2]
            f.write(f"[{i:02d}] {txt} (conf={conf:.2f})\n")
            
    return True, f"Done in {elapsed:.3f}s"

def main():
    parser = argparse.ArgumentParser(description="Runtime Batch Test for Business Card OCR")
    parser.add_argument("--start", type=int, default=1, help="Start index (1-based)")
    parser.add_argument("--end", type=int, default=None, help="End index (1-based, inclusive)")
    args = parser.parse_args()

    print("Starting Runtime Batch Test...")
    
    image_paths = sorted(list(INPUT_DIR.glob("*.jpeg")) + list(INPUT_DIR.glob("*.jpg")) + list(INPUT_DIR.glob("*.png")))
    total_found = len(image_paths)
    if total_found == 0:
        print(f"No images found in {INPUT_DIR}")
        return

    # 範囲選択の処理
    start_idx = max(0, args.start - 1)
    end_idx = args.end if args.end is not None else total_found
    
    selected_paths = image_paths[start_idx:end_idx]
    total_selected = len(selected_paths)

    print(f"Found {total_found} images. Processing range [{args.start} to {end_idx}] ({total_selected} images).")
    print("Initializing OCR Reader...")
    reader = get_reader()
    
    font = None
    try:
        # Windows で日本語をサポートするために MS ゴシックを使用してみる
        if os.name == 'nt':
            font = ImageFont.truetype("msgothic.ttc", 16)
    except:
        pass

    batch_summary = []
    for i, img_path in enumerate(selected_paths):
        curr_num = start_idx + i + 1
        print(f"[{curr_num}/{total_found}] Processing {img_path.name}...")
        try:
            success, msg = process_single_image(img_path, reader, font)
            batch_summary.append(f"{img_path.name}: {msg}")
        except Exception as e:
            batch_summary.append(f"{img_path.name}: ERROR - {str(e)}")
            traceback.print_exc()

    summary_file = OUTPUT_BASE_DIR / "batch_summary.txt"
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(f"Runtime Batch Summary - {time.ctime()}\n\n")
        f.write("\n".join(batch_summary))
    
    print(f"Batch finished. Results in {OUTPUT_BASE_DIR}")

if __name__ == "__main__":
    main()
