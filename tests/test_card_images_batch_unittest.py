import os
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from card.ocr import get_reader
from card.processor import parse_bcard_with_bbox, parse_bcard_fields

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
# 環境変数を指定せずに固定のフォルダーパスを使用する場合は、ここを直接編集してください。
HARDCODED_IMAGE_DIR = r"T:\duong_dan_folder_anh"
DEFAULT_DIRS = [HARDCODED_IMAGE_DIR, "test_images", "reports/images"]
REPORT_PATH = Path(os.getenv("CARD_TEST_REPORT", "reports/card_image_batch_report.txt"))
OCR_MIN_CONF = float(os.getenv("CARD_REPORT_OCR_MIN_CONF", "0.0"))


def _pick_image_dir() -> Path | None:
    env_dir = os.getenv("CARD_TEST_IMAGES_DIR", "").strip()
    candidates = [env_dir] if env_dir else DEFAULT_DIRS

    for c in candidates:
        p = Path(c)
        if p.exists() and p.is_dir():
            images = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
            if images:
                return p

    # ユーザーが明示的にディレクトリを設定した場合は、レポートに明記されるよう、空であってもそのディレクトリを返します。
    if env_dir:
        return Path(env_dir)
    for c in DEFAULT_DIRS:
        p = Path(c)
        if p.exists() and p.is_dir():
            return p
    return None


def _list_images(folder: Path) -> list[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS])


def _write_report(folder: Path | None, results: list[dict], note: str = "") -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    ok_count = sum(1 for r in results if r.get("status") == "OK")
    fail_count = sum(1 for r in results if r.get("status") == "FAIL")

    lines = []
    lines.append("CARD IMAGE BATCH TEST REPORT")
    lines.append("=" * 40)
    lines.append(f"Image folder: {folder if folder else 'N/A'}")
    lines.append(f"Total: {len(results)} | OK: {ok_count} | FAIL: {fail_count}")
    if note:
        lines.append(f"Note: {note}")
    lines.append("")

    for idx, r in enumerate(results, start=1):
        lines.append(f"[{idx}] {r.get('file', '<unknown>')} | {r.get('status')}")
        if r.get("error"):
            lines.append(f"  error: {r['error']}")
        fields = r.get("fields", {})
        if fields:
            for k in ["full_name", "title", "email", "company", "phone", "address"]:
                v = (fields.get(k) or "").strip()
                lines.append(f"  {k}: {v}")
        ocr_lines = r.get("ocr_lines", [])
        if ocr_lines:
            lines.append("  ocr_lines:")
            for l in ocr_lines:
                lines.append(f"    - [{l.get('conf', 0.0):.2f}] {l.get('text', '')}")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


class TestCardImageBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = _pick_image_dir()
        cls.results = []

        if cls.folder is None:
            _write_report(None, [], note="画像を含むフォルダーが見つかりませんでした。'test_images' を作成して画像を追加してください。")
            raise unittest.SkipTest("画像フォルダーが見つかりませんでした。test_images を使用するか CARD_TEST_IMAGES_DIR を設定してください。")

        images = _list_images(cls.folder)
        if not images:
            _write_report(cls.folder, [], note="フォルダーは存在しますが、サポートされている画像ファイルが見つかりませんでした。")
            raise unittest.SkipTest(f"画像が見つかりませんでした: {cls.folder}")

        reader = get_reader()

        for img_path in images:
            try:
                img = Image.open(img_path)
                img = ImageOps.exif_transpose(img).convert("RGB")
                np_img = np.array(img)

                ocr_results = reader.readtext(np_img, detail=1, paragraph=False)
                ocr_lines = [
                    {"conf": float(conf), "text": text.strip()}
                    for bbox, text, conf in ocr_results
                    if text and text.strip() and float(conf) >= OCR_MIN_CONF
                ]
                raw_text = "\n".join(
                    t.strip() for (bbox, t, conf) in ocr_results if t and t.strip() and float(conf) > 0.3
                ).strip()

                fields_bbox = parse_bcard_with_bbox(ocr_results)
                fields_text, _ = parse_bcard_fields(raw_text, ocr_results)

                merged = {
                    "full_name": fields_bbox.get("full_name") or fields_text.get("full_name") or "",
                    "title": fields_bbox.get("title") or fields_text.get("title") or "",
                    "email": fields_bbox.get("email") or fields_text.get("email") or "",
                    "company": fields_bbox.get("company") or fields_text.get("company") or "",
                    "phone": fields_bbox.get("phone") or fields_text.get("phone") or "",
                    "address": fields_bbox.get("address") or fields_text.get("address") or "",
                }

                status = "OK" if len(ocr_results) > 0 else "FAIL"
                error = "" if status == "OK" else "OCR が空のリストを返しました"

                cls.results.append({
                    "file": img_path.name,
                    "status": status,
                    "error": error,
                    "fields": merged,
                    "ocr_lines": ocr_lines,
                })
            except Exception as ex:
                cls.results.append({
                    "file": img_path.name,
                    "status": "FAIL",
                    "error": str(ex),
                    "fields": {},
                    "ocr_lines": [],
                })

        _write_report(cls.folder, cls.results)

    def test_no_failures(self):
        failed = [r for r in self.results if r.get("status") == "FAIL"]
        self.assertEqual(
            len(failed),
            0,
            msg=f"Found {len(failed)} failed image(s). See {REPORT_PATH}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
