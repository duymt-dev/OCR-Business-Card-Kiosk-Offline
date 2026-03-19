import os
import json
import time
import sqlite3
import logging
import shutil
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

# ログの設定
logger = logging.getLogger("kiosk.db")

_ALLOWED_SORT_COLUMNS = {
    "created_at",
    "full_name",
    "company",
    "email",
    "phone",
    "title",
    "address",
    "registration_id",
}

def get_db_path():
    """
    database フォルダ内の registrations.db への絶対パスを返します。
    """
    # このファイルは database/update_database.py にあるため、
    # .db ファイルは同じディレクトリにある必要があります。
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'registrations.db')


def get_connection(db_path=None):
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database(db_path=None):
    """
    SQLite データベースを初期化し、存在しない場合は registrations テーブルを作成します。
    """
    if db_path is None:
        db_path = get_db_path()
        
    try:
        # ディレクトリが存在することを確認
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        conn = get_connection(db_path)
        cursor = conn.cursor()
        
        # テーブルを作成
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_id TEXT UNIQUE,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            title TEXT,
            company TEXT,
            address TEXT,
            last_bcard_text TEXT,
            bcard_link TEXT,
            face_link TEXT,
            qr_link TEXT,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'admin',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
        ''')
        conn.commit()
        _ensure_default_admin(conn)
        return conn
    except Exception as e:
        print(f"Database setup error: {e}")
        return None


def _ensure_default_admin(conn):
    cursor = conn.cursor()
    row = cursor.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    if int(row["c"]) > 0:
        return

    default_username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    default_password = os.getenv("ADMIN_PASSWORD", "admin123")
    default_display_name = os.getenv("ADMIN_DISPLAY_NAME", "Administrator").strip() or "Administrator"

    cursor.execute(
        """
        INSERT INTO users (username, password_hash, display_name, role, is_active)
        VALUES (?, ?, ?, 'admin', 1)
        """,
        (default_username, generate_password_hash(default_password), default_display_name),
    )
    conn.commit()
    logger.warning(
        "Default admin user was created. username=%s password=%s. Change ADMIN_PASSWORD in environment for production.",
        default_username,
        default_password,
    )


def create_user(
    username: str,
    password: str,
    *,
    display_name: Optional[str] = None,
    role: str = "admin",
    is_active: int = 1,
    db_path=None,
):
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username is required")
    if not password:
        raise ValueError("password is required")

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (username, password_hash, display_name, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username,
                generate_password_hash(password),
                (display_name or "").strip() or username,
                (role or "admin").strip() or "admin",
                int(is_active),
            ),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "username": username}
    finally:
        conn.close()


def get_user_by_username(username: str, db_path=None):
    username = (username or "").strip().lower()
    if not username:
        return None
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT id, username, password_hash, display_name, role, is_active, created_at, last_login
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def authenticate_user(username: str, password: str, db_path=None):
    user = get_user_by_username(username, db_path=db_path)
    if not user:
        return None
    if int(user.get("is_active") or 0) != 1:
        return None
    if not check_password_hash(user.get("password_hash", ""), password or ""):
        return None

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
            (user["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user.get("role") or "admin",
    }

def save_to_sqlite(reg_id, payload, data, bcard_fields, reg_folder):
    """
    単一の登録情報を SQLite データベースに保存します。
    application.py から呼び出されます。
    """
    logger.info("save_to_sqlite called for reg_id: %s", reg_id)
    try:
        db_path = get_db_path()
        conn = get_connection(db_path)
        cursor = conn.cursor()
        
        # 画像パスを決定 (絶対パスではなく、ポータブルな URL パス)
        def pick_image_url(stem: str) -> str:
            for ext in ("jpeg", "jpg", "png", "webp"):
                p = reg_folder / f"{stem}.{ext}"
                if p.exists():
                    return f"/registrations/{reg_id}/{p.name}"
            return f"/registrations/{reg_id}/{stem}.jpeg"

        bcard_link = pick_image_url("bcard")
        face_link = pick_image_url("face")
        qr_link = str((reg_folder / 'registration_qr.png').absolute())
        
        # データベースへの INSERT または REPLACE
        logger.info(f"DEBUG_DB: Executing INSERT OR REPLACE for {reg_id}")
        cursor.execute('''
        INSERT OR REPLACE INTO registrations (
            registration_id, full_name, email, phone, title, company, address, 
            last_bcard_text, bcard_link, face_link, qr_link, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ''', (
            reg_id,
            bcard_fields.get('full_name', ''),
            bcard_fields.get('email', ''),
            bcard_fields.get('phone', ''),
            bcard_fields.get('title', ''),
            bcard_fields.get('company', ''),
            bcard_fields.get('address', ''),
            payload.get('last_bcard_text', ''),
            bcard_link,
            face_link,
            qr_link
        ))
        conn.commit()
        conn.close()
        logger.info(f"DEBUG_DB: Successfully saved {reg_id} to SQLite.")
    except Exception as db_err:
        logger.error(f"DEBUG_DB: {reg_id} の SQLite データベース更新に失敗しました: {db_err}", exc_info=True)


def update_registration_with_ocr(reg_id: str, bcard_fields: dict, last_bcard_text: str = ""):
    """
    OCR フィールドを使用して既存の登録情報を更新します。
    'database is locked' に対する再試行ロジックが含まれています。
    """
    db_path = get_db_path()
    max_retries = 5
    retry_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            conn = get_connection(db_path)
            cursor = conn.cursor()
            
            # レコードが存在する場合のみ更新
            cursor.execute('''
            UPDATE registrations SET 
                full_name = ?, email = ?, phone = ?, title = ?, company = ?, address = ?, 
                last_bcard_text = ?, created_at = datetime('now', 'localtime')
            WHERE registration_id = ?
            ''', (
                bcard_fields.get('full_name', ''),
                bcard_fields.get('email', ''),
                bcard_fields.get('phone', ''),
                bcard_fields.get('title', ''),
                bcard_fields.get('company', ''),
                bcard_fields.get('address', ''),
                last_bcard_text,
                reg_id
            ))

            if cursor.rowcount == 0:
                if attempt < max_retries - 1:
                    logger.warning(f"DEBUG_DB: 更新対象のレコード {reg_id} が見つかりません。{retry_delay}秒後に再試行します... (試行 {attempt+1}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"DEBUG_DB: すべての再試行後もレコード {reg_id} が見つかりませんでした。OCR 結果を更新できません。")
                    return False
            
            conn.commit()
            conn.close()
            logger.info(f"DEBUG_DB: Successfully updated OCR results for {reg_id} (Attempt {attempt+1})")
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                logger.warning(f"DEBUG_DB: Database locked, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                continue
            logger.error(f"DEBUG_DB: 登録情報 {reg_id} の更新に失敗しました: {e}")
            break
        except Exception as e:
            logger.error(f"DEBUG_DB: 登録情報 {reg_id} の更新中に予期しないエラーが発生しました: {e}")
            break
    return False


def save_to_sqlite_with_retry(reg_id, payload, data, bcard_fields, reg_folder):
    """
    再試行ロジックを使用して、単一の登録情報を SQLite データベースに保存します。
    """
    db_path = get_db_path()
    max_retries = 5
    retry_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            conn = get_connection(db_path)
            cursor = conn.cursor()
            
            # 画像パスを決定
            def pick_image_url(stem: str) -> str:
                for ext in ("jpeg", "jpg", "png", "webp"):
                    p = reg_folder / f"{stem}.{ext}"
                    if p.exists():
                        return f"/registrations/{reg_id}/{p.name}"
                return f"/registrations/{reg_id}/{stem}.jpeg"

            bcard_link = pick_image_url("bcard")
            face_link = pick_image_url("face")
            qr_link = str((reg_folder / 'registration_qr.png').absolute())
            
            cursor.execute('''
            INSERT OR REPLACE INTO registrations (
                registration_id, full_name, email, phone, title, company, address, 
                last_bcard_text, bcard_link, face_link, qr_link, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ''', (
                reg_id,
                bcard_fields.get('full_name', ''),
                bcard_fields.get('email', ''),
                bcard_fields.get('phone', ''),
                bcard_fields.get('title', ''),
                bcard_fields.get('company', ''),
                bcard_fields.get('address', ''),
                payload.get('last_bcard_text', ''),
                bcard_link,
                face_link,
                qr_link
            ))
            conn.commit()
            conn.close()
            logger.info(f"DEBUG_DB: Successfully saved {reg_id} to SQLite (Attempt {attempt+1}).")
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                logger.warning(f"DEBUG_DB: Database locked, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                continue
            logger.error(f"DEBUG_DB: Failed to save {reg_id}: {e}")
            break
        except Exception as e:
            logger.error(f"DEBUG_DB: {reg_id} の保存中に予期しないエラーが発生しました: {e}")
            break
    return False

def _sanitize_sort(sort_by: str, sort_dir: str) -> tuple[str, str]:
    sort_col = sort_by if sort_by in _ALLOWED_SORT_COLUMNS else "created_at"
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    return sort_col, direction


def list_registrations(
    *,
    search: str = "",
    page: int = 1,
    page_size: int = 10,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    date_from: str = "",
    date_to: str = "",
):
    page = max(1, int(page))
    page_size = max(1, min(10000, int(page_size)))
    offset = (page - 1) * page_size
    sort_col, direction = _sanitize_sort(sort_by, sort_dir)
    kw = f"%{(search or '').strip()}%"

    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()
    where_sql = """
        WHERE (? = '' OR
            lower(registration_id) LIKE lower(?) OR
            lower(full_name) LIKE lower(?) OR
            lower(company) LIKE lower(?) OR
            lower(email) LIKE lower(?) OR
            lower(phone) LIKE lower(?) OR
            lower(title) LIKE lower(?) OR
            lower(address) LIKE lower(?) OR
            lower(last_bcard_text) LIKE lower(?))
          AND (? = '' OR date(created_at) >= date(?))
          AND (? = '' OR date(created_at) <= date(?))
    """
    where_params = [
        search.strip(),
        kw, kw, kw, kw, kw, kw, kw, kw,
        date_from, date_from,
        date_to, date_to,
    ]

    conn = get_connection()
    try:
        cur = conn.cursor()
        total = cur.execute(
            f"SELECT COUNT(*) AS c FROM registrations {where_sql}",
            where_params,
        ).fetchone()["c"]
        rows = cur.execute(
            f"""
            SELECT registration_id, full_name, company, email, phone, title, address, last_bcard_text, bcard_link, face_link, qr_link, created_at
            FROM registrations
            {where_sql}
            ORDER BY {sort_col} {direction}
            LIMIT ? OFFSET ?
            """,
            [*where_params, page_size, offset],
        ).fetchall()
        data = [dict(r) for r in rows]
        return {
            "items": data,
            "page": page,
            "page_size": page_size,
            "total": int(total),
            "total_pages": (int(total) + page_size - 1) // page_size,
            "sort_by": sort_col,
            "sort_dir": direction.lower(),
            "search": search.strip(),
        }
    finally:
        conn.close()


def get_registration(registration_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT registration_id, full_name, company, email, phone, title, address,
                   last_bcard_text, bcard_link, face_link, qr_link, created_at
            FROM registrations
            WHERE registration_id = ?
            """,
            (registration_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_registration(registration_id: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM registrations WHERE registration_id = ?", (registration_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_registration(registration_id: str, updates: dict) -> bool:
    if not updates:
        return False
    allowed = {"full_name", "company", "email", "phone", "title"}
    payload = {k: updates[k] for k in updates if k in allowed}
    if not payload:
        return False
    set_sql = ", ".join([f"{k} = ?" for k in payload.keys()])
    values = list(payload.values()) + [registration_id]
    conn = get_connection()
    try:
        cur = conn.cursor()
        exists = cur.execute(
            "SELECT 1 FROM registrations WHERE registration_id = ?",
            (registration_id,),
        ).fetchone()
        if not exists:
            return False
        cur.execute(
            f"UPDATE registrations SET {set_sql} WHERE registration_id = ?",
            values,
        )
        conn.commit()
        return True
    finally:
        conn.close()


def dashboard_stats():
    conn = get_connection()
    try:
        cur = conn.cursor()
        total = cur.execute("SELECT COUNT(*) AS c FROM registrations").fetchone()["c"]
        today = cur.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE date(created_at) = date('now', 'localtime')"
        ).fetchone()["c"]
        with_email = cur.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE coalesce(trim(email), '') <> ''"
        ).fetchone()["c"]
        with_phone = cur.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE coalesce(trim(phone), '') <> ''"
        ).fetchone()["c"]
        return {
            "total": int(total),
            "today": int(today),
            "with_email": int(with_email),
            "with_phone": int(with_phone),
        }
    finally:
        conn.close()


def wipe_all_registrations():
    """
    登録情報をすべて削除し、registrations/ フォルダ内のすべてのファイルを削除します。
    """
    db_path = get_db_path()
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        # 1. すべてのデータベースレコードを削除
        cursor.execute("DELETE FROM registrations")
        # オプション: autoincrement をリセット
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='registrations'")
        conn.commit()
        logger.warning("DATABASE FULL WIPE: All registration records deleted from SQLite.")
    except Exception as e:
        logger.error(f"Failed to wipe registrations table: {e}")
    finally:
        conn.close()

    # 2. すべての登録フォルダを削除
    # 登録フォルダはルートの 'registrations' フォルダにあると想定
    # application.py に基づき、Path("registrations")
    current_dir = Path(__file__).parent.absolute()
    reg_dir = current_dir.parent / "registrations"
    
    if reg_dir.exists():
        try:
            # ディレクトリ自体を削除すると権限の問題が発生する可能性があるため、子要素のみを削除
            for item in reg_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            logger.warning(f"DISK FULL WIPE: All contents of {reg_dir} deleted.")
        except Exception as e:
            logger.error(f"Failed to wipe registrations directory {reg_dir}: {e}")


def check_and_trigger_cleanup(limit=10000):
    """
    登録総数が制限を超えているか確認します。
    超えている場合は、一括削除を実行します。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        count = cursor.execute("SELECT COUNT(*) AS c FROM registrations").fetchone()["c"]
        conn.close()

        if int(count) >= limit:
            logger.warning(f"Registration limit reached ({count}/{limit}). Triggering FULL WIPE.")
            wipe_all_registrations()
            return True
    except Exception as e:
        logger.error(f"Error in check_and_trigger_cleanup: {e}")
    return False


def list_recent_registrations(limit: int = 10):
    limit = max(1, min(100, int(limit)))
    conn = get_connection()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT registration_id, full_name, company, email, created_at
            FROM registrations
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def process_registrations(base_dir, db_path):
    """
    registrations/ 内のすべてのフォルダをスキャンし、データベースを更新します。
    バッチ更新に使用されます。
    """
    conn = setup_database(db_path)
    if not conn: return
    cursor = conn.cursor()
    
    registrations_dir = Path(base_dir) / 'registrations'
    
    if not registrations_dir.exists():
        print(f"Directory {registrations_dir} does not exist.")
        return

    for folder in registrations_dir.iterdir():
        if folder.is_dir() and folder.name.startswith('REG_'):
            data_file = folder / 'data.json'
            if not data_file.exists():
                continue
                
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                bcard_fields = data.get('bcard_fields', {})
                
                cursor.execute('''
                INSERT OR REPLACE INTO registrations (
                    registration_id, full_name, email, phone, title, company, address, 
                    last_bcard_text, bcard_link, face_link, qr_link, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ''', (
                    folder.name,
                    bcard_fields.get('full_name', ''),
                    bcard_fields.get('email', ''),
                    bcard_fields.get('phone', ''),
                    bcard_fields.get('title', ''),
                    bcard_fields.get('company', ''),
                    bcard_fields.get('address', ''),
                    data.get('last_bcard_text', ''),
                    str((folder / 'bcard.jpeg').absolute()),
                    str((folder / 'face.jpeg').absolute()),
                    str((folder / 'registration_qr.png').absolute())
                ))
                print(f"Processed {folder.name}")
            except Exception as e:
                print(f"Error processing {folder.name}: {e}")
                
    conn.commit()
    conn.close()
    print("Database batch update complete.")

if __name__ == "__main__":
    # 'database' フォルダ内から実行する場合、base_dir は1つ上の階層
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PARENT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
    DB_PATH = os.path.join(CURRENT_DIR, 'registrations.db')
    process_registrations(PARENT_DIR, DB_PATH)
