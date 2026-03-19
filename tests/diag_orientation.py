import os
import sys
from pathlib import Path

# Set environment variables
os.environ["OCR_PROFILE"] = "quality"
os.environ["OCR_USE_ANGLE_CLS"] = "1"

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from card.ocr import get_reader

def main():
    img_path = str(PROJECT_ROOT / "test_images_registrations" / "REG_12-03-2026_21-33-15_bcard.jpeg")
    img = cv2.imread(img_path)
    if img is None:
        print("Failed to read image")
        return

    reader = get_reader()
    results = reader.readtext(img, detail=1)
    
    print(f"Results for {img_path}:")
    h, w = img.shape[:2]
    mid_y = h / 2
    for i, (bbox, text, score, angle_info) in enumerate(results):
        yc = sum(p[1] for p in bbox) / 4
        pos = "TOP" if yc < mid_y else "BOT"
        print(f"[{i}] {pos} (y={yc:.1f}) {text} | score={score:.2f} | angle={angle_info}")

if __name__ == "__main__":
    main()
