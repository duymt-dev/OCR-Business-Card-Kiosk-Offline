import os
import shutil
from pathlib import Path

def aggregate_cards():
    # スクリプトの場所に基づいてプロジェクトルートを特定
    registrations_dir = project_root / "registrations"
    target_dir = project_root / "test_images_registrations"
    target_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Searching for bcard.jpeg in {registrations_dir}...")
    
    count = 0
    # registrations/ 内のすべてのサブディレクトリをスキャン
    for reg_folder in registrations_dir.iterdir():
        if reg_folder.is_dir():
            bcard_path = reg_folder / "bcard.jpeg"
            if bcard_path.exists():
                # 重複を避けるために登録フォルダー名をプレフィックスとして使用
                new_filename = f"{reg_folder.name}_bcard.jpeg"
                shutil.copy2(bcard_path, target_dir / new_filename)
                count += 1
                if count % 50 == 0:
                    print(f"  Copied {count} images...")
                    
    print(f"\nDone! Copied {count} business card images to {target_dir}")

if __name__ == "__main__":
    aggregate_cards()
