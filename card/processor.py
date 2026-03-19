import re
import logging
from difflib import SequenceMatcher
from card.constants import (
    TITLE_RE, FAX_HINT_RE, URL_RE, URL_HINT_RE, TAX_HINT_RE,
    PHONE_LABEL_RE, EMAIL_LABEL_RE, MISC_NOISE_RE, PHONE_NO_LABEL_RE,
    COMPANY_HINTS, COMPANY_STRONG_RE, LEGAL_SUFFIX_ONLY_RE, VIETNAM_RE, SpatialConfig,
    EMAIL_RE, GENERIC_EMAIL_USERS, COMPANY_LEGAL_RE, COMPANY_INDUSTRY_RE,
    DEPARTMENT_RE, JP_CHAR_RE
)
from card.utils import (
    infer_company_from_email, should_skip_noise_line, normalize_email_text, 
    fix_broken_tld, extract_email_domain, format_domain_as_company
)
from card.logic import (
    group_ocr_by_y, all_titles, all_emails, first_email, all_phones,
    extract_company, extract_address, guess_full_name, fix_ocr_spaces,
    detect_is_full_jp
)

logger = logging.getLogger("kiosk.card.processor")
# _GENERIC_EMAIL_USERS は constants.py から GENERIC_EMAIL_USERS としてインポートされるようになりました
_TITLE_TOKEN_RE = re.compile(r"\b(president|director|manager|chief|head|sales|engineer|supervisor|general director)\b|部長|課長|係長|主任|代表取締役|社長", re.I)
_COMPANY_TOKEN_RE = re.compile(r"\b(company|co\.?|ltd|limited|inc|corp|corporation|llc|jsc|gmbh)\b|株式会社|有限会社|合同会社", re.I)
_NOISE_NAME_RE = re.compile(r"\b(iso|tax|url|www|http|fax|tel|phone|mobile|email|e-mail|office|headquarters|branch|representative)\b", re.I)


def _normalize_ocr_orientation(ocr_results: list) -> list:
    """CLSモデルとヒューリスティックを使用して、180度回転（上下逆さま）した名刺を検出して反転させます。"""
    if not ocr_results:
        return ocr_results

    # OCR結果から画像の境界を推定
    max_x, max_y = 0, 0
    for item in ocr_results:
        bbox = item[0]
        for p in bbox:
            max_x = max(max_x, p[0])
            max_y = max(max_y, p[1])

    top_contact, bot_contact = 0, 0
    top_name_like, bot_name_like = 0, 0
    cls_180_votes, cls_0_votes = 0, 0
    mid_y = max_y / 2

    for item in ocr_results:
        bbox, text, conf = item[0], item[1], item[2]
        angle_info = item[3] if len(item) > 3 else None
        
        if not text: continue
        yc = sum(p[1] for p in bbox) / len(bbox)
        is_top = yc < mid_y

        # 1. CLSモデルによる判定
        if angle_info and isinstance(angle_info, (list, tuple)) and len(angle_info) >= 1:
            label = str(angle_info[0])
            if label == '180': cls_180_votes += 1
            elif label == '0': cls_0_votes += 1

        # 2. ヒューリスティック信号
        has_contact = bool(EMAIL_RE.search(text) or PHONE_LABEL_RE.search(text) or 
                         URL_RE.search(text) or URL_HINT_RE.search(text))
        has_name_signal = bool((len(text) <= 5 and re.search(r"[\u4E00-\u9FFF]", text)) or
                             TITLE_RE.search(text))

        if has_contact:
            if is_top: top_contact += 1
            else: bot_contact += 1
        if has_name_signal:
            if is_top: top_name_like += 1
            else: bot_name_like += 1

    is_flipped = False
    if cls_180_votes > cls_0_votes and cls_180_votes >= 1:
        is_flipped = True
        logger.info(f"Orientation: CLS model detected upside-down (180:{cls_180_votes}, 0:{cls_0_votes})")
    elif top_contact > bot_contact and top_contact >= 1:
        is_flipped = True
        logger.info(f"Orientation: Heuristic detected upside-down (TopContact={top_contact})")

    if is_flipped:
        logger.info("座標を正規化しています（180度反転）。")
        normalized = []
        for item in ocr_results:
            bbox, text, conf = item[0], item[1], item[2]
            angle = item[3] if len(item) > 3 else None
            raw_flip = [[max_x - p[0], max_y - p[1]] for p in bbox]
            restored = [raw_flip[2], raw_flip[3], raw_flip[0], raw_flip[1]]
            normalized.append((restored, text, conf, angle))
        
        normalized.sort(key=lambda f: (sum(p[1] for p in f[0])/4, sum(p[0] for p in f[0])/4))
        return normalized

    return ocr_results


def _line_center(bbox) -> tuple[float, float]:
    return (sum(p[0] for p in bbox) / len(bbox), sum(p[1] for p in bbox) / len(bbox))


def _ascii_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for ch in s if ord(ch) < 128) / max(1, len(s))


def _build_line_features(ocr_results: list) -> list[dict]:
    feats = []
    for bbox, text, conf, *_ in ocr_results:
        t = (text or "").strip()
        if not t:
            continue
        x_c, y_c = _line_center(bbox)
        t_low = t.lower()
        digit_ratio = sum(ch.isdigit() for ch in t) / max(1, len(t))
        feats.append({
            "bbox": bbox,
            "text": t,
            "conf": float(conf),
            "x": x_c,
            "y": y_c,
            "len": len(t),
            "ascii_ratio": _ascii_ratio(t),
            "digit_ratio": digit_ratio,
            "has_email": bool(EMAIL_RE.search(t)),
            "has_phone": bool(PHONE_LABEL_RE.search(t) or PHONE_NO_LABEL_RE.search(t)),
            "has_url": bool(URL_RE.search(t) or URL_HINT_RE.search(t)),
            "has_title_token": bool(_TITLE_TOKEN_RE.search(t)),
            "has_company_token": bool(_COMPANY_TOKEN_RE.search(t)),
            "has_noise_name": bool(_NOISE_NAME_RE.search(t)),
            "text_lower": t_low,
        })
    feats.sort(key=lambda f: (f["y"], f["x"]))
    return feats


def _score_title_line(feat: dict) -> float:
    s = 0.0
    if feat["has_title_token"]:
        s += 6.0
    if 3 <= feat["len"] <= 48:
        s += 1.0
    if feat["has_email"] or feat["has_phone"] or feat["has_url"]:
        s -= 6.0
    if feat["has_company_token"]:
        s -= 2.0
    s += min(2.0, feat["conf"] * 2.0)
    return s


def _score_company_line(feat: dict, domain_clean: str) -> float:
    s = 0.0
    text_clean = re.sub(r"\s+", "", feat["text_lower"])
    if feat["has_company_token"]:
        s += 8.5
    
    if domain_clean:
        if domain_clean in text_clean or text_clean in domain_clean:
            s += 5.0
        elif len(domain_clean) >= 4 and domain_clean[:4] in text_clean:
            # 部分的なブランド名の一致をチェック（例: 'byokane' 内の 'byoka'）
            s += 3.5
            
    if 5 <= feat["len"] <= 80:
        s += 1.0
    if feat["has_email"] or feat["has_phone"] or feat["has_url"]:
        s -= 20.0
    if feat["digit_ratio"] > 0.35:
        s -= 2.0
    if re.search(r"[\u4E00-\u9FFF]", feat["text"]):
        s += 5.0
    s += min(2.5, feat["conf"] * 3.0)
    return s


def _score_name_line(feat: dict) -> float:
    s = 0.0
    if feat["has_email"] or feat["has_phone"] or feat["has_url"] or feat["has_noise_name"]:
        return -10.0
    if feat["has_title_token"] or feat["has_company_token"]:
        s -= 3.0
    if 2 <= feat["len"] <= 32:
        s += 2.0
    if feat["digit_ratio"] < 0.1:
        s += 1.0
    if feat["ascii_ratio"] < 0.8:
        s += 1.0  # 日本語の名前の行をわずかに優先
    s += min(2.0, feat["conf"] * 2.0)
    return s


_KANA_RE = re.compile(r"[\u3040-\u30FF]")  # Hiragana + Katakana

def is_valid_ocr(text: str, conf: float) -> bool:
    """Dynamic confidence gate.

    - Text rá»—ng / quÃ¡ ngáº¯n: luÃ´n drop.
    - Chá»©a Hiragana/Katakana: ngÆ°á»¡ng tháº¥p hÆ¡n (0.15) vÃ¬ RapidOCR
      thÆ°á»ng cho conf tháº¥p vá»›i kana dÃ¹ nháº­n Ä‘Ãºng.
    - CÃ²n láº¡i (Latin, HÃ¡n, sá»‘...): ngÆ°á»¡ng 0.3.
    """
    if not text:
        return False
    
    # 1文字の日本語（漢字/かな）を許可
    t_strip = text.strip()
    is_jp = _KANA_RE.search(text) or re.search(r"[\u4E00-\u9FFF]", text)
    if len(t_strip) < 1 or (len(t_strip) < 2 and not is_jp):
        return False
        
    threshold = 0.15 if _KANA_RE.search(text) else 0.3
    return conf > threshold


def extract_email_domain(email: str) -> str:
    """メールのドメイン（@とTLDの間）から会社名を抽出します。"""
    if not email or "@" not in email:
        return ""
    domain_part = email.split("@")[1]
    # TLD（.com, .vn, .co.jpなど）を削除
    domain_clean = re.sub(r"\.(com|vn|jp|cn|co|net|org|edu|gov|info)(\.[a-z]{2,3})?$", "", domain_part, flags=re.I)
    return domain_clean


def format_domain_as_company(domain: str) -> str:
    """ドメインを会社名（大文字）としてフォーマットし、会社の接尾辞を展開します。"""
    # 一般的なTLDを削除
    domain = re.sub(r'\.(com|jp|vn|net|org|co\.jp|com\.vn)$', '', domain, flags=re.I)
    
    # 一般的な会社の接尾辞を展開
    suffix_map = {
        'corp': 'CORPORATION',
        'co': 'CO.',
        'ltd': 'LTD.',
        'inc': 'INC.',
        'llc': 'LLC',
    }
    
    # ダッシュ/アンダースコアで分割
    parts = re.split(r'[-_]', domain)
    
    # 最後の部分が会社の接尾辞であるかチェック
    if len(parts) > 1 and parts[-1].lower() in suffix_map:
        suffix = suffix_map[parts[-1].lower()]
        base = ' '.join(parts[:-1]).upper()
        return f"{base} {suffix}"
    
    # デフォルト: 大文字にして区切り文字を置換
    return domain.replace("-", " ").replace("_", " ").upper()


def _legalize_company_name(company_base: str, domain: str, address: str, ocr_results: list) -> str:
    if not company_base:
        return company_base
    if COMPANY_STRONG_RE.search(company_base):
        return company_base

    domain_hint = infer_company_from_email(f"x@{domain}") if domain else None
    base = company_base.strip().upper()
    if domain_hint:
        hint_upper = domain_hint.strip().upper()
        if len(base) <= 5:
            base = hint_upper

    has_vietnam_signal = bool(VIETNAM_RE.search(address or ""))
    if not has_vietnam_signal:
        for _, txt, *_ in ocr_results or []:
            t = (txt or "").lower()
            if "com.vn" in t or "viet nam" in t or "vietnam" in t:
                has_vietnam_signal = True
                break

    if has_vietnam_signal and "VIETNAM" not in base:
        base = f"{base} VIETNAM"

    if not re.search(r"\b(CO\.?|COMPANY|LTD\.?|LIMITED|INC\.?|CORP\.?|CORPORATION|LLC|JSC)\b", base, re.I):
        return f"{base} CO., LTD"
    return base


# Removed _fallback_name_from_email definition




def parse_bcard_fields(text: str, ocr_results: list = None, is_full_jp: bool = False) -> tuple[dict, list[str]]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # 最適化: 効率的な検索のために正規化された行を一度だけ事前計算
    norm_lines = [re.sub(r"\s+", "", ln.lower()) for ln in lines]
    active_indices = list(range(len(lines)))
    
    def drop_lines_containing(value: str, label: str = ""):
        nonlocal active_indices
        if not value: return
        v_clean = re.sub(r"\s+", "", value.strip().lower())
        if not v_clean: return
        before_count = len(active_indices)
        
        # 事前計算された正規化文字列を使用してインデックスをフィルタリング
        active_indices = [i for i in active_indices if v_clean not in norm_lines[i] and norm_lines[i] not in v_clean]
        
        dropped_count = before_count - len(active_indices)
        if dropped_count > 0:
            display_value = value[:50] + "..." if len(value) > 50 else value
            logger.info(f"  Dropped {dropped_count} line(s) containing {label or 'value'}: '{display_value}' -> {len(active_indices)} lines remaining")

    def get_current_lines():
        return [lines[i] for i in active_indices]

    logger.info(f"{len(lines)} 行で parse_bcard_fields を開始します")
    
    # ノイズパターンの除外
    for i in active_indices[:]:
        ln = lines[i]
        if should_skip_noise_line(ln):
            drop_lines_containing(ln, "noise pattern")
        elif len(ln) > 50 and not (re.search(r"株式会社|有限会社", ln) or COMPANY_LEGAL_RE.search(ln)):
            # 住所やスローガンの誤認の可能性が高い
            drop_lines_containing(ln, "long non-company line")
        elif DEPARTMENT_RE.search(ln):
            drop_lines_containing(ln, "department/department fragment")

    # メールの除外
    em = first_email(text)
    

    for email_item in all_emails(text):
        drop_lines_containing(email_item, "email")
    if em:
        drop_lines_containing(em, "再構成されたメール")

    # 電話番号の除外
    phones = all_phones(text)
    for ph in phones:
        drop_lines_containing(ph, "phone")

    # 長い数字列の除外
    for i in active_indices[:]:
        if re.search(r"\d(?:.?\d){9,}", lines[i]):
            drop_lines_containing(lines[i], "long digit sequence")

    # 役職の除外（名前/会社名と干渉しないよう最初に実施）
    titles = all_titles(get_current_lines(), ocr_results)
    for t in titles:
        drop_lines_containing(t, "title")
    
    # 名前のヒントとして使用するために、最初に会社名を検索
    comp = extract_company(get_current_lines(), ocr_results=ocr_results, email=em)
    if comp and (EMAIL_RE.search(comp) or "email" in comp.lower()):
        comp = ""
    
    # 名前の検索
    name = guess_full_name(get_current_lines(), em, comp or "", ocr_results=ocr_results, is_full_jp=is_full_jp, titles=titles)

    # 名前/会社名の重複を解決
    if comp and name:
        comp_norm = re.sub(r"\s+", "", comp.lower())
        name_norm = re.sub(r"\s+", "", name.lower())
        if comp_norm == name_norm or comp_norm in name_norm or name_norm in comp_norm:
            # 大幅に重複している場合、人名らしく見えるなら名前、
            # 強力な法的マーカーがあるなら会社名を信頼する。
            if not COMPANY_STRONG_RE.search(comp):
                comp = ""
    
    # 特定されたフィールドを active_indices から除外
    if comp:
        if VIETNAM_RE.search(comp):
            if COMPANY_STRONG_RE.search(comp):
                drop_lines_containing(comp, "company (strong)")
        else:
            drop_lines_containing(comp, "company")

    if name:
        for part in (name.split("/") if "/" in name else [name]):
            drop_lines_containing(part.strip(), "name part")

    # 会社名のヒントを除外
    if comp:
        comp_clean = re.sub(r"\s+", "", comp.lower())
        for i in active_indices[:]:
            if COMPANY_HINTS.search(lines[i]):
                ln_clean = norm_lines[i]
                if comp_clean in ln_clean or ln_clean in comp_clean:
                    drop_lines_containing(lines[i], "company hint")

    # 住所の除外（最後に実施）
    addr = extract_address(get_current_lines())
    if addr:
        drop_lines_containing(addr, "address")

    final_lines = get_current_lines()
    res = {
        "full_name": (name or "").upper(),
        "email": em or "",
        "phone": "; ".join(phones) if phones else "",
        "company": comp or "",
        "title": "; ".join(titles) if titles else "",
        "address": addr or "",
    }
    return res, final_lines



def _bootstrap_fields_with_scoring(fields_heur: dict, ocr_results: list) -> list[dict]:
    feats = _build_line_features(ocr_results)
    if not feats:
        return feats

    if not (fields_heur.get("title") or "").strip():
        best_t = max(feats, key=_score_title_line)
        if _score_title_line(best_t) >= 6.0:
            fields_heur["title"] = best_t["text"]

    if not (fields_heur.get("company") or "").strip():
        em = fields_heur.get("email", "")
        domain_clean = re.sub(r"\s+", "", extract_email_domain(em).lower()) if "@" in em else ""
        comp_cands = [f for f in feats if not (f["has_email"] or f["has_phone"] or f["has_url"])]
        if comp_cands:
            best_c = max(comp_cands, key=lambda f: _score_company_line(f, domain_clean))
        else:
            best_c = None
        if best_c is not None and _score_company_line(best_c, domain_clean) >= 7.0:
            fields_heur["company"] = best_c["text"]

    if not (fields_heur.get("full_name") or "").strip():
        blocked = {
            re.sub(r"\s+", "", (fields_heur.get("title", "") or "").lower()),
            re.sub(r"\s+", "", (fields_heur.get("company", "") or "").lower()),
        }
        cands = []
        for f in feats:
            t_clean = re.sub(r"\s+", "", f["text_lower"])
            if any(b and (b in t_clean or t_clean in b) for b in blocked):
                continue
            cands.append(f)
        if cands:
            best_n = max(cands, key=_score_name_line)
            if _score_name_line(best_n) >= 3.5:
                fields_heur["full_name"] = best_n["text"].upper()

    return feats


def _compute_title_anchor(fields_heur: dict, ocr_results: list):
    """役職のアンカー座標を計算します。"""
    y_title, x_title_min, x_title_max = None, None, None
    title_heur = fields_heur.get("title", "")
    if not title_heur:
        return y_title, x_title_min, x_title_max

    tm_list = [t.strip().lower() for t in title_heur.split(";") if t.strip()]
    y_sums, count = 0, 0
    for bbox, text, conf, *_ in ocr_results:
        t_low = text.strip().lower()
        if any(tm in t_low or t_low in tm for tm in tm_list):
            yc = (bbox[0][1] + bbox[2][1]) / 2
            y_sums += yc
            x1, x2 = bbox[0][0], bbox[1][0]
            if x_title_min is None or x1 < x_title_min:
                x_title_min = x1
            if x_title_max is None or x2 > x_title_max:
                x_title_max = x2
            count += 1
    if count > 0:
        y_title = y_sums / count
    return y_title, x_title_min, x_title_max


def _refine_title_by_name_anchor(fields_heur: dict, ocr_results: list) -> None:
    name_heur = fields_heur.get("full_name", "")
    if not name_heur:
        return

    y_name, x_name_min, x_name_max = None, None, None
    parts = [p.strip() for p in name_heur.split("/") if p.strip()]
    anchor_name = next((p for p in parts if re.search(r"[a-zA-Z]", p)), parts[0] if parts else "")
    if anchor_name:
        anchor_low = anchor_name.lower()
        for bbox, text, conf, *_ in ocr_results:
            t_low = text.strip().lower()
            if anchor_low in t_low or t_low in anchor_low:
                yc = (bbox[0][1] + bbox[2][1]) / 2
                x1, x2 = bbox[0][0], bbox[1][0]
                if y_name is None:
                    y_name = yc
                if x_name_min is None or x1 < x_name_min:
                    x_name_min = x1
                if x_name_max is None or x2 > x_name_max:
                    x_name_max = x2

    if y_name is None:
        return

    comp_val = re.sub(r"\s+", "", fields_heur.get("company", "").lower())
    refined_titles = []
    for bbox, text, conf, *_ in ocr_results:
        clean = text.strip()
        if not clean or not is_valid_ocr(clean, conf):
            continue
        if should_skip_noise_line(clean):
            continue
        if comp_val and comp_val in re.sub(r"\s+", "", clean.lower()):
            continue
        if COMPANY_STRONG_RE.search(clean):
            continue
        if TITLE_RE.search(clean):
            yc_t = (bbox[0][1] + bbox[2][1]) / 2
            x1_t, x2_t = bbox[0][0], bbox[1][0]
            if abs(yc_t - y_name) <= SpatialConfig.TITLE_REFINE_Y_OFFSET and \
               x2_t >= (x_name_min - SpatialConfig.TITLE_REFINE_X_OFFSET) and \
               x1_t <= (x_name_max + SpatialConfig.TITLE_REFINE_X_OFFSET):
                if clean not in refined_titles:
                    refined_titles.append(clean)

    if refined_titles:
        fields_heur["title"] = "; ".join(refined_titles)
    else:
        logger.info(f"  No spatially refined title found, keeping heuristic title: '{fields_heur.get('title', '')}'")


def _refine_name_with_bbox(fields_heur: dict, cleaned_temp_lines: list[str], ocr_results: list,
                           y_title, x_title_min, x_title_max, is_full_jp: bool = False):
    heur_name = fields_heur.get("full_name", "")
    is_latin_confident, trusted_latin, skip_bbox_refinement = False, "", False
    if not heur_name:
        return skip_bbox_refinement, is_latin_confident, trusted_latin

    name_parts = [p.strip() for p in heur_name.split("/") if p.strip()]
    parts_info = []
    for p in name_parts:
        p_low = p.lower()
        for bbox, text, conf, *_ in ocr_results:
            t_low = text.strip().lower()
            if p_low in t_low or t_low in p_low:
                parts_info.append({"y": (bbox[0][1] + bbox[2][1]) / 2, "x1": bbox[0][0], "x2": bbox[1][0], "text": p})
                break
        else:
            # If a part isn't found in OCR, don't break yet, just track it
            pass

    email_val = fields_heur.get("email", "")
    if not is_full_jp and email_val and "@" in email_val:
        email_user = email_val.split("@")[0].lower()
        email_dense = re.sub(r"[^a-z]", "", email_user)
        name_tokens = [t.lower() for t in re.split(r"[\s/._-]+", heur_name) if len(t) >= 3]
        if any(nt in email_dense or email_dense in nt for nt in name_tokens):
            skip_bbox_refinement, is_latin_confident, trusted_latin = True, True, heur_name.upper()

    if not skip_bbox_refinement and len(parts_info) == 2 and len(name_parts) == 2:
        if abs(parts_info[0]["y"] - parts_info[1]["y"]) <= SpatialConfig.DUAL_NAME_PROXIMITY and \
           abs(parts_info[0]["x1"] - parts_info[1]["x1"]) <= SpatialConfig.DUAL_NAME_ALIGNMENT_X:
            skip_bbox_refinement = True

    if not skip_bbox_refinement and len(parts_info) == 1 and len(name_parts) == 1 and y_title is not None:
        info = parts_info[0]
        thresh_y = SpatialConfig.NAME_TITLE_Y_LATIN if re.search(r"[a-zA-Z]", info["text"]) else SpatialConfig.NAME_TITLE_Y_KANJI
        if abs(info["y"] - y_title) <= thresh_y and x_title_min is not None and x_title_max is not None:
            if not (info["x2"] < (x_title_min - SpatialConfig.NAME_TITLE_X_OFFSET) or info["x1"] > (x_title_max + SpatialConfig.NAME_TITLE_X_OFFSET)):
                skip_bbox_refinement = True

    if not is_full_jp and email_val and "@" in email_val:
        email_user = email_val.split("@")[0].lower()
        email_dense = re.sub(r"[^a-z]", "", email_user)
        # すでに英字パーツがあるかチェック
        latin_p = ""
        if " / " in heur_name:
            parts_cur = [p.strip() for p in heur_name.split(" / ") if p.strip()]
            latin_candidates = [p for p in parts_cur if not re.search(r"[^\x00-\x7F]", p)]
            if latin_candidates:
                latin_p = latin_candidates[0]
        elif re.search(r"[a-zA-Z]", heur_name) and not re.search(r"[^\x00-\x7F]", heur_name):
            latin_p = heur_name.strip()

        if latin_p:
            email_parts = [tp for tp in re.split(r"[^a-z]", email_user) if len(tp) >= 2]
            name_tokens = [t.lower() for t in re.split(r"[\s/._-]+", latin_p) if len(t) >= 3]
            is_match = any(
                nt in email_parts or (len(nt) >= 3 and (nt in email_dense or email_dense in nt)) or
                SequenceMatcher(None, nt, email_dense).find_longest_match(0, len(nt), 0, len(email_dense)).size >= 4
                for nt in name_tokens
            )
            if not is_match:
                for token in email_parts:
                    if len(token) >= 4 and token in latin_p.lower().replace(" ", ""):
                        is_match = True
                        break
            if not is_match:
                for line in cleaned_temp_lines:
                    if any(sw in line.lower() for sw in ["email:", "tel:", "phone:", "fax:", "www.", "http"]):
                        continue
                    if any(tk in email_parts or (len(tk) >= 5 and tk in email_user) for tk in [t.lower() for t in re.split(r"[\s/._-]+", line) if len(t) >= 3]):
                        latin_p, is_match = line, True
                        break
            if is_match:
                is_latin_confident, trusted_latin = True, latin_p.upper()
                # 漢字 / 英字のフォーマットを維持するようにする
                kanji_part = heur_name.split(' / ')[0] if ' / ' in heur_name else (
                    heur_name if re.search(r"[^\x00-\x7F]", heur_name) else ""
                )
                if kanji_part:
                    fields_heur["full_name"] = f"{kanji_part} / {trusted_latin}"
                else:
                    fields_heur["full_name"] = trusted_latin
            elif email_user not in GENERIC_EMAIL_USERS:
                new_name = ""
                email_tokens = [t for t in re.split(r"[^a-z]", email_user) if len(t) >= 2]
                if len(email_tokens) >= 2:
                    for line in cleaned_temp_lines:
                        if not any(sw in line.lower() for sw in ["email:", "tel:", "phone:", "fax:", "www.", "http"]) and sum(c.isdigit() for c in line) <= 5 and sum(1 for token in email_tokens if token in line.lower()) >= 2:
                            new_name = line.strip()
                            break
                if not new_name and len(email_user) >= 5:
                    for line in cleaned_temp_lines:
                        if not any(sw in line.lower() for sw in ["email:", "tel:", "phone:", "fax:", "www.", "http"]) and sum(c.isdigit() for c in line) <= 5 and (email_user in line.lower() or (len(email_user) >= 6 and email_user[:5] in line.lower())):
                            new_name = line.strip()
                            break
                # Removed fallback name from email username

                label_r = r"(?i)(e[\s-]?mail|mail|tel|phone)(\s*[:.]?)"
                if len(new_name) >= 3:
                    new_name = re.sub(label_r, "", new_name).strip()
                    cur_f = fields_heur.get("full_name", "")
                    # 現在の full_name から漢字を保持するためのロジック
                    # 内部スペースを含む連続した非ASCIIブロックをキャプチャするように正規表現を改善
                    jp_match = re.search(r"(?:[^\x00-\x7F]+(?:\s+[^\x00-\x7F]+)*)", cur_f)
                    if jp_match:
                        jp_part = jp_match.group(0).strip()
                        fields_heur["full_name"] = f"{jp_part} / {new_name.upper()}"
                    else:
                        fields_heur["full_name"] = new_name.upper()
                    is_latin_confident, trusted_latin, skip_bbox_refinement = True, new_name.upper(), True

    return skip_bbox_refinement, is_latin_confident, trusted_latin



def _collect_candidate_ocr(fields_heur: dict, ocr_results: list, skip_bbox_refinement: bool) -> list:
    candidate_ocr = []
    for item in ocr_results:
        bbox, text, conf, *_ = item
        if not is_valid_ocr(text, conf):
            continue
        t = text.strip()
        if should_skip_noise_line(t):
            continue

        is_skip = False
        for key in ("email", "phone"):
            v = fields_heur.get(key)
            if v and (re.sub(r"\s+", "", str(v).lower()) in re.sub(r"\s+", "", t.lower()) or re.sub(r"\s+", "", t.lower()) in re.sub(r"\s+", "", str(v).lower())):
                is_skip = True
                break
        if is_skip or re.search(r"\d(?:.?\d){9,}", t):
            continue

        if not skip_bbox_refinement:
            if COMPANY_HINTS.search(t) and (not VIETNAM_RE.search(t) or COMPANY_STRONG_RE.search(t)):
                continue
            if any(v and (re.sub(r"\s+", "", str(v).lower()) in re.sub(r"\s+", "", t.lower()) or re.sub(r"\s+", "", t.lower()) in re.sub(r"\s+", "", str(v).lower())) for v in [fields_heur.get("company")]):
                continue
            if fields_heur.get("title") and any(len(tt_c := re.sub(r"\s+", "", tt.lower())) > 2 and (tt_c in (t_c := re.sub(r"\s+", "", t.lower())) or t_c in tt_c) for tt in fields_heur["title"].split(";")):
                continue

        candidate_ocr.append(item)
    return candidate_ocr


def _refine_name_from_candidates(fields_heur: dict, candidate_ocr: list, y_title, x_title_min, x_title_max,
                                 is_latin_confident: bool, trusted_latin: str, is_full_jp: bool = False) -> None:
    name_cands = []
    for b, t, c, *_ in candidate_ocr: # 4要素のタプルを安全にサポート
        if y_title is not None and abs((b[0][1] + b[2][1]) / 2 - y_title) > (SpatialConfig.NAME_CANDIDATE_Y_LATIN if re.search(r"[a-zA-Z]", t) else SpatialConfig.NAME_CANDIDATE_Y_KANJI):
            continue
        if x_title_min is not None and (b[1][0] < (x_title_min - SpatialConfig.NAME_CANDIDATE_X_OFFSET) or b[0][0] > (x_title_max + SpatialConfig.NAME_CANDIDATE_X_OFFSET)):
            continue
        # name_cands のすべての要素を保持
        name_cands.append((b, t, c, *_))

    titles_list = [t.strip() for t in fields_heur.get("title", "").split(";") if t.strip()]
    nf = guess_full_name(
        group_ocr_by_y(name_cands, SpatialConfig.NAME_Y_GROUPING_THRESH, SpatialConfig.NAME_X_GROUPING_THRESH),
        fields_heur.get("email"),
        fields_heur.get("company"),
        ocr_results=name_cands,
        is_full_jp=is_full_jp,
        titles=titles_list
    )
    if nf:
        nf = nf.upper()
        # nf が英字のみの場合、fields_heur から既存の漢字を保持
        cur_f = fields_heur.get("full_name", "")
        jp_match = re.search(r"(?:[^\x00-\x7F]+(?:\s+[^\x00-\x7F]+)*)", cur_f)
        if jp_match and not re.search(r"[^\x00-\x7F]", nf):
            jp_part = jp_match.group(0).strip()
            if jp_part not in nf:
                fields_heur["full_name"] = f"{jp_part} / {nf}"
            else:
                fields_heur["full_name"] = nf
        else:
            fields_heur["full_name"] = f"{nf} / {trusted_latin}" if is_latin_confident and " / " not in nf and re.search(r"[^\x00-\x7F]", nf) else nf


def _refine_address_with_bbox(fields_heur: dict, ocr_results: list) -> None:
    cur_addr = fields_heur.get("address", "")
    if not cur_addr:
        return

    anchor_x1, y_min_pool, y_max_pool, addr_blocks = None, None, None, []
    for bbox, text, conf, *_ in ocr_results:
        if cur_addr.lower() in text.lower() or text.lower() in cur_addr.lower():
            x1, yc = bbox[0][0], (bbox[0][1] + bbox[2][1]) / 2
            if anchor_x1 is None or x1 < anchor_x1:
                anchor_x1 = x1
            if y_min_pool is None or yc < y_min_pool:
                y_min_pool = yc
            if y_max_pool is None or yc > y_max_pool:
                y_max_pool = yc
            addr_blocks.append((bbox[0][1], text))

    if anchor_x1 is None:
        return

    logger.info(f"  住所のアンカー: x={anchor_x1:.1f}, y_range=[{y_min_pool:.1f}, {y_max_pool:.1f}]")
    added_count = 0
    for bbox, text, conf, *_ in ocr_results:
        if not is_valid_ocr(text, conf):
            continue
        t = text.strip()
        if should_skip_noise_line(t):
            continue
        if re.search(r"(?i)^[\s,;]*(?:tel|fax|phone|mob(?:ile)?|é›»è©±|ãƒ•ã‚¡ãƒƒã‚¯ã‚¹)[\s:ï¼š()ï¼ˆï¼‰]*[\d\-]", t):
            continue

        yc, t_c = (bbox[0][1] + bbox[2][1]) / 2, re.sub(r"\s+", "", t.lower())
        if any(t.lower() in ex[1].lower() for ex in addr_blocks):
            continue

        if any(v and any((p_c := re.sub(r"\s+", "", p.strip().lower())) and (p_c in t_c or t_c in p_c) for p in str(v).split("/" if k == "full_name" else ";")) for k, v in [("full_name", fields_heur.get("full_name")), ("title", fields_heur.get("title")), ("company", fields_heur.get("company"))]):
            continue
        
        # 明らかに会社のように見える行が取り込まれるのを防ぐ
        if COMPANY_STRONG_RE.search(t):
            continue

        if abs(bbox[0][0] - anchor_x1) <= SpatialConfig.ADDRESS_ALIGNMENT_X and y_min_pool - SpatialConfig.ADDRESS_PROXIMITY_Y <= yc <= y_max_pool + SpatialConfig.ADDRESS_PROXIMITY_Y:
            addr_blocks.append((bbox[0][1], t))
            added_count += 1
            logger.info(f"    Added to address: '{t}' (x_offset={abs(bbox[0][0] - anchor_x1):.1f}, y={yc:.1f})")

    if added_count > 0:
        logger.info(f"  住所にさらに {added_count} 行を追加しました")

    addr_blocks.sort(key=lambda x: x[0])
    fields_heur["address"] = ", ".join([txt for _, txt in addr_blocks])


def _cleanup_full_name(fields_heur: dict) -> None:
    if not (current_full := fields_heur.get("full_name")):
        return

    non_name = re.compile(
        r"(支社|事務所|住所|所在地|〒|postal|zip|address|addr|\d{3}-\d{4}|"
        r"東京都|大阪府|京都府|神奈川県|北海道|沖縄県|丁目|番地|"
        r"ビル|タワー|プラザ|スクエア|電話|tel|phone|mobile|cell|fax|email|e-?mail|mail|url|http|www\.|"
        r"株.{0,2}会社|有限会社|合同会社|製作所|研究所|co\.|ltd\*.|inc\.|corp\.?|incorporated|"
        r"solutions?|technolog(y|ies)|industr(y|ies)|enterprise|trading|"
        r"association|federation|foundation|institute|society|union|organization|council|committee|"
        r"代表取締役|取締役|執行役員|社長|副社長|専務|常務|部長|次長|課長|係長|主任|主査|室長|所長|工場長|支店長|グループ長|"
        r"副部長|副課長|副長|主幹|参事|参与|嘱託|理事|監事|幹事|主事|"
        r"株式|有限|合同|合資|相互|一般|公益|医療|学校|監査|法人|財団|社団|協会|連盟|学会|組合|協議会|委員会|"
        r"ディレクター|マネージャー|最高経営責任者|最高技術責任者|最高財務責任者|最高執行責任者|"
        r"生産技術部|営業部|技術部|製造部|品質部|開発部|総務部|人事部|経理部|企画部|外国部|大規模法人部門|法人部門|法人部|事業部|推進部|推進室|企画室|本部|部門|部署|工業|"
        r"生産技[术術]领域|大規模法人|総括|専門職|"
        r"製造|現場|センター|オフィス|オフイス|ビルディング|勤務先|製造現場|工場|第|"
        r"学校法人|財団法人|社団法人|協会|連盟|学会|組合|協議会|委員会|限门|本社|支店|営業所|事業所|拠点|出張所|倉庫|物品倉庫|配送センター|ロジスティクス|保管庫|物流|"
        r"新たな感動|驚き|創出|新たな価値|をつくる|を支援)",
        re.I,
    )
    parts = [p.strip() for p in current_full.split("/") if p.strip()]
    fields_heur["full_name"] = " / ".join(p for p in parts if not non_name.search(p))


# Removed _ensure_name_from_email logic


def _refine_company_using_email_domain(fields_heur: dict, ocr_results: list, feats: list[dict] | None = None) -> None:
    comp_current = fields_heur.get("company", "")
    em_current = fields_heur.get("email", "")
    if comp_current and (EMAIL_RE.search(comp_current) or "e-mail" in comp_current.lower() or "email" in comp_current.lower()):
        comp_current = ""
        fields_heur["company"] = ""

    if "@" not in em_current:
        return

    domain = extract_email_domain(em_current)
    if not domain:
        return

    # ユーザー要件: 会社名が「強力」（法的接尾辞あり）か「十分に長い」（スペース修正後3単語以上）かをチェック
    comp_fixed = fix_ocr_spaces(comp_current)
    is_strong = bool(comp_current and COMPANY_LEGAL_RE.search(comp_current) and not LEGAL_SUFFIX_ONLY_RE.match(comp_current))
    is_long = bool(comp_fixed and len(comp_fixed.split()) >= 3)
    
    # 新規: 「会社名」が単に full_name と一致する個人名である場合はスキップしない
    fn_low = fields_heur.get("full_name", "").lower()
    comp_low = comp_fixed.lower()
    is_likely_name = bool(fn_low and (comp_low in fn_low or fn_low in comp_low))
    
    if (is_strong or is_long) and not is_likely_name:
        logger.info(f"  会社名が堅牢です ('{comp_current}')。リファインをスキップします。")
        return

    logger.info(f"  メールのドメインを使用して会社名を取り込んでいます: '{domain}'")
    domain_clean = re.sub(r"\s+", "", domain.lower())
    if not comp_current:
        pool_raw = feats if feats is not None else _build_line_features(ocr_results)
        pool = [f for f in pool_raw if not (f["has_email"] or f["has_phone"] or f["has_url"])]
        if pool:
            best_c = max(pool, key=lambda f: _score_company_line(f, domain_clean))
            # ドメインが一致する場合は閾値を緩和
            threshold = 6.5 if domain_clean else 7.5
            if _score_company_line(best_c, domain_clean) >= threshold:
                fields_heur["company"] = best_c["text"]
                comp_current = best_c["text"]
    candidates = [
        text.strip() for bbox, text, conf, *_ in ocr_results
        if is_valid_ocr(text, conf) and text and text.strip()
        and not should_skip_noise_line(text.strip())
        and not (em_current and em_current.lower() in text.lower())
        and "@" not in text
        and not EMAIL_RE.search(text)
        and not re.search(r"(?i)\b(?:e[\-\s._]?mail)\b", text)
    ]

    if comp_current:
        comp_clean = re.sub(r"\s+", "", comp_current.lower())
        if domain_clean in comp_clean or comp_clean in domain_clean:
            logger.info("  ドメインが現在の会社名と一致します")
        else:
            logger.info("  ドメインが現在の会社名と一致しません。候補を検索しています...")
            comp_current = ""

    if not comp_current:
        domain_tokens = [t for t in re.split(r"[^a-z0-9]+", domain.lower()) if len(t) >= 3]
        best_match = None
        best_score = 0
        for cand in candidates:
            cand_clean = re.sub(r"\s+", "", cand.lower())
            score = 0
            score += sum(1 for tok in domain_tokens if tok in cand_clean)
            cand_tokens = [t for t in re.split(r"[^a-z0-9]+", cand.lower()) if len(t) >= 3]
            score += sum(1 for tok in cand_tokens if tok in domain_clean)
            if score > best_score:
                best_score = score
                best_match = cand

        if best_match and best_score > 0:
            logger.info(f"  トークンの一致が見つかりました: '{best_match}' (score={best_score})")
            fields_heur["company"] = best_match
            comp_current = best_match

    if not comp_current:
        longest_cand = max(candidates, key=len) if candidates else ""
        if longest_cand:
            longest_clean = re.sub(r"\s+", "", longest_cand.lower())
            domain_clean = re.sub(r"\s+", "", domain.lower())
            if domain_clean in longest_clean or longest_clean in domain_clean:
                logger.info(f"  最長の候補との部分文字列一致が見つかりました: '{longest_cand}'")
                fields_heur["company"] = longest_cand
                comp_current = longest_cand

    if not comp_current:
        formatted = format_domain_as_company(domain)
        formatted = _legalize_company_name(
            formatted,
            domain=domain,
            address=fields_heur.get("address", ""),
            ocr_results=ocr_results,
        )
        logger.info(f"  フォーマットされたドメインを会社名として使用します: '{formatted}'")
        fields_heur["company"] = formatted


def parse_bcard_with_bbox(ocr_results, return_full_results=False, filtering_bbox: dict = None) -> dict | tuple[dict, list]:
    logger.info("=" * 80)
    logger.info("OCR生テキスト (オリジナル):")
    for i, (bbox, txt, conf, *_) in enumerate(ocr_results, 1):
        logger.info(f"  [{i}] {txt} (conf={conf:.2f})")
    
    # オプションのロジックフィルタリング: YOLOのバウンディングボックス内のOCR結果のみを保持
    if filtering_bbox and isinstance(filtering_bbox, dict):
        # YOLOがわずかに切り取った実際のテキストや、歪み補正中にずれたテキストを
        # 誤って除外しないように、十分なマージン（パディング）を使用します。
        # ただし、遠くの背景ノイズを除外できる程度にタイトである必要があります。
        margin = 150 # 安全のための大きなマージン
        fx1 = filtering_bbox.get("x1", 0) - margin
        fy1 = filtering_bbox.get("y1", 0) - margin
        fx2 = filtering_bbox.get("x2", 9999) + margin
        fy2 = filtering_bbox.get("y2", 9999) + margin
        
        filtered = []
        for item in ocr_results:
            # bboxは通常、4つのポイントのリスト [(x,y), (x,y), (x,y), (x,y)] です
            # OCRボックスの中心が filtering_bbox 内にあるかチェック
            box = item[0]
            cx = sum(p[0] for p in box) / 4
            cy = sum(p[1] for p in box) / 4
            
            if fx1 <= cx <= fx2 and fy1 <= cy <= fy2:
                filtered.append(item)
            else:
                logger.debug(f"  [除外済み] {item[1]} (center={cx:.1f},{cy:.1f} vs bbox={fx1:.1f},{fy1:.1f}-{fx2:.1f},{fy2:.1f})")
        
        if filtered:
            logger.info(f"フィルタリング: {len(ocr_results)} -> {len(filtered)} 個の結果をYOLO bbox内に保持しました。")
            ocr_results = filtered
        else:
            logger.warning("フィルタリング: YOLO bbox内にOCR結果が見つかりません（座標をチェックしてください）！ フィルタリングをスキップします。")

    logger.info("=" * 80)

    # 名刺が上下逆さまの場合は向きを正規化
    ocr_results = _normalize_ocr_orientation(ocr_results)

    raw_lines = [text.strip() for bbox, text, conf, *_ in ocr_results if is_valid_ocr(text, conf)]
    text_raw = "\n".join(raw_lines).strip()

    is_full_jp = detect_is_full_jp(ocr_results)
    fields_heur, cleaned_temp_lines = parse_bcard_fields(text_raw, ocr_results=ocr_results, is_full_jp=is_full_jp)
    if "title" not in fields_heur:
        fields_heur["title"] = ""
    feats = _bootstrap_fields_with_scoring(fields_heur, ocr_results)

    _refine_address_with_bbox(fields_heur, ocr_results)
    _refine_title_by_name_anchor(fields_heur, ocr_results)
    y_title, x_title_min, x_title_max = _compute_title_anchor(fields_heur, ocr_results)

    skip_bbox_refinement, is_latin_confident, trusted_latin = _refine_name_with_bbox(
        fields_heur,
        cleaned_temp_lines,
        ocr_results,
        y_title,
        x_title_min,
        x_title_max,
        is_full_jp=is_full_jp,
    )

    candidate_ocr = _collect_candidate_ocr(fields_heur, ocr_results, skip_bbox_refinement)
    if not skip_bbox_refinement:
        _refine_name_from_candidates(
            fields_heur,
            candidate_ocr,
            y_title,
            x_title_min,
            x_title_max,
            is_latin_confident,
            trusted_latin,
            is_full_jp=is_full_jp,
        )

    _cleanup_full_name(fields_heur)
    # 最終的なロジッククリーンアップ
    _refine_company_using_email_domain(fields_heur, ocr_results, feats=feats)

    for _field in ("company", "address", "title", "full_name"):
        if fields_heur.get(_field):
            val = fix_ocr_spaces(fields_heur[_field])
            if _field == "full_name":
                # 厳格: full_name には絶対に数字を入れない
                val = re.sub(r"\d+", "", val)
            elif _field == "company":
                # 会社名には数字を保持（例: 3A CONSULTING）
                pass 
            val = re.sub(r"\s+", " ", val).strip(" ,.-/")
            fields_heur[_field] = val

    logger.info(f"parse_bcard_with_bbox の結果: {fields_heur}")
    if return_full_results:
        return fields_heur, ocr_results
    return fields_heur
