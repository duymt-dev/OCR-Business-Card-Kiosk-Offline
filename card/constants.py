import re
import os

LATIN_SYS_PROMPT = (
    "You are an information extraction model specialized in reading business cards "
    "in Vietnamese and English. "
    "Your task is to extract structured information from raw OCR text. "
    "Return ONLY one valid JSON object with the keys: "
    "\"full_name\", \"title\", \"email\", \"company\", \"phone\", \"address\". "
    "All values are strings, use \"\" for missing data. "
    "If the company name is missing, infer it from the email domain "
    "(e.g. 'tuyenpv@saomaisoft.com' -> 'SaoMaiSoft'). "
    "Do not output any explanation, only the JSON."
)

JAPANESE_SYS_PROMPT = (
    "You are an information extraction model specialized in reading Japanese business cards, "
    "including cards that mix Japanese and English. "
    "Your task is to extract structured information from raw OCR text. "
    "Return ONLY one valid JSON object with the keys: "
    "\"full_name\", \"title\", \"email\", \"company\", \"phone\", \"address\". "
    "All values are strings, use \"\" for missing data.\n"
    "Rules:\n"
    "• Person names are often written in Japanese (e.g. 小濱隆彦) and may be near titles.\n"
    "• Titles include words like 部長, 課長, 係長, 主任, 代表取締役, 社長, マネージャー, etc.\n"
    "• Company names often contain 株式会社 or 有限会社. "
    "If you see '株式会社' or '有限会社' followed by other characters on the same line, "
    "treat the whole line as company name (e.g. '株式会社アークテイク'). "
    "Never output just '株式会社' or '有限会社' alone as the company name.\n"
    "• If the company still cannot be determined, infer it from the email domain "
    "(e.g. 'xxx@arktake.co.jp' -> 'アークテイク' or 'Arktake').\n"
    "• Keep Japanese characters as-is. You may use romaji only when the name or company "
    "exists only in Latin letters.\n"
    "Output strictly one JSON object and nothing else (no ```json, no comments)."
)

# Fax: ベトナム語 / 英語 / 日本語
FAX_HINT_RE = re.compile(
    r"\b("
    r"fax|facsimile|tel\s*/?\s*fax|phone\s*/?\s*fax|"
    r"số\s*fax|so\s*fax|máy\s*fax|may\s*fax|"
    r"ファックス|ファクス|ＦＡＸ"
    r")\b",
    re.I,
)

# URL (URL + ラベルヒントのみ。完全なドメインパーサーではありません)
URL_RE = re.compile(
    r"https?://[^\s]+|www\.[^\s]+",
    re.I,
)

URL_HINT_RE = re.compile(
    r"(?:"
    r"\b(?:website|web\s*site|homepage|home\s*page|url|site|"
    r"trang\s*web|trang\s*chủ|trangchu|"
    r"ホームページ|サイト|ＨＰ)\b|"
    r"(?:website|web\s*site|homepage|url|site|trang\s*web|ホームページ)[:\s]*(?:https?://|www\.)[^\s]+"
    r")",
    re.I,
)

# 税務番号 / MST: ベトナム語 / 英語 / 日本語
TAX_HINT_RE = re.compile(
    r"(?:"
    r"mst|mã\s*số\s*thuế|ma\s*so\s*thue|ma\s*[s560]*\s*th[uếe0-9]*|"
    r"tax|tax\s*code|tax\s*id|tax\s*no\.?|vat\s*no\.?|vat\s*id|"
    r"税番号|法人番号|納税者番号"
    r")",
    re.I,
)

PHONE_LABEL_RE = re.compile(
    r"\b(tel|phone|t|m|mobile|hotline|đt|dd|dt|電\s*話|携\s*帯|携\s*带|điện\s*thoại|di\s*d[o06óòỏõọôốồổỗộ]*ng|office)\b",
    re.I,
)

EMAIL_LABEL_RE = re.compile(r"\b(email|e-mail|mail)\b", re.I)

MISC_NOISE_RE = re.compile(
    r"\b(extrusion|scan\s*to\s*add|this\s*contact|follow\s*us|connect\s*with\s*(?:me|innovation)|innovation|iso|認証取得|新时达|股票代码|勤務先|association|federation|foundation|institute|society|union|organization|council|committee|office|headquarters|branch|representative)\b"
    r"|応援しています|万博を応援|健康経営|SDGs|新たな感動|驚きを創出|新たな価値|をつくる|を支援|に挑戦|をお届け|をご提案|を実現"
    r"|抗菌|抗茵|富士(?:フイルム|フィルム)?|プライバシーマーク"
    r"|生産技[术術]领域|大規模法人|健康保険証|住民票|運転免許証|個人番号|マイナンバー|登録番号|適格請求書"
    r"|再生紙使用|再生纸使用",
    re.I,
)

GENERIC_EMAIL_USERS = {
    "info", "sales", "admin", "contact", "support", "hr", "marketing", 
    "office", "mail", "service", "hello", "webmaster", "postmaster",
    "kiosk", "reception", "desk", "help", "order", "enquiry", "inquiry"
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9._\-]+\.[A-Z]{2,}", re.I)

PHONE_MARKERS = (
    r"(?i)(?:tel|phone|ph\.?|p\.?|mob(?:ile)?|cell|handphone|hp|m\.?|c\.?|"
    r"direct|dir\.?|main|office|work|home|"
    r"đ(?:iện)?\s*t(?:hoại)?|dd|dđ|di\s*động|máy\s*bàn|hotline|cskh|zalo|"
    r"電\s*話|でんわ|テレフォン|ＴＥＬ|ＴＥＬ\.|携\s*帯|携\s*带|代表|直通|内線|連絡先|"
    # OCR 誤認識 / バリエーション
    r"te[l1i]|mo[b6]ile|電詰|電語|電請|ＴＥＬ\.|ＴＥＬ"
    r")"
)

FAX_MARKERS = r"(?i)(?:fax|f\.?|facs|facsimilie|facsimile|ファックス|ファクス|ＦＡＸ|fa[xz]|f\s*a\s*x)"

PHONE_RE = re.compile(
    fr"{PHONE_MARKERS}[:\s(（.\-/]*?"
    r"((?:\+?(?:84|81|86|0))[\d\s()（）.\-]{6,12}\d)",
    re.I,
)

PHONE_DOMESTIC_RE = re.compile(
    fr"{PHONE_MARKERS}[:\s(（.\-/]*?"
    r"(\+?0[\d\s()（）.\-]{6,12}\d)",
    re.I,
)

# ラベルなし版 (フォールバック)
# 避けるべきラベルにネガティブ・ルックビハインドを追加
PHONE_NO_LABEL_RE = re.compile(
    r"(?<!MST)(?<!TAX)(?<!FAX)[:\s]*"
    r"("
    r"(?:\+?\d{1,3})[\d\s()（）.\-]{7,12}\d"
    r"|"
    r"0[\d\s()（）.\-]{8,11}\d"
    r")",
    re.I,
)

FAX_PHONE_RE = re.compile(
    fr"{FAX_MARKERS}[:\s(（.\-/]*?"
    r"((?:\+?(?:84|81|86|0))[\d\s()（）.\-]{6,13}\d)",
    re.I,
)

COMPANY_HINTS = re.compile(
    r"("
    # --- English / International ---
    r"corporation|corp\.?|incorporated|inc\.?|limited|ltd\.?|llc|llp|plc|holdings?|"
    r"pvt|pte|s\.a\.|gmbh|company|group|co.,ltd|"
    r"solutions?|industr(?:y|ies)|int(?:ernationa)?l|"
    r"enterprise|trading|investment|logistics|electric|"
    
    # --- ベトナム語 (標準 & 略語) ---
    r"công\s*ty|cty|tnhh|cổ\s*phần|cp|tập\s*đoàn|tổng\s*công\s*ty|doanh\s*nghiệp|chi\s*nhánh|văn\s*phòng\s*đại\s*diện|"
    r"hợp\s*tác\s*xã|liên\s*doanh|thương\s*mại|dịch\s*vụ|sản\s*xuất|đầu\s*tư|xây\s*dựng|bất\s*động\s*sản|"
    r"tm-dv|sx-tm|mtv|"
    
    # --- ベトナム語 声調なし / OCR ---
    r"cong\s*ty|c0ng\s*ty|c0ng\s*fy|co\s*phan|c0\s*phan|tap\s*doan|tong\s*cty|"
    r"thuong\s*mai|dich\s*vu|dau\s*tu|xay\s*dung|bat\s*dong\s*san|"
    r"tnhhh|tmhh|jnhh|tnnh|" # TNHH typos
    
    # --- 日本語 (法人格 & 業種) ---
    r"株式会社|有限会社|合同会社|合資会社|相互会社|協同組合|"
    r"精工所|製作所|研究所|製薬|生命|損保|銀行|証券|通信|建設|電気|工業|産業|商事|貿易|工所|"
    r"クリニック|病院|法律事務所|"
    r")",
    re.I,
)

COMPANY_LEGAL_RE = re.compile(
    r"("
    r"corporation|corp\.?|incorporated|inc\.?|limited|ltd\.?|td\.?|llc|llp|plc|holdings?|gmbh|co\.|co,"
    r"|association|federation|foundation|institute|society|union|organization"
    r"|công\s*ty|cty|tnhh|cổ\s*phần|cp|tập\s*đoàn|tổng\s*công\s*ty|doanh\s*nghiệp|"
    r"cong\s*ty|c0ng\s*ty|co\s*phan|c0\s*phan|tap\s*doan|congty|"
    r"株式会社|有限会社|合同会社|合資会社|協同組合|"
    r"精工所|工所|製作所|研究所|ホールディングス|コーポレーション|インコーポレイテッド|ステムシンク|"
    r"tnhhh|tmhh|tnnh|" # TNHH の誤入力
    r"抒式会社|株武会社|有限会杜|杜団法人|JSC"
    r")(?:\b|[.,\s]|$|(?=[^\x00-\x7f]))",
    re.I | re.UNICODE,
)

COMPANY_INDUSTRY_RE = re.compile(
    r"("
    r"industries|technologies|manufacturing|solutions?|trading|systems?|services?|group"
    r")(\b|[.,\s]|$)",
    re.I,
)

COMPANY_STRONG_RE = re.compile(
    fr"{COMPANY_LEGAL_RE.pattern}|{COMPANY_INDUSTRY_RE.pattern}|"
    r"株式会社|有限会社|合同会社|協同組合|JSC|学校法人|財団法人|社団法人",
    re.I | re.UNICODE,
)

# 法人格の接尾辞のみ、または接尾辞 + 短い漢字 (1-4 文字) の会社フィールドを検出します。
# 漢字名部分が短すぎて意味をなさない場合、メールのドメイン名による詳細化をトリガーします。
# 一致する例 (→ 詳細化をトリガー):
#   株式会社, 日本株式会社, 株式会社日本, 有限会社東京
# 一致しない例 (→ そのまま保持):
#   東化学工業株式会社, 淀川変圧器株式会社 (5文字以上の漢字 → 有効)
_LEGAL_SUFFIX_PAT = r"株式会社|有限会社|合同会社|合資会社|協同組合|Co\.?,?\s*Ltd\.?|LLC|Inc\.?|Corp\.?|TNHH|GmbH|PLC|JSC|LLP"
_CJK_BARE = r"[\u3000-\u9fff\u3040-\u30ff]?"   # 0 または 1 文字の CJK 文字
LEGAL_SUFFIX_ONLY_RE = re.compile(
    r"^\s*"
    r"(?:" + _CJK_BARE + r")?"        # optional minimal prefix
    r"(?:" + _LEGAL_SUFFIX_PAT + r")"  # 必須の法人格接尾辞
    r"(?:" + _CJK_BARE + r")?"        # optional minimal suffix
    r"\s*\.?\s*$",
    re.I | re.UNICODE,
)

VIETNAM_RE = re.compile(r"viet\s*nam", re.I)

ADDRESS_HINTS = re.compile(
    r"\b("
    # --- 英語 / 国際 ---
    r"address|add|addr|location|base|"
    r"street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|way|highway|hwy|"
    r"floor|fl\.?|level|suite|unit|room|rm\.?|building|bldg\.?|tower|block|lot|plot|"
    r"industrial\s*(?:park|zone|estate)|ipp|iz|technopark|"
    r"district|ward|city|province|state|contact\s*at|"
    
    # --- ベトナム語 (標準 & 略語 & 声調なし) ---
    r"địa\s*chỉ|đ\/c|dia\s*chi|"
    r"số|nhà|đường|phố|ngõ|ngách|hẻm|kiệt|"
    r"quận|huyện|thị\s*xã|thành\s*phố|tỉnh|phường|xã|thôn|ấp|khóm|tổ|"
    r"khu\s*công\s*nghiệp|kcn|khu\s*chế\s*xuất|kcx|cụm\s*cn|ccn|"
    r"tòa\s*nhà|cao\s*ốc|tầng|phòng|"
    # 声調なし / 略語
    r"duong|pho|quan|huyen|thi\s*xa|thanh\s*pho|tinh|phuong|thon|ap|"
    r"tp\.|t\.p\.|q\.|h\.|p\.|"
    
    # --- OCR 誤認識 ---
    r"str|stree[t1]|"
    r"fl[o0][o0]r|"
    r"bldq|bldg|"
    r"addre[s5]{2}"
    r")\b|"
    # --- 日本語 & 複合語 (\b 境界なし) ---
    r"〒|亍|住所|所在地|勤務先|"
    r"都|道|府|県|支庁|振興局|"  # 強力な日本語の住所ヒント
    # 弱い1文字のヒントを除去: 市|区|町|村|郡|番|号
    r"丁\s*目|番\s*地|" # 丁目, 番地
    r"ビ\s*ル|タ\s*ワー|プラザ|スクエア|号\s*室|階|F|センター|オフィス|オフイス|ビルディング|"
    r"製造|現場|工場|本社|支店|営業所|事業所|拠点|出張所|倉庫|物品倉庫|配送センター|ロジスティクス|保管庫|物流|"
    r"東京|京東|大阪|京都|愛知|神奈川|北海道|沖縄|横浜|名古屋|札幌|福岡|神戸|川崎|埼玉|広島|仙台|千葉|"
    r"ホーチミン|ハノイ|ダナン|ハイフォン|カントー|HO\s?CHI\s?MINH|HANOI|DANANG|DA\s?NANG|HAIPHONG|CANTHO|"
    r"丁目|番地",
    re.I | re.UNICODE,
)

JP_ADD_RE = re.compile(r"[\u4E00-\u9FFF]")

JP_ADDR_SUFFIX_RE = re.compile(r"都|道|府|県|市|区|町|村|郡|丁目|番地|号")

VN_ACCENT_RE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ]", re.I)

TITLE_RE = re.compile(
    r"("
    # 複合タイトル (例: CHIEFOFREPRESENTATIVEOFFICE)
    r"chief[\s_-]*of[\s_-]*representative[\s_-]*office|"
    r"[\s_-]*of[\s_-]*representative[\s_-]*office|"
    r"representative[\s_-]*office|"
    # 標準的な英語のタイトル
    r"director|operating|manager|engineer|technician|supervisor|assistant|coordinator|consultant|advisor|division|div|administrator|adminisfrator"
    r"chief|lead|president|ceo|cto|cfo|coo|founder|owner|"
    r"sales|marketing|business|hr|finance|accounting|operations|strategy|promotion|dept|"
    # 日本語のタイトル
    r"WSグループ兼|博士\s*[\(（]工学[\)）]|[\(（]工学[\)）]|[一二]級(?:建築|土木|電気工事|管工事|造園|建設機械|電気通信工事)施工管理技士(?:補)?|施工管理技士|"
    r"(?:社長|会長|役員)特命(?:事項)?(?:担当|推進)?|特命(?:事項)?(?:担当|推進)?|"
    r"总经理|董事长|会長|部長|営業|部長|課長|主任|係長|課長代理|課長補佐|部門長|社長|取締役|代表|グループ長|役員|事長|副理|副理事長|"
    r"課長補佐|課長代理|係長|所長|工場長|支店長|代表取缔役|取缔役|生産技術部|営業部|技術部|製造部|品質部|開発部|総務部|人事部|"
    r"代表取締役|取締役|執行役員|社長|副社長|専務|常務|"
    r"部長|次長|課長|係長|主任|主査|室長|所長|工場長|支店長|"
    r"課長代理|課長補佐|部長代理|部長補佐|副部長|副課長|副長|"
    r"主幹|参事|参与|嘱託|理事|監事|幹事|主事|"
    r"代表|ディレクター|マネージャー|マネジャー|エンジニア|技師|技術者|技士|技能士|"
    r"スーパーバイザー|監督|アシスタント|助手|補佐|オフィサー|役員|"
    r"コーディネーター|コンサルタント|顧問|チーフ|リーダー|スタッフ|"
    r"プレジデント|最高経営責任者|最高技術責任者|最高財務責任者|最高執行責任者|"
    r"創業者|設立者|オーナー|責任者|"
    r"営業|販売|マーケティング|マーケティング|事業|人事|財務|経理|会計|運営|業務|"
    r"生産|技術|開発|製造|品質|企画|総務|設計|研究|"
    r"生産技術部|営業部|技術部|製造部|品質部|開発部|総務部|人事部|経理部|企画部|"
    r"外国部|大規模法人部門|法人部門|法人部|生産技[术術]领域|"
    r"事業部|推進部|推進室|企画室|開発室|事務局|総括|専門職|"
    r"本部|部門|部署|"
    r"員|タ儿一7|タ儿|又子么|加工|"
    # ベトナム語のタイトル
    r"giám\s*đốc|phó\s*giám\s*đốc|tổng\s*giám\s*đốc|chủ\s*tịch|"
    r"trưởng\s*phòng|trưởng\s*ban|trưởng\s*bộ\s*phận|"
    r"quản\s*lý|chuyên\s*viên|kỹ\s*sư|nhân\s*viên|thư\s*ký|"
    r"giam|giam\s*doc|pho\s*giam\s*doc|tong\s*giam\s*doc|chu\s*tich|Tóng|Giám|tong"
    r"truong\s*phong|truong\s*ban|truong\s*bo\s*phan|"
    r"quan\s*ly|chuyen\s*vien|ky\s*su|nhan\s*vien|thu\s*ky|"
    r"kinh doanh|team|san\s*xuat|"
    r"b[oôộ0][\s_-]*ph[aâậ][\s_-]*[nrm]"
    r")",
    re.I,
)

JP_CHAR_RE = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF]")
DEPARTMENT_RE = re.compile(
    r"\b(dept|department|factory|office|representative|division|group|team|section|branch|unit|dept\.?)\b|"
    r"本部|事業部|開発部|営業部|広報部|人事部|総務部|経理部|技術部|製造部|支店|営業所|工場|室|課|係|局",
    re.I
)
JP_NAME_RE = re.compile(
    r"^(?:[\u4E00-\u9FFF]{2,5}|"
    r"[\u4E00-\u9FFF]{1,3}\s+[\u4E00-\u9FFF]{1,3}(?:\s+[\u4E00-\u9FFF]{1,3})?)$"
)

# 単独、または非常に短いペアで現れる場合にノイズになりやすい漢字
JP_BAD_NAME_CHARS_RE = re.compile(r"^[本社支店〒役部課係室局]$")

# ひらがな + カタカナ (句読点/記号ブロック \u3000-\u303f を除く)
KANA_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fc]")

# 抽出された名前から削除する敬称
HONORIFIC_STRIP_RE = re.compile(
    r"\s*[\(（]\s*(?:Mr|Ms|Mrs|Miss|Dr|Prof|Sir|様|氏|くん|ちゃん|さん)\.?\s*[\)）]\s*"
    r"|\s+(?:様|氏|くん|ちゃん|さん)\s*$",
    re.I
)

class SpatialConfig:
    """名刺解析で使用される空間閾値の設定。"""
    
    # グローバル OCR グルーピング
    Y_GROUPING_THRESH = 15
    X_GROUPING_THRESH = 30

    # 名前漢字の OCR グルーピング (離れた文字に対して 20px の垂直偏差を許容)
    NAME_Y_GROUPING_THRESH = 50
    NAME_X_GROUPING_THRESH = 500
    
    # ラテン名の断片に対する OCR グルーピング
    LATIN_NAME_Y_GROUPING_THRESH = 50
    LATIN_NAME_X_GROUPING_THRESH = 500
    
    # 会社のグルーピング
    COMPANY_Y_GROUPING_THRESH = 0
    COMPANY_X_GROUPING_THRESH = 0
    
    # タイトルの精緻化 (名前アンカー周辺)
    TITLE_REFINE_Y_OFFSET = 150
    TITLE_REFINE_X_OFFSET = 10
    
    # 名前の検証 (空間的)
    DUAL_NAME_PROXIMITY = 40   # 漢字とラテン名のパーツ間の最大距離
    DUAL_NAME_ALIGNMENT_X = 30  # デュアルネームの最大水平オフセット
    NAME_TITLE_Y_LATIN = 60    # ラテン名 vs タイトルの Y 距離閾値
    NAME_TITLE_Y_KANJI = 60    # 漢字名 vs タイトルの Y 距離閾値
    NAME_TITLE_X_OFFSET = 80   # 名前とタイトルの整列に対する X オフセット許容誤差
    
    # 名前候補のフィルタリング (初期検索時に使用)
    NAME_CANDIDATE_Y_LATIN = 60  
    NAME_CANDIDATE_Y_KANJI = 60  
    NAME_CANDIDATE_X_OFFSET = 80 
    
    # 住所の精緻化
    ADDRESS_ALIGNMENT_X = 40
    ADDRESS_PROXIMITY_Y = 40 
