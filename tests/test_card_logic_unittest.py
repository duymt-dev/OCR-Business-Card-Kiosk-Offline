import logging
import sys
import unittest
from pathlib import Path

# リポジトリルートなどからテストを実行できるようにする
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING)

from card.utils import normalize_email_text, clean_ocr_text
from card.logic import (
    group_ocr_by_y,
    first_email,
    all_emails,
    first_phone,
    all_phones,
    extract_company,
    extract_address,
    guess_full_name,
)
from card.processor import extract_email_domain, format_domain_as_company, parse_bcard_with_bbox, parse_bcard_fields


class TestCardUtils(unittest.TestCase):
    def test_normalize_email_text(self):
        self.assertEqual(normalize_email_text("  foo @ example . com  "), "foo@example.com")

    def test_clean_ocr_text_keeps_phone_punct(self):
        s = "Tel: +84 (0) 123-456 !!!"
        self.assertEqual(clean_ocr_text(s), "Tel: +84 (0) 123-456")


class TestCardLogic(unittest.TestCase):
    def test_first_email_uses_normalization(self):
        txt = "Email: foo @ example . com\n"
        self.assertEqual(first_email(txt), "foo@example.com")
        self.assertEqual(all_emails(txt), ["foo@example.com"])

    def test_all_emails_multiple(self):
        txt = "Email: a@x.com\nb@y.com\nmail: c.z@foo.net"
        self.assertEqual(all_emails(txt), ["a@x.com", "b@y.com", "c.z@foo.net"])

    def test_phone_extraction(self):
        txt = "Tel: +84(0222)3734995\nMobile: 0901 234 567"
        self.assertEqual(first_phone(txt), "+84(0222)3734995")
        self.assertEqual(all_phones(txt), ["842223734995", "0901234567"])

    def test_phone_extraction_international_codes(self):
        txt = "Mobile:+65 9390 0599\nTel:+6560502391\nM/P:+66822190915"
        self.assertEqual(first_phone(txt), "+65 9390 0599")
        self.assertEqual(all_phones(txt), ["6593900599", "6560502391", "66822190915"])

    def test_group_ocr_by_y(self):
        ocr = [
            ([[0, 0], [50, 0], [50, 10], [0, 10]], "HELLO", 0.9),
            ([[60, 1], [100, 1], [100, 11], [60, 11]], "WORLD", 0.9),
            ([[0, 30], [40, 30], [40, 40], [0, 40]], "LINE2", 0.9),
        ]
        self.assertEqual(group_ocr_by_y(ocr, y_thresh=5, x_thresh=15), ["HELLO WORLD", "LINE2"])

    def test_extract_company_simple(self):
        lines = [
            "TEL: +84 912 345 678",
            "ACME CO., LTD.",
        ]
        self.assertEqual(extract_company(lines), "ACME CO., LTD.")

    def test_extract_address_simple(self):
        lines = [
            "ACME CO., LTD.",
            "123 Nguyen Trai, District 1, Ho Chi Minh City",
        ]
        self.assertEqual(extract_address(lines), "123 Nguyen Trai, District 1, Ho Chi Minh City")

    def test_first_email_prioritizes_personal_account(self):
        text = "wapotech@wapotech.com.vn buivantrong@wapotech.com.vn"
        email = first_email(text)
        self.assertEqual(email, "buivantrong@wapotech.com.vn")

    def test_guess_full_name_handles_merged_latin_token_and_blocks_phone_label(self):
        lines = [
            "SASAKIANTHONYYUTAKA",
            "Di dōng",
            "Truòng Phòng Kinh Doanh",
        ]
        name = guess_full_name(
            lines,
            email="y.sasaki@hogetsu.com.vn",
            company="CONG TY TNHH HOGETSU VIET NAM"
        )
        self.assertEqual(name, "SASAKI ANTHONYYUTAKA")

    def test_guess_full_name_merges_split_kanji_fragments(self):
        """別々の OCR 行にある「弓庭」と「一廣」が「弓庭一廣」にマージされることを確認"""
        lines = [
            "技術本部",
            "スマートファクトリー推進部長",
            "一廣",
            "弓庭",
            "株式会社たけびし",
            "ソリューションをご紹介",
            "615-8501京都市右京区西京極豆田町29",
            "E-mail:Kazuhiro.Yuba@takebishi.co.jp",
        ]
        name = guess_full_name(lines, email="Kazuhiro.Yuba@takebishi.co.jp", company="株式会社たけびし")
        # デフォルトのフォーマットでは、検出された場合にメールからラテン文字の名前が追加されます
        self.assertIn("弓庭一廣", name)
        self.assertIn("KAZUHIRO YUBA", name)

    def test_guess_full_name_splits_merged_token_by_email(self):
        """HIDEKATSUKURODA + kurodah@erm.jp が HIDEKATSU KURODA に分割されることを確認"""
        lines = ["HIDEKATSUKURODA", "General Manager"]
        name = guess_full_name(lines, email="kurodah@erm.jp", company="EIWA")
        self.assertIn("HIDEKATSU KURODA", name)

    def test_guess_full_name_splits_camel_case(self):
        """SasakiAnthonyYutaka が Sasaki Anthony Yutaka に分割されることを確認"""
        lines = ["SasakiAnthonyYutaka", "Director"]
        # CamelCase ロジックを強制するためのメールアドレスの不一致
        name = guess_full_name(lines, email="diff@example.com", company="Any")
        self.assertIn("Sasaki Anthony Yutaka", name)

    def test_guess_full_name_blocks_company_lines(self):
        """MITSUBISHI ELECTRICVIETNAM CO.,LTD. が名前の候補から除外されることを確認"""
        lines = ["MITSUBISHI ELECTRICVIETNAM CO.,LTD.", "NGOTIENDINH"]
        # NGOTIENDINH は 1 単語であり、メール/CamelCase がない場合は難しいかもしれませんが、
        # 会社名の行は確実に選択されないようにする必要があります。
        name = guess_full_name(lines, email=None, company=None)
        if name:
            self.assertNotIn("MITSUBISHI ELECTRICVIETNAM CO.,LTD.", name)


class TestCardProcessorHelpers(unittest.TestCase):
    def test_extract_email_domain_handles_co_jp(self):
        self.assertEqual(extract_email_domain("user@arktake.co.jp"), "arktake")

    def test_format_domain_as_company(self):
        self.assertEqual(format_domain_as_company("saomaisoft.com"), "SAOMAISOFT")
        self.assertEqual(format_domain_as_company("acme-ltd.com"), "ACME LTD.")

    def test_company_refine_ignores_strong_marker_when_domain_mismatch(self):
        lines = [
            "Wapotech Technology Equiqment",
            "forElectricity,Waterand EnvironmentJSC",
            "WAPOTECH",
            "Director",
            "Mobile:0917866899",
            "Tel:+844-6523795Fax:+844-6523796",
            "wapotech@wapotech.com.vn",
            "Website:www.wapotech.com.vn",
        ]
        ocr = []
        for i, line in enumerate(lines):
            y = i * 20
            bbox = [[0, y], [300, y], [300, y + 10], [0, y + 10]]
            ocr.append((bbox, line, 0.99))
        fields = parse_bcard_with_bbox(ocr)
        self.assertIn("wapotech", fields["company"].lower())
        self.assertNotIn("forelectricity", fields["company"].lower())

    def test_bbox_merge_two_kanji_lines_into_full_name(self):
        lines = [
            "E-mail:takuya.okuda.eh@yodohen.co.jp",
            "奥田",
            "TAKUYA OKUDA",
            "卓也",
            "課長",
            "淀川変圧器株式会社",
        ]
        ocr = []
        for i, line in enumerate(lines):
            y = i * 20
            if line == "奥田":
                y = 120
            if line == "TAKUYA OKUDA":
                y = 145
            if line == "卓也":
                y = 170
            bbox = [[100, y], [360, y], [360, y + 12], [100, y + 12]]
            ocr.append((bbox, line, 0.99))
        fields = parse_bcard_with_bbox(ocr)
        self.assertIn("奥田卓也", fields["full_name"])
        self.assertIn("TAKUYA OKUDA", fields["full_name"])

    def test_bbox_merge_two_kanji_lines_with_x_offset(self):
        lines = [
            "E-mail:takuya.okuda.eh@yodohen.co.jp",
            "奥田",
            "TAKUYA OKUDA",
            "卓也",
            "課長",
            "淀川変圧器株式会社",
        ]
        ocr = []
        for i, line in enumerate(lines):
            y = i * 20
            x1 = 100
            x2 = 360
            if line == "奥田":
                y = 140
                x1, x2 = 80, 260
            if line == "TAKUYA OKUDA":
                y = 162
                x1, x2 = 120, 380
            if line == "卓也":
                y = 186
                x1, x2 = 210, 390
            bbox = [[x1, y], [x2, y], [x2, y + 12], [x1, y + 12]]
            ocr.append((bbox, line, 0.99))
        fields = parse_bcard_with_bbox(ocr)
        self.assertIn("奥田卓也", fields["full_name"])

    def test_parse_fields_does_not_drop_name_by_email_localpart(self):
        txt = "\n".join([
            "Wapotech Technology Equiqment",
            "forElectricity,Waterand EnvironmentJSC",
            "BUI VAN TRONG",
            "Director",
            "Long BienDistrict,Hanoi city",
            "Mobile:0917866899",
            "wapotech@wapotech.com.vn",
            "buivantrong@wapotech.com.vn",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertEqual(fields["full_name"], "BUI VAN TRONG")

    def test_parse_fields_split_name_token_by_email(self):
        txt = "\n".join([
            "KDDI Vietnam Co., Ltd.",
            "BUI THI THANHYEN",
            "Assistant Manager",
            "Email: yen.buithanh@kddivietnam.com",
            "Mobile:+84907119688",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertEqual(fields["full_name"], "BUI THI THANH YEN")

    def test_parse_fields_refine_company_by_email_domain_with_ocr(self):
        lines = [
            "KDDI HANOI HEAD OFFICE",
            "Add:15h Floor,ICON 4 Building,243A La Thanh StrLang Thuong",
            "Ward.Dong Da Dist, Hanoicity.Vietnam.",
            "KDDI Vietnam",
            "Email:yen.buithanh@kddivietnam.com",
        ]
        txt = "\n".join(lines)
        ocr = []
        for i, line in enumerate(lines):
            y = i * 20
            bbox = [[0, y], [500, y], [500, y + 10], [0, y + 10]]
            ocr.append((bbox, line, 0.99))
        fields, _ = parse_bcard_fields(txt, ocr_results=ocr)
        self.assertIn("kddi", fields["company"].lower())
        self.assertNotIn("add:", fields["company"].lower())

    def test_parse_fields_company_removes_non_company_noise_chunks(self):
        txt = "\n".join([
            "KDDI VIETNAM CORPORATION -HANOI HEAD OFFICE KDL",
            "A:o, Ward, Dong Da Dist., Hanoi city, Vietnam.",
            "Email:abc@kddivietnam.com",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertIn("kddi vietnam corporation", fields["company"].lower())
        self.assertNotIn("head office", fields["company"].lower())
        self.assertNotIn("ward", fields["company"].lower())

    def test_parse_fields_company_trims_jp_department_tail_after_legal_entity(self):
        txt = "\n".join([
            "豊田通商株式会社术デ一機械部",
            "E-mail :hideyuki_haba@toyotsu-machinery.co.jp",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertEqual(fields["company"], "豊田通商株式会社")

    def test_parse_fields_company_keeps_clean_jp_legal_entity_name(self):
        txt = "\n".join([
            "株式会社豊通マシナリー",
            "E-mail :hideyuki_haba@toyotsu-machinery.co.jp",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertEqual(fields["company"], "株式会社豊通マシナリー")

    def test_parse_fields_prefers_longer_strong_company_line(self):
        txt = "\n".join([
            "CO.,LTD",
            "HIRAIWA VIETNAM CO.,LTD",
            "Chief Representative",
        ])
        fields, _ = parse_bcard_fields(txt, ocr_results=[])
        self.assertIn("HIRAIWA VIETNAM", fields["company"].upper())
        self.assertIn("CO.", fields["company"].upper())
        self.assertIn("LTD", fields["company"].upper())


if __name__ == "__main__":
    unittest.main(verbosity=2)
