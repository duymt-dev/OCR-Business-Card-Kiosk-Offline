import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from card.ocr import get_reader

def check_format():
    image_name = "REG_12-03-2026_21-11-21_bcard.jpeg"
    img_path = str(PROJECT_ROOT / "test_images_registrations" / image_name)
    img = cv2.imread(img_path)
    if img is None: return
    
    reader = get_reader()
    raw_results = reader.engine(img)
    if isinstance(raw_results, tuple): raw_results = raw_results[0]
    
    if raw_results:
        item = raw_results[0]
        print(f"Item Type: {type(item)}")
        print(f"Item Len: {len(item)}")
        print(f"Item Content: {item}")
        for i, val in enumerate(item):
            print(f"  [{i}]: {type(val)} = {val}")

if __name__ == "__main__":
    check_format()
