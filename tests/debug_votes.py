import os
import sys
import re
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
from card.ocr import get_reader

def estimate_is_upside_down_debug(rows, img_h):
    if not rows: return False
    cls_180_votes, cls_0_votes = 0, 0
    top_contact, bot_contact = 0, 0
    top_name, bot_name = 0, 0
    top_company, bot_company = 0, 0
    mid_y = img_h / 2
    
    for bbox, text, conf, angle_info in rows:
        if not text: continue
        yc = sum(p[1] for p in bbox) / 4
        is_top = yc < mid_y
        t_low = text.lower()
        has_contact = bool(re.search(r"@|\.com|\.vn|tel:|fax:|\d{3,}-\d{3,}", t_low))
        has_name = bool(len(text) <= 8 and re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", text))
        has_company = bool(re.search(r"有限会社|株式会社|合同会社|公司|ltd|limited|inc|corp|corporation", t_low))
        
        if has_contact:
            if is_top: top_contact += 1
            else: bot_contact += 1
        if has_name:
            if is_top: top_name += 1
            else: bot_name += 1
        if has_company:
            if is_top: top_company += 1
            else: bot_company += 1
        
        sig = []
        if has_contact: sig.append("CON")
        if has_name: sig.append("NAM")
        if has_company: sig.append("CPY")
        if sig: print(f"  {'TOP' if is_top else 'BOT'} | {','.join(sig)} | {text}")

    print(f"Votes: CLS(180={cls_180_votes}, 0={cls_0_votes}) CON(T={top_contact}, B={bot_contact}) NAM(T={top_name}, B={bot_name}) CPY(T={top_company}, B={bot_company})")
    
    decisions = []
    if cls_180_votes > cls_0_votes: decisions.append("CLS_180")
    if bot_company > top_company: decisions.append("BOT_CPY")
    if top_contact > bot_contact and (bot_name > top_name or bot_company >= top_company): decisions.append("TOP_CON_AND_BOT_OTHER")
    if top_contact > bot_contact and top_contact >= 1: decisions.append("TOP_CON_ONLY")
    
    print(f"Decisions triggered: {decisions}")
    return len(decisions) > 0

def main():
    image_names = ["REG_12-03-2026_21-11-21_bcard.jpeg", "REG_12-03-2026_21-14-10_bcard.jpeg"]
    reader = get_reader()
    
    for name in image_names:
        img_path = str(PROJECT_ROOT / "test_images_registrations" / name)
        print(f"\nAnalyzing {name}:")
        img = cv2.imread(img_path)
        if img is None:
            print("  Failed to read")
            continue
        results = reader.readtext(img, detail=1)
        h = img.shape[0]
        estimate_is_upside_down_debug(results, h)

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
