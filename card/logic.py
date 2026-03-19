import re
import logging
from typing import Optional
from card.constants import (
    TITLE_RE, EMAIL_RE, PHONE_RE, PHONE_DOMESTIC_RE, PHONE_NO_LABEL_RE,
    COMPANY_HINTS, ADDRESS_HINTS, JP_ADD_RE, JP_ADDR_SUFFIX_RE,
    VN_ACCENT_RE, JP_CHAR_RE, JP_NAME_RE, KANA_RE, HONORIFIC_STRIP_RE,
    SpatialConfig, COMPANY_STRONG_RE, GENERIC_EMAIL_USERS,
    FAX_HINT_RE, TAX_HINT_RE, COMPANY_LEGAL_RE, COMPANY_INDUSTRY_RE,
    URL_RE, PHONE_MARKERS, FAX_MARKERS, JP_BAD_NAME_CHARS_RE, MISC_NOISE_RE
)
from card.utils import normalize_email_text, should_skip_noise_line

logger = logging.getLogger("kiosk.card.logic")

# ---------------------------------------------------------------------------
# wordsegment (ステージ 2) — インポート時に1回読み込み、失敗時はスキップ
# ---------------------------------------------------------------------------
try:
    from wordsegment import load as _ws_load, segment as _ws_segment
    _ws_load()
    _WORDSEGMENT_OK = True
    logger.debug("wordsegment loaded OK")
except Exception:
    _WORDSEGMENT_OK = False
    logger.debug("wordsegment not available, Stage-2 disabled")

# ステージ-1: 既知のビジネス/住所キーワード — その前にスペースを挿入
_SPACE_KEYWORDS_RE = re.compile(
    r"(?<=[A-Za-z])("
    # Legal suffixes
    r"CO\.?|LTD\.?|CORP\.?|INC\.?|LLC|JSC|PLC|GMBH|KK|VINA|"
    # Countries / regions
    r"VIETNAM|VIET NAM|JAPAN|CHINA|KOREA|INDIA|SINGAPORE|THAILAND|"
    # Common business nouns
    r"COMPONENTS?|ELECTRONICS?|TECHNOLOGY|TECHNOLOGIES|INDUSTRIAL|"
    r"TRADING|SOLUTIONS?|MANUFACTURING|INTERNATIONAL|HOLDINGS?|GROUP|"
    r"COMPANY|ENTERPRISE|FACTORY|WORKS|SYSTEMS?|SERVICES?|LABS?|"
    # Address keywords
    r"ZONE|PARK|WARD|DISTRICT|PROVINCE|CITY|STREET|ROAD|AVENUE|QUARTER|MAI" # Added MAI to avoid split if needed? No, wait.
    r")(?=[A-Z(,.[\s]|$)",
    re.IGNORECASE
)

# ロジック内部で使用される特定の正規表現
JP_TITLE_WORDS_RE = re.compile(
    r"(本部|部門|部署|事業部|管理|総務|経理|人事|営業|技術|開発|製造|品質|企画|法務|広報|購買|物流|生産|研究|設計|工場|支店|本社|拠点|"
    r"推進部|推進室|企画室|開発室|事務局|総括|専門職|外国部|大規模法人|法人部門|法人部|"
    r"社長|副社長|専務|常務|取締役|代表|部長|次長|課長|係長|主任|主幹|参事|参与|嘱託|理事|監事|幹事|主事|代理|補佐|主査|室長|所長|役員|執行|事長|副理|副長|日越協同|勤務先|"
    r"株式|有限|合同|合資|相互|一般|公益|医療|学校|監査|法人|協会|組合|連盟|学会|"
    r"マネージャー|リーダー|チーフ|スタッフ|DX認定|住友電工|ホーチミン|ハノイ|ダナン|ハイフォン|カントー|東京|大阪|京都|横浜)"
)

PHONE_MARKERS_RE = re.compile(PHONE_MARKERS, re.I)

# 役職や名前の接頭辞を削除するための正規表現
# 非ASCII（日本語）文字が含まれている場合のみ削除
# これにより、"Sumitomo Heavy Industries" のような英字社名から単語が削除されるのを防ぐ
_COMPANY_MARKER_RE = re.compile(
    r"^[^\x00-\x7F]{1,10}?\s+(?=.*(?:株式会社|有限会社|合同会社|合資会社|Co\.|Ltd\.|LLC|Inc\.))"
)

# 既知の認証/ブランディング/産業関連のノイズをフィルタリング
_LATIN_NOISE_RE = re.compile(
    r"\b(ISO|ISMS|JIS|IEC|CERTIFIED|CERTIFICATION|APPROVED|REGISTERED|"
    r"TUV|JQA|SGS|UKAS|ANAB|A2LA|NVLAP|DAkkS|COFRAC|DAKKS|PROVINCE|DISTRICT|"
    r"VIETNAM|VIET NAM|JAPAN|KOREA|CHINA|SINGAPORE|THAILAND|INDUSTRIAL|PARK|"
    r"DIRECTOR|MANAGER|ENGINEER|SUPERVISOR|DEPARTMENT|DIVISION|OFFICE|FACTORY|"
    r"VENTURE|JOINT|PARTNERSHIP|CONSTRUCTION|ELECTRONICS|TELECOM|SOLUTIONS|GROUP|BRANCH|"
    r"MOTOR|ELECTRIC|POWER|ENERGY|TECHNOLOGY|EQUIPMENT|AUTOMATION|SUSTAINABLE|DEVELOPMENT|GOALS|" \
    r"PLSTIC|MLDN|ERSION|VERSION|MOLDING|MOLD|PLASTIC|" \
    r"BRING\s*SUCCESS\s*TO\s*YOU|CONNECT\s*WITH\s*INNOVATION|BE\s*THE\s*RIGHT\s*ONE|" \
    r"HANOI|SAIGON|HEADOFFICE|HEAD\s*OFFICE|REPRESENTATIVE|BRANCH|" \
    r"VIETNAM|INC|CORP|LTD|CO\.)\b" \
    r"|\bCO[,.\s]|\bLTD[,.\s]?|\bCORP(ORATION)?\b|\bINC\.?\b|\bJSC\b|\bLLC\b|\bPTE\.?\b",
    re.I
)

def _normalize_company_name(s: str) -> str:
    if not s:
        return s
    # 断片化した英語の法的接尾辞（CO., LTD.のバリアント）を修正
    # マッチ例: CO. LTD, CO LTD, CO. LT, CO LT, CO.,LT など
    s = re.sub(r"\bCO\.?\s*,?\s*(?:LTD|LT|LD)\.?\s*$", "CO., LTD.", s, flags=re.I)
    # OCRエラーの可能性がある末尾の断片を修正
    # ただし、単なる 'Co.' を意図している場合に 'CO.' を過剰に修正しないよう注意が必要
    # とはいえ、ビジネス名において 'Ltd.' は非常に一般的
    s = re.sub(r"\bCORP\s*$", "CORP.", s, flags=re.I)
    
    # 新規: すべての英字社名を強制的に大文字にする
    # CJK文字よりも英字/ローマ字が大幅に多い場合、すべて大文字にする
    latin_chars = len(re.findall(r"[a-zA-Z]", s))
    if latin_chars > 0 and (len(re.findall(r"[\u4e00-\u9fff]", s)) == 0):
        # Pure or almost pure Latin card company
        s = s.upper()
    elif latin_chars > len(s) * 0.3:
        # 混在しているが、英字名が大部分を占める場合
        s = s.upper()
        
    return s.strip()

def group_ocr_by_y(
    ocr_results: list[tuple],
    y_thresh: int = SpatialConfig.Y_GROUPING_THRESH,
    x_thresh: int = SpatialConfig.X_GROUPING_THRESH,
) -> list[str]:
    items = []
    for bbox, text, conf, *_ in ocr_results:
        if not text or not text.strip():
            continue
        try:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            if not xs or not ys:
                continue
            x_min, x_max = min(xs), max(xs)
            x_c, y_c = sum(xs) / len(xs), sum(ys) / len(ys)
            items.append({"y_c": y_c, "x_c": x_c, "x_min": x_min, "x_max": x_max, "text": text.strip()})
        except Exception:
            continue

    if not items:
        return []

    items.sort(key=lambda t: (t["y_c"], t["x_c"]))
    lines: list[str] = []
    curr_line_texts = [items[0]["text"]]
    curr_y = items[0]["y_c"]
    curr_x_right = items[0]["x_max"]

    for item in items[1:]:
        if abs(item["y_c"] - curr_y) <= y_thresh and (item["x_min"] - curr_x_right) <= x_thresh:
            curr_line_texts.append(item["text"])
            curr_x_right = max(curr_x_right, item["x_max"])
        else:
            lines.append(" ".join(curr_line_texts))
            curr_line_texts = [item["text"]]
            curr_y = item["y_c"]
            curr_x_right = item["x_max"]
    lines.append(" ".join(curr_line_texts))
    return lines

def all_titles(lines: list[str], ocr_results: list = None) -> list[str]:
    from card.constants import COMPANY_STRONG_RE
    titles: list[str] = []
    if ocr_results:
        anchor_y = None
        anchor_x1, anchor_x2 = None, None
        for bbox, text, conf, *_ in ocr_results:
            clean = text.strip()
            if not clean or should_skip_noise_line(clean): continue
            if COMPANY_STRONG_RE.search(clean): continue
            if TITLE_RE.search(clean):
                yc = (bbox[0][1] + bbox[2][1]) / 2
                x1, x2 = bbox[0][0], bbox[1][0]
                if anchor_y is None:
                    anchor_y, anchor_x1, anchor_x2 = yc, x1, x2
                if abs(yc - anchor_y) <= 125:
                    if x2 >= (anchor_x1 - 50) and x1 <= (anchor_x2 + 50):
                        if clean not in titles: titles.append(clean)
        return titles

    for ln in lines:
        clean = ln.strip()
        if not should_skip_noise_line(clean) and not COMPANY_STRONG_RE.search(clean) \
                and TITLE_RE.search(clean) and clean not in titles:
            titles.append(clean)
    return titles

def strip_phone_label(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"[\+\d\(]", s)
    if m:
        res = s[m.start():].strip()
        res = re.sub(r"[\s\.\:\,\;\|\-\/]+$", "", res)
        return res
    return s

def fix_ocr_spaces(s: str) -> str:
    if not s:
        return s
    if len(re.findall(r"[\u3000-\u9fff]", s)) > len(s) * 0.3:
        return s
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = _SPACE_KEYWORDS_RE.sub(r" \1", s)
    if _WORDSEGMENT_OK:
        def _seg(m: re.Match) -> str:
            tok = m.group()
            parts = _ws_segment(tok.lower())
            return " ".join(p.upper() for p in parts) if len(parts) > 1 else tok
        s = re.sub(r"[A-Z]{8,}", _seg, s)
    s = re.sub(r"\(([A-Za-z])", r" (\1", s)
    s = re.sub(r"([A-Za-z])\)", r"\1) ", s)
    s = re.sub(r"([A-Za-z]),([A-Za-z])", r"\1, \2", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def is_plausible_phone(s: str, min_digits: int = 5) -> bool:
    if not s: return False
    return sum(ch.isdigit() for ch in s) >= min_digits

def all_emails(text: str) -> list[str]:
    norm = normalize_email_text(text or "")
    return EMAIL_RE.findall(norm)

def first_email(text: str) -> Optional[str]:
    emails = all_emails(text)
    if not emails: return None
    scored = []
    for em in emails:
        user, domain = em.split("@")[0].lower(), em.split("@")[1].lower()
        base_domain = re.sub(r"\.(com|vn|jp|net|org|co|info|biz|gov|edu)(\.[a-z]{2,})?$", "", domain)
        score = 0
        if user in GENERIC_EMAIL_USERS: score -= 10
        if user == base_domain or user in base_domain or base_domain in user: score += 5
        if any(c in user for c in "._-"): score -= 2
        scored.append((score, em))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def all_phones(text: str) -> list[str]:
    s = text or ""
    results: list[str] = []
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    for ln in lines:
        # FaxやTaxのみの行は避けるが、明確な電話マーカーがある場合は許可
        is_phone_marker = bool(PHONE_MARKERS_RE.search(ln))
        is_fax_or_tax = bool(FAX_HINT_RE.search(ln) or TAX_HINT_RE.search(ln))
        
        if is_fax_or_tax and not is_phone_marker:
            continue
        for pattern in [PHONE_RE, PHONE_DOMESTIC_RE, PHONE_NO_LABEL_RE]:
            for m in pattern.finditer(ln):
                full_match = m.group(0)
                phone = strip_phone_label(full_match)
                if phone and phone not in results:
                    if sum(c.isdigit() for c in phone) >= 8:
                        results.append(phone)
    return results

def first_phone(text: str) -> Optional[str]:
    phones = all_phones(text)
    return phones[0] if phones else None

def extract_company(lines: list[str], ocr_results: list = None, email: Optional[str] = None) -> Optional[str]:
    search_lines = lines
    if ocr_results:
        search_lines = group_ocr_by_y(
            ocr_results, 
            y_thresh=SpatialConfig.COMPANY_Y_GROUPING_THRESH,
            x_thresh=SpatialConfig.COMPANY_X_GROUPING_THRESH
        )
    scored = []
    for ln in search_lines:
        if should_skip_noise_line(ln): continue
        score = 0
        if re.search(r"株式会社|有限会社", ln):
            # 特殊ケース: 法的接尾辞のみの場合は低いスコアを与える
            if len(ln.strip()) <= 4: score += 10
            else: score += 150
        elif COMPANY_LEGAL_RE.search(ln):
            score += 100
        elif COMPANY_INDUSTRY_RE.search(ln):
            score += 30
        
        # --- 新規: メールアドレスのドメインによる加点 ---
        if email and isinstance(email, str) and "@" in email:
            from card.utils import extract_email_domain
            domain = extract_email_domain(email).lower()
            base_domain = re.sub(r"\.(com|vn|jp|net|org|co|info|biz|gov|edu)(\.[a-z]{2,})?$", "", domain)
            if base_domain and len(base_domain) >= 3:
                ln_clean = re.sub(r"[^a-z0-9]", "", ln.lower())
                domain_clean = re.sub(r"[^a-z0-9]", "", base_domain.lower())
                if domain_clean in ln_clean:
                    score += 120 # 行内にブランド名が見つかった
        
        # 役職、部署、または住所のように見える場合は減点
        if JP_TITLE_WORDS_RE.search(ln) or TITLE_RE.search(ln):
            score -= 50
        if ADDRESS_HINTS.search(ln):
            score -= 100
        
        if score > 0:
            cleaned = _COMPANY_MARKER_RE.sub("", ln).strip()
            # 何も残らない、または英字が短すぎる場合は、日本語が含まれていない限りスキップ
            if len(cleaned) <= 3 and not re.search(r"[\u4E00-\u9FFF]", cleaned): continue
            
            # 特定の長い名前に対するボーナス
            score += len(cleaned) / 10.0
            
            # 非常に長い名前（住所やスローガンの誤認が多い）は減点
            if len(ln) > 40 and not (re.search(r"株式会社|有限会社", ln) or COMPANY_LEGAL_RE.search(ln)):
                score -= 150

            # 日本語の企業名を強力に優先
            if re.search(r"[\u4E00-\u9FFF]", cleaned):
                score += 100 # 50から増加
            scored.append({"score": score, "text": cleaned if cleaned else ln})
            
    if scored:
        scored.sort(key=lambda x: x["score"], reverse=True)
        res = scored[0]["text"]
        # 合成ドキュメントに従い、すべての企業名を大文字にする
        res = res.upper()
        return _normalize_company_name(res)
    candidates = []
    for ln in search_lines:
        s = ln.strip(" -–|•·")
        # 日本語が含まれている場合はスキップ（英字社名ではなく人名の可能性が高いため）
        if JP_CHAR_RE.search(s): continue
        # 緩和: 社名に数字が含まれることを許可（例: 3A CONSULTING）
        # ただし、すべて数字であるか、数字が多すぎる場合は減点
        digit_count = sum(c.isdigit() for c in s)
        if digit_count > 0:
            if digit_count > (len(s) / 2) or digit_count > 5: continue
            # 許可するが、加点はしない
        # メールアドレス形式を含む行はスキップ
        if "@" in s or EMAIL_RE.search(s) or re.search(r"(?i)\b(?:e[\-\s._]?mail)\b", s): continue
        
        # 英字社名は通常3単語以上（例: "Company Name Ltd" または "A B Association"）
        s_upper = s.upper()
        s_fixed = fix_ocr_spaces(s_upper)
        
        if not s_fixed or len(s_fixed) < 5: continue
        # 1-2単語の場合、候補とするには実質的にすべて大文字であり、ノイズでない必要がある
        word_count = len(s_fixed.split())
        if word_count < 3:
            if not s_fixed.isupper(): continue
            if len(s_fixed) < 4: continue
        # fix_ocr_spacesによってメールアドレス形式が露出する可能性があるため、再度チェック
        if "@" in s_fixed or EMAIL_RE.search(s_fixed) or re.search(r"(?i)\b(?:e[\-\s._]?mail)\b", s_fixed): continue
        
        noise_words = [
            "ADDRESS", "PROVINCE", "DISTRICT", "INDUSTRIAL", "FACTORY", "ZONE", "PARK", 
            "WARD", "ROAD", "STREET", "CITY", "P.O.BOX", "ROUTE", "FLOOR",
            "SUCCESS", "BETTER", "FUTURE", "SLOGAN", "BRING", "QUALITY", "DREAM", "WORLD", "HAPPY",
            "MEMBER", "BELONG", "CERTIFIED", "PARTNER", "OFFICIAL"
        ]
        if any(nw in s_fixed.upper() for nw in noise_words): continue
        # 役職や部署のパターンに一致する行はスキップ
        if TITLE_RE.search(s) or TITLE_RE.search(s_fixed) or JP_TITLE_WORDS_RE.search(s): continue
        candidates.append(s_fixed)
    if candidates:
        res = max(candidates, key=len).upper()
        return _normalize_company_name(res)
    return None

def extract_address(lines: list[str]) -> Optional[str]:
    scored = []
    for ln in lines:
        clean = ln.strip()
        if not clean: continue
        score = 0
        if ADDRESS_HINTS.search(clean): score += 15
        score += len(JP_ADD_RE.findall(clean)) * 2 + len(JP_ADDR_SUFFIX_RE.findall(clean)) * 8
        score += len(VN_ACCENT_RE.findall(clean)) * 3
        score += sum(c.isdigit() for c in clean)
        if len([w for w in re.split(r"[,\s.\-/]+", clean) if w]) >= 4: score += 5
        
        # 明らかに住所ではない行を除外
        from card.utils import should_skip_noise_line
        if should_skip_noise_line(clean):
            score = 0
        elif JP_NAME_RE.match(clean) or re.search(r"^[A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)*$", clean):
            # 名前のように見える（漢字または英字タイトルケース）
            score -= 15
        
        if score > 8:
            scored.append({"score": score, "native": len(re.findall(r"[^\x00-\x7F]", clean)), "len": len(clean), "text": clean})
    if scored:
        scored.sort(key=lambda x: (x["score"], x["native"], x["len"]), reverse=True)
        return scored[0]["text"]
    return None

def detect_is_full_jp(ocr_results_or_lines: list) -> bool:
    """意味のあるコンテンツに基づいて、これが「純粋な日本語」の名刺である可能性が高いかどうかを検出します。"""
    if not ocr_results_or_lines:
        return False
        
    if isinstance(ocr_results_or_lines[0], (list, tuple)):
        source = [item[1] for item in ocr_results_or_lines]
    else:
        source = ocr_results_or_lines
        
    meaningful_lines = []
    for ln in source[:30]:  # より多くの行をチェック
        if should_skip_noise_line(ln): continue
        if any(pat.search(ln) for pat in [EMAIL_RE, PHONE_RE, PHONE_DOMESTIC_RE, PHONE_NO_LABEL_RE]): continue
        if URL_RE.search(ln) or ADDRESS_HINTS.search(ln): continue
        meaningful_lines.append(ln)
    
    jp_lines = 0
    latin_lines = 0
    for ln in meaningful_lines:
        has_jp = bool(re.search(r"[^\x00-\x7F]", ln))
        
        # 英字のみの行: 短いものや一般的な技術用語はスキップ
        if not has_jp:
            # "3", "JQA", "ISO" のような短いものはスキップ
            if len(re.sub(r"[^a-zA-Z]", "", ln)) < 5:
                continue
            # 技術的な表記に見えるもの（数字が多い）はスキップ
            if len(re.findall(r"\d", ln)) > len(re.findall(r"[a-zA-Z]", ln)):
                continue
            
            has_lat = bool(re.search(r"[a-zA-Z]{3,}", ln))
            if has_lat:
                latin_lines += 1
        else:
            jp_lines += 1

    total = jp_lines + latin_lines
    ratio = jp_lines / total if total > 0 else 0
    # 日本語の行が多い場合、英字が含まれていても日本語名刺である可能性が高い
    result = (ratio >= 0.65) or (jp_lines >= 5 and jp_lines > latin_lines * 2)
    logger.info(f"detect_is_full_jp: jp={jp_lines}, lat={latin_lines}, ratio={ratio:.2f} -> {result}")
    return result

def guess_full_name(lines: list[str], email: Optional[str], company: Optional[str], ocr_results: list = None, is_full_jp: Optional[bool] = None, titles: Optional[list[str]] = None) -> Optional[str]:
    name_kanji, name_latin, name_kana = None, None, None
    from difflib import SequenceMatcher
    
    if is_full_jp is None:
        is_full_jp = detect_is_full_jp(ocr_results if ocr_results else lines)
        
    if is_full_jp:
        logger.info("「純粋な日本語」名刺を検出 - 英字名の抽出をスキップします。")

    # Local has_jp for word count constraints
    has_jp = any(JP_CHAR_RE.search(ln) for ln in lines)

    # --- Chain Merge: Kanji name extraction with spatial grouping ---
    MAX_KANJI_TOTAL_LEN = 10

    def _is_kanji_name_candidate(text: str, company: Optional[str] = None) -> bool:
        """Check if text is a valid kanji name fragment (not title/noise/address/company)."""
        clean = text.strip()
        if not clean:
            return False
        
        # New: Exclude if it looks like a slogan (punctuation, too long, marketing words)
        if re.search(r"[!?！？]", clean) or len(clean) > 25:
            return False
        if re.search(r"予約|受付|営業|年中無休|無料|お気軽に|下さい|キャンペーン", clean):
            return False

        # New: Exclude if it's too similar to the company name
        if company:
            # Normalize common Kanji variants for comparison (産/產, etc.)
            def _norm_jp(t):
                if not t: return ""
                t = re.sub(r"[\s\(\)（）]", "", t).lower()
                # Common abbreviations/variants
                t = t.replace("產", "産").replace("實", "実").replace("廣", "広").replace("電工", "電気工業")
                return t
                
            comp_norm = _norm_jp(company)
            text_norm = _norm_jp(clean)
            
            if text_norm and (text_norm in comp_norm or comp_norm in text_norm):
                # Only exclude if it's a significant part of the company name 
                # or if it's a known company abbreviation (e.g. 住友電工)
                if len(text_norm) >= 3 or text_norm == "住友電工" or text_norm == comp_norm:
                    return False

        if JP_TITLE_WORDS_RE.search(clean) or "HP" in clean.upper():
            return False
        # Katakana/Kanji specific noise
        if re.search(r"部|課|室|係|支店|営業所|工場|GROUP|DEPT|DEPARTMENT|FACTORY|法人部", clean, re.I):
            return False
        if should_skip_noise_line(clean):
            return False

        # Allow if contains Kanji OR Katakana
        has_name_chars = bool(re.search(r"[\u4E00-\u9FFF]", clean)) or bool(re.search(r"[\u30A0-\u30FF]", clean))
        if not has_name_chars:
            return False
        if ADDRESS_HINTS.search(clean) or COMPANY_STRONG_RE.search(clean):
            return False
        if JP_BAD_NAME_CHARS_RE.match(clean):
            return False

        if TITLE_RE.search(clean):
            return False
        if "：" in clean or ":" in clean or "•" in clean or "・" in clean:
            return False
        if re.search(r"\d{3,}", clean):
            return False
        jp_count = len(JP_CHAR_RE.findall(clean))
        return 1 <= jp_count <= 5

    if ocr_results:
        potential_fragments = []
        for bbox, text, conf, *_ in ocr_results:
            clean = text.strip()
            if not _is_kanji_name_candidate(clean, company=company):
                continue
            jp_count = len(JP_CHAR_RE.findall(clean))
            if jp_count < 1 or jp_count > 5:
                continue
            y_c = (bbox[0][1] + bbox[2][1]) / 2
            x_left = bbox[0][0]
            x_right = bbox[1][0]
            potential_fragments.append({
                "text": clean,
                "bbox": bbox,
                "y_c": y_c,
                "x_left": x_left,
                "x_right": x_right,
                "jp_count": jp_count,
                "is_clear": 1 if JP_NAME_RE.match(clean) else 0,
            })

        used = [False] * len(potential_fragments)
        groups = []
        for anchor_idx, anchor in enumerate(potential_fragments):
            if used[anchor_idx]:
                continue
            group = [anchor]
            used[anchor_idx] = True
            for other_idx, other in enumerate(potential_fragments):
                if used[other_idx]:
                    continue
                y_diff = abs(anchor["y_c"] - other["y_c"])
                if y_diff > 50:
                    continue
                group_x_right = max(f["x_right"] for f in group)
                group_x_left = min(f["x_left"] for f in group)
                gap_x = max(0, other["x_left"] - group_x_right, group_x_left - other["x_right"])
                if gap_x <= SpatialConfig.NAME_X_GROUPING_THRESH:
                    group.append(other)
                    used[other_idx] = True
            groups.append(group)

        # 利用可能な場合は役職アンカーを事前に計算
        title_anchors = []
        if ocr_results and titles:
            for bbox, text, conf, *_ in ocr_results:
                if any(t in text for t in titles):
                    yc = (bbox[0][1] + bbox[2][1]) / 2
                    x1, x2 = bbox[0][0], bbox[1][0]
                    title_anchors.append({"y": yc, "x1": x1, "x2": x2})

        best_kanji = None
        best_score = -1
        for group in groups:
            group.sort(key=lambda f: f["x_left"])
            texts = [f["text"] for f in group]
            
            # Simple join instead of _chained_merge to avoid hallucinating overlaps
            merged = " ".join(texts).replace("  ", " ").strip()
            merged_no_space = merged.replace(" ", "")
            merged_jp_count = len(JP_CHAR_RE.findall(merged_no_space))
            
            # Core score based on regex match
            is_clear = 1 if JP_NAME_RE.match(merged_no_space) else 0
            
            # Post-merge Noise Check: If result is a title keyword or noise, reject
            if JP_TITLE_WORDS_RE.search(merged_no_space) or MISC_NOISE_RE.search(merged_no_space):
                continue
            if ADDRESS_HINTS.search(merged_no_space) or COMPANY_STRONG_RE.search(merged_no_space):
                continue
            if len(merged_no_space) <= 2:
                # If it's a 2-char string where both are bad chars (like "本社", "支店")
                if all(JP_BAD_NAME_CHARS_RE.match(c) for c in merged_no_space):
                    continue
            
            # Spatial score: proximity to titles
            spatial_boost = 0
            if title_anchors:
                group_yc = sum(f["y_c"] for f in group) / len(group)
                group_x1 = min(f["x_left"] for f in group)
                group_x2 = max(f["x_right"] for f in group)
                
                for ta in title_anchors:
                    y_dist = abs(group_yc - ta["y"])
                    # Check vertical proximity
                    if y_dist <= SpatialConfig.NAME_TITLE_Y_KANJI:
                        # Check horizontal alignment/overlapping
                        if not (group_x2 < ta["x1"] - SpatialConfig.NAME_TITLE_X_OFFSET or 
                                group_x1 > ta["x2"] + SpatialConfig.NAME_TITLE_X_OFFSET):
                            spatial_boost = 150 # Strong boost for being near a title
                            break
                    elif y_dist <= SpatialConfig.NAME_TITLE_Y_KANJI * 2:
                        spatial_boost = 50 # Moderate boost

            # Refusal: if it's too clearly a title/company already identified
            is_title = False
            if titles:
                m_norm = merged_no_space
                for t in titles:
                    if m_norm in t or t in m_norm:
                        is_title = True; break
            if is_title: continue

            # Final scoring for Kanji
            score = is_clear * 100 + merged_jp_count * 10 + spatial_boost
            
            if score > best_score:
                best_score = score
                best_kanji = merged

        name_kanji = best_kanji
    else:
        jp_cands = []
        for ln in lines:
            clean = ln.strip()
            if not clean:
                continue
            if not _is_kanji_name_candidate(clean, company=company):
                continue
            jp_count = len(JP_CHAR_RE.findall(clean))
            if 1 <= jp_count <= MAX_KANJI_TOTAL_LEN:
                is_clear = 1 if JP_NAME_RE.match(clean) else 0
                jp_cands.append((is_clear, jp_count, clean))
        if jp_cands:
            jp_cands.sort(reverse=True)
            name_kanji = jp_cands[0][2]
        else:
            name_kanji = None

    def _is_initials_match(user: str, text: str) -> bool:
        """メールのユーザー名が名前のイニシャル + オプションで姓と一致するかどうかをチェックします。"""
        tokens = [t.lower() for t in re.split(r"[\s/._-]+", text) if len(t) >= 1]
        if not tokens or len(tokens) < 2: return False
        
        # シナリオ 1: hvphuong 対 Hoang Viet Phuong (イニシャル + イニシャル + フル)
        # 最初の単語のイニシャルと最後のフル単語を結合
        parts_hv = "".join(t[0] for t in tokens[:-1]) + tokens[-1]
        if user == parts_hv: return True
        
        # シナリオ 2: phuonghv 対 Hoang Viet Phuong (フル + イニシャル + イニシャル)
        parts_phv = tokens[-1] + "".join(t[0] for t in tokens[:-1])
        if user == parts_phv: return True
        
        # シナリオ 3: hvp 対 Hoang Viet Phuong (すべてイニシャル)
        parts_hvp = "".join(t[0] for t in tokens)
        if user == parts_hvp: return True
        
        return False

    is_mixed = bool(name_kanji)

    if not is_full_jp and email and "@" in email:
        user = email.split("@")[0].lower()
        raw_parts = [p for p in re.split(r"[^a-z]", user) if len(p) >= 2]
        parts = set(raw_parts)
        if _WORDSEGMENT_OK:
            for p in raw_parts:
                if len(p) >= 7:
                    for subp in _ws_segment(p):
                        if len(subp) >= 3: parts.add(subp)
        cands = []  # (一致数, オーバーラップあり, 長さ, テキスト, bbox) のリスト
        for ln in lines:
            if JP_TITLE_WORDS_RE.search(ln) or any(sw in ln.lower() for sw in ["tel:", "fax:", "email:"]): continue
            if titles and isinstance(titles, list) and any(t in ln for t in titles) and len(ln) > 30: continue
            if "@" in ln: continue # メールアドレスを含む行はスキップ
            if _LATIN_NOISE_RE.search(ln) or JP_CHAR_RE.search(ln): continue
            word_count = len(ln.strip().split())
            # 混在名刺（ベトナム人の名前は通常3-4単語）に合わせて単語数制限を緩和
            limit_word_count = 4 if is_mixed else 2
            if has_jp:
                # 混在名刺の場合、1-4単語を許可
                # 連結された英字は1単語として現れることが多い
                if word_count > limit_word_count or word_count < 1: continue
            else:
                # 英字のみの名刺の場合、通常2-4単語だが、すべて大文字の場合は1単語でも許可
                if word_count > 4 or word_count < 1: continue
                if word_count == 1 and not ln.strip().isupper(): continue
            
            # この行のbboxを検索
            ln_bbox = None
            if ocr_results:
                for bbox, text, conf, *_ in ocr_results:
                    if ln in text or text in ln:
                        ln_bbox = bbox; break
            
            words = ln.strip().split()
            new_words = []
            for w in words:
                if len(w) >= 6 and w == w.upper():
                    dense = w.lower()
                    split_points = []
                    for p in parts:
                        start = 0
                        while True:
                            idx = dense.find(p, start)
                            if idx == -1: break
                            split_points.append(idx); split_points.append(idx + len(p))
                            start = idx + 1
                    if split_points:
                        split_points = sorted(list(set(split_points)))
                        new_w = ""; last_p = 0
                        for p in split_points:
                            if p > last_p: new_w += w[last_p:p] + " "
                            last_p = p
                        new_w += w[last_p:]; new_words.append(new_w.strip())
                    else: new_words.append(w)
                else: new_words.append(w)
            ln_to_test = " ".join(new_words)
            ln_tokens = [tk for tk in re.split(r"[\s/._-]+", ln_to_test.lower()) if len(tk) >= 3]
            match_count = sum(1 for tk in ln_tokens if tk in parts)
            dense = ln_to_test.lower().replace(" ", "")
            has_overlap = (user in dense) or (len(user) >= 6 and user[:5] in dense)
            is_all_caps = ln_to_test.isupper() and len(re.findall(r"[A-Z]", ln_to_test)) >= 3
            is_initials = _is_initials_match(user, ln_to_test)
            
            if is_mixed:
                if match_count >= 1 or has_overlap or is_initials:
                    cands.append({
                        "match_count": match_count, 
                        "has_overlap": 1 if has_overlap or is_initials else 0, 
                        "is_all_caps": 1 if is_all_caps else 0,
                        "len": len(ln_to_test), 
                        "text": ln_to_test, 
                        "bbox": ln_bbox,
                        "tokens": set(ln_tokens)
                    })
            else:
                if match_count >= 1 or has_overlap or is_all_caps or is_initials:
                    cands.append({
                        "match_count": match_count, 
                        "has_overlap": 1 if has_overlap or is_initials else 0, 
                        "is_all_caps": 1 if is_all_caps else 0,
                        "len": len(ln_to_test), 
                        "text": ln_to_test, 
                        "bbox": ln_bbox,
                        "tokens": set(ln_tokens)
                    })

        # --- 新規: 英字の候補を役職に対してフィルタリング ---
        if titles:
            valid_cands = []
            for c in cands:
                c_norm = re.sub(r"\s+", "", c["text"].lower())
                is_t = False
                for t in titles:
                    t_norm = re.sub(r"\s+", "", t.lower())
                    if c_norm == t_norm or c_norm in t_norm or t_norm in c_norm:
                        is_t = True; break
                if not is_t:
                    valid_cands.append(c)
            cands = valid_cands

        if cands:
            # wordsegmentによる英字の最終スコアリング
            for c in cands:
                t = c["text"]
                # 英字に対する空間ブースト
                s_boost = 0
                if title_anchors and c["bbox"]:
                    byc = (c["bbox"][0][1] + c["bbox"][2][1]) / 2
                    bx1, bx2 = c["bbox"][0][0], c["bbox"][1][0]
                    for ta in title_anchors:
                        if abs(byc - ta["y"]) <= SpatialConfig.NAME_TITLE_Y_LATIN:
                            if not (bx2 < ta["x1"] - SpatialConfig.NAME_TITLE_X_OFFSET or 
                                    bx1 > ta["x2"] + SpatialConfig.NAME_TITLE_X_OFFSET):
                                s_boost = 100; break
                
                # wordsegment による評価
                ws_score = 0
                if _WORDSEGMENT_OK:
                    dense_latin = re.sub(r"[^a-zA-Z]", "", t)
                    if len(dense_latin) >= 5:
                        parts_ws = _ws_segment(dense_latin.lower())
                        # 1つの単語が2-3個の一般的な辞書単語に分割できる場合、名前である可能性が高い
                        if 2 <= len(parts_ws) <= 3:
                            ws_score = 50
                
                c["final_score"] = c["match_count"] * 100 + c["has_overlap"] * 150 + c["is_all_caps"] * 20 + s_boost + ws_score

            cands.sort(key=lambda x: x["final_score"], reverse=True)
            name_latin = cands[0]["text"].strip()

    search_lines_for_latin = lines
    
    # 名刺が純粋な日本語であると特定された場合、英字名の抽出をスキップ
    if not is_full_jp and not name_latin and not (name_kanji and not email):
        upper = [] # (text, bbox)
        for ln in search_lines_for_latin[:10]:
            s = ln.strip()
            if not s or JP_CHAR_RE.search(s): continue
            
            # 新規: 会社名と一致する場合は除外
            if company:
                s_norm = re.sub(r"[^a-z]", "", s.lower())
                c_norm = re.sub(r"[^a-z]", "", company.lower())
                if s_norm and (s_norm in c_norm or c_norm in s_norm):
                    if len(s_norm) >= 4 or s_norm == c_norm:
                        continue

            s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
            if s != s.upper() or any(ch.isdigit() for ch in s): continue
            if not all(ch == " " or "A" <= ch <= "Z" or ch in "-." for ch in s): continue
            word_count = len(s.split())
            limit_word_count = 4 if is_mixed else 3
            if is_mixed:
                if word_count > limit_word_count or word_count < 2: continue
            elif word_count < 2 or word_count > 4: continue 
            if _LATIN_NOISE_RE.search(s): continue
            
            # bboxを検索
            ln_bbox = None
            if ocr_results:
                for bbox, text, conf, *_ in ocr_results:
                    if s in text or text in s: ln_bbox = bbox; break
            upper.append({"text": s, "bbox": ln_bbox})


        if upper: 
            upper.sort(key=lambda x: len(x["text"]), reverse=True)
            candidate = upper[0]["text"]
            if is_mixed and email and "@" in email:
                # 混在名刺の場合、候補をメールアドレスと照合
                ln_tokens = [tk for tk in re.split(r"[\s/._-]+", candidate.lower()) if len(tk) >= 3]
                m_count = sum(1 for tk in ln_tokens if tk in parts)
                d_user = email.split("@")[0].lower()
                h_overlap = (d_user in candidate.lower().replace(" ", "")) or (len(d_user) >= 6 and d_user[:5] in candidate.lower().replace(" ", ""))
                if m_count >= 1 or h_overlap:
                    name_latin = candidate
                else:
                    # 混在名刺で、フォールバック候補がメールアドレスと一致しない場合は拒否
                    name_latin = None
            else:
                name_latin = candidate
        else:
            blocks = []
            for ln in lines[:8]:
                s = ln.strip()
                if not s or JP_CHAR_RE.search(s) or _LATIN_NOISE_RE.search(s): continue
                # 新規: 会社名と酷似している場合は除外
                if company:
                    s_n = re.sub(r"[^a-z]", "", s.lower())
                    c_n = re.sub(r"[^a-z]", "", company.lower())
                    if s_n and (s_n in c_n or c_n in s_n) and (len(s_n) >= 4 or s_n == c_n):
                        continue

                s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
                if not all(ch == " " or "A" <= ch <= "Z" or ch in "-. " or "a" <= ch.lower() <= "z" for ch in s): continue
                word_count = len(s.split())
                limit_word_count = 4 if is_mixed else 3
                if is_mixed:
                    if word_count > limit_word_count or word_count < 2: continue
                else:
                    if word_count > 4 or word_count < 2: continue
                    
                if not any(ch.isdigit() for ch in s): 
                    blocks.append(s)
            
            if blocks:
                # 最良のものを選び、混在している場合は再度検証
                best_block = max(blocks, key=lambda s: sum(c.isalpha() for c in s))
                if is_mixed and email and "@" in email:
                    ln_tokens = [tk for tk in re.split(r"[\s/._-]+", best_block.lower()) if len(tk) >= 3]
                    m_count = sum(1 for tk in ln_tokens if tk in parts)
                    d_user = email.split("@")[0].lower()
                    h_overlap = (d_user in best_block.lower().replace(" ", "")) or (len(d_user) >= 6 and d_user[:5] in best_block.lower().replace(" ", ""))
                    if m_count >= 1 or h_overlap:
                        name_latin = best_block
                    else:
                        # 混在名刺で、ブロックがメールアドレスと一致しない場合は拒否
                        name_latin = None
                else:
                    name_latin = best_block

    for ln in lines:
        if KANA_RE.search(ln) and not JP_CHAR_RE.search(ln):
            clean = ln.strip()
            if 2 <= len(clean) <= 12 and not any(sw in clean.lower() for sw in ["http", "www", "email"]):
                name_kana = clean; break
    if name_latin: name_latin = HONORIFIC_STRIP_RE.sub("", name_latin).strip()
    parts = []
    if name_kanji: parts.append(name_kanji)
    if name_latin and name_latin != name_kanji: 
        if not re.search(r"[^\x00-\x7F]", name_latin): parts.append(name_latin)
    if name_kana: parts.append(name_kana)
    return " / ".join(parts) if parts else None
