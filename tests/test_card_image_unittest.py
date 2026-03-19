import os
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# リポジトリルートなどからテストを実行できるようにする
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from card.ocr import get_reader
from card.processor import parse_bcard_with_bbox, parse_bcard_fields

# 環境変数を指定せずに固定の画像パスを使用する場合は、ここを直接編集してください。
HARDCODED_IMAGE_PATH = r"test_images\WIN_20260226_16_46_25_Pro.jpg"
REPORT_PATH = Path(os.getenv("CARD_SINGLE_REPORT", "reports/card_image_single_report.txt"))


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def _write_single_report(image_path: Path, ocr_results: list, fields_bbox: dict, fields_text: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("CARD IMAGE SINGLE TEST REPORT")
    lines.append("=" * 40)
    lines.append(f"Image: {image_path}")
    lines.append(f"OCR lines: {len(ocr_results)}")
    lines.append("")
    lines.append("fields_bbox:")
    for k in ["full_name", "title", "email", "company", "phone", "address"]:
        lines.append(f"  {k}: {(fields_bbox.get(k) or '').strip()}")
    lines.append("")
    lines.append("fields_text:")
    for k in ["full_name", "title", "email", "company", "phone", "address"]:
        lines.append(f"  {k}: {(fields_text.get(k) or '').strip()}")
    lines.append("")
    lines.append("ocr_lines:")
    for bbox, text, conf in ocr_results:
        t = (text or "").strip()
        if not t:
            continue
        lines.append(f"  - [{float(conf):.2f}] {t}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


class TestCardImagePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        img_path = os.getenv("CARD_TEST_IMAGE", "").strip() or HARDCODED_IMAGE_PATH.strip()
        if not img_path:
            raise unittest.SkipTest("画像ベースのテストを実行するには、CARD_TEST_IMAGE を設定するか HARDCODED_IMAGE_PATH を編集してください")

        cls.image_path = Path(img_path)
        if not cls.image_path.exists():
            raise unittest.SkipTest(f"Image not found: {cls.image_path}")

        img = Image.open(cls.image_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        cls.np_img = np.array(img)

        cls.reader = get_reader()
        cls.ocr_results = cls.reader.readtext(cls.np_img, detail=1, paragraph=False)

        cls.raw_text = "\n".join(
            t.strip() for (bbox, t, conf) in cls.ocr_results if t and t.strip() and float(conf) > 0.3
        ).strip()

        cls.fields_bbox = parse_bcard_with_bbox(cls.ocr_results)
        cls.fields_text, cls.lines = parse_bcard_fields(cls.raw_text, cls.ocr_results)
        _write_single_report(cls.image_path, cls.ocr_results, cls.fields_bbox, cls.fields_text)

    def test_ocr_returns_list(self):
        self.assertIsInstance(self.ocr_results, list)
        self.assertGreater(len(self.ocr_results), 0, "OCR returned empty results")

    def test_fields_have_expected_keys(self):
        required = {"full_name", "title", "email", "company", "phone", "address"}
        self.assertTrue(required.issubset(set(self.fields_bbox.keys())))
        self.assertTrue(required.issubset(set(self.fields_text.keys())))

    def test_optional_expected_values(self):
        checks = [
            ("CARD_EXPECT_EMAIL", "email"),
            ("CARD_EXPECT_PHONE", "phone"),
            ("CARD_EXPECT_COMPANY", "company"),
            ("CARD_EXPECT_FULL_NAME", "full_name"),
        ]

        for env_name, field_name in checks:
            expected = os.getenv(env_name, "").strip()
            if not expected:
                continue

            got_bbox = _normalize(self.fields_bbox.get(field_name, ""))
            got_text = _normalize(self.fields_text.get(field_name, ""))
            exp = _normalize(expected)

            self.assertTrue(
                exp in got_bbox or exp in got_text,
                msg=(
                    f"フィールド '{field_name}' に '{expected}' が含まれていることを期待しました。 "
                    f"bbox='{self.fields_bbox.get(field_name, '')}', "
                    f"text='{self.fields_text.get(field_name, '')}'"
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
