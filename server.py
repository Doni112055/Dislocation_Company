from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date
import sqlite3
import hashlib
import json
import os
import io
import threading
import time
import shutil

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "dislocation.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
AUTO_BACKUP_DIR = os.path.join(BACKUP_DIR, "auto")


def perform_auto_backup(label: str = "auto") -> Optional[str]:
    """Автоматический фоновый снимок базы данных SQLite на лету без блокировки"""
    try:
        os.makedirs(AUTO_BACKUP_DIR, exist_ok=True)
        if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
            return None

        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target_path = os.path.join(AUTO_BACKUP_DIR, f"dislocation_{label}_{ts_str}.db")

        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(target_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        # Ротация: храним историю автобэкапов за последние 30 дней
        cutoff = datetime.now().timestamp() - (30 * 86400)
        for fname in os.listdir(AUTO_BACKUP_DIR):
            fpath = os.path.join(AUTO_BACKUP_DIR, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                try:
                    os.remove(fpath)
                except Exception:
                    pass

        return target_path
    except Exception as e:
        print(f"⚠️ Ошибка автобэкапа: {e}")
        return None


def self_heal_database_if_needed():
    """Автоматическое самовосстановление базы, если файл был поврежден или случайно удален"""
    try:
        if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
            candidates = []
            for b_dir in [AUTO_BACKUP_DIR, BACKUP_DIR]:
                if os.path.exists(b_dir):
                    for fname in os.listdir(b_dir):
                        if fname.endswith(".db"):
                            fp = os.path.join(b_dir, fname)
                            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                                candidates.append((os.path.getmtime(fp), fp))
            if candidates:
                candidates.sort(reverse=True)
                latest_backup = candidates[0][1]
                shutil.copy2(latest_backup, DB_PATH)
                print(f"🛡️ [САМОВОССТАНОВЛЕНИЕ] База данных успешно восстановлена из резервной копии: {latest_backup}")
    except Exception as e:
        print(f"⚠️ Ошибка самовосстановления: {e}")


app = FastAPI(
    title="Система Дислокации Контейнеров PRO",
    description="Корпоративный сервер мониторинга и дислокации контейнеров для всей компании",
    version="3.0.0"
)

# Разрешаем подключение со всех рабочих станций компании в локальной сети
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    con = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # Включаем WAL-режим для высокой параллельности (многопользовательская запись без блокировок)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=10000;")
    return con


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():
    self_heal_database_if_needed()
    con = get_db()
    cur = con.cursor()

    # Таблица пользователей системы
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT DEFAULT '',
        role TEXT DEFAULT 'dispatcher',
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """)

    # Основная таблица дислокации контейнеров (со всеми необходимыми полями)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS containers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        container_no TEXT NOT NULL,
        client TEXT DEFAULT '',            -- Грузополучатель
        cargo_name TEXT DEFAULT '',        -- Наименование груза
        barge TEXT DEFAULT '',             -- Баржа
        destination TEXT NOT NULL,         -- Нахождение (Хайратон, Термез-порт)
        status TEXT NOT NULL,              -- Статус (Груженный, Порожний)
        reason TEXT DEFAULT '',            -- Причина / примечание
        sent_date TEXT NOT NULL,           -- yyyy-mm-dd
        return_date TEXT DEFAULT NULL,     -- yyyy-mm-dd (дата возврата / прибытия)
        country TEXT DEFAULT '',           -- Страна
        added_by TEXT DEFAULT '',          -- Пользователь, добавивший запись
        changed_by TEXT DEFAULT '',        -- Пользователь, изменивший запись
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # Миграция: автоматическое добавление колонок client, barge, cargo_name
    cur.execute("PRAGMA table_info(containers)")
    cols = [r["name"] for r in cur.fetchall()]
    if "client" not in cols:
        cur.execute("ALTER TABLE containers ADD COLUMN client TEXT DEFAULT ''")
    if "cargo_name" not in cols:
        cur.execute("ALTER TABLE containers ADD COLUMN cargo_name TEXT DEFAULT ''")
    if "barge" not in cols:
        cur.execute("ALTER TABLE containers ADD COLUMN barge TEXT DEFAULT ''")

    # Полный журнал аудита изменений (кто, когда, что изменил)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS container_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        container_id INTEGER NOT NULL,
        container_no TEXT NOT NULL,
        action TEXT NOT NULL,             -- CREATE, UPDATE, DELETE
        changes TEXT NOT NULL,            -- Список изменений в JSON
        user TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )
    """)

    # Единый реестр контейнеров компании
    cur.execute("""
    CREATE TABLE IF NOT EXISTS registry (
        container_no TEXT PRIMARY KEY,
        active INTEGER NOT NULL DEFAULT 1,
        note TEXT DEFAULT '',
        replaced_by TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """)

    # Гарантированное закрепление профиля Администратора навсегда: логин admin, пароль 12345
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("SELECT id FROM users WHERE LOWER(username) = 'admin'")
    admin_row = cur.fetchone()
    admin_p_hash = hash_password("12345")
    if admin_row:
        cur.execute("""
            UPDATE users 
            SET password_hash = ?, role = 'admin', full_name = 'Главный диспетчер (Админ)', is_active = 1 
            WHERE id = ?
        """, (admin_p_hash, admin_row["id"]))
    else:
        cur.execute("""
            INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
            VALUES ('admin', ?, 'Главный диспетчер (Админ)', 'admin', 1, ?)
        """, (admin_p_hash, ts))

    # Гарантированное закрепление профиля Оператора: логин operator, пароль 12345
    cur.execute("SELECT id FROM users WHERE LOWER(username) = 'operator'")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
            VALUES ('operator', ?, 'Оператор мониторинга', 'dispatcher', 1, ?)
        """, (hash_password("12345"), ts))

    # Таблица архива удаленных/завершенных рейсов контейнеров (для отчетов за любые периоды)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS container_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_id INTEGER,
        container_no TEXT NOT NULL,
        client TEXT DEFAULT '',
        cargo_name TEXT DEFAULT '',
        barge TEXT DEFAULT '',
        destination TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT DEFAULT '',
        sent_date TEXT NOT NULL,
        return_date TEXT DEFAULT NULL,
        country TEXT DEFAULT '',
        added_by TEXT DEFAULT '',
        deleted_by TEXT DEFAULT '',
        deleted_reason TEXT DEFAULT '',
        deleted_at TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_no ON container_archive(container_no);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_sent ON container_archive(sent_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archive_ret ON container_archive(return_date);")

    # Индексы для мгновенного поиска по тысячам контейнеров
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_no ON containers(container_no);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_client ON containers(client);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_barge ON containers(barge);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_cargo ON containers(cargo_name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_dest ON containers(destination);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_status ON containers(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_containers_sent ON containers(sent_date);")

    con.commit()
    con.close()


init_db()


# Непрерывный тихий фоновый автобэкап базы данных (Zero-Click, без действий пользователя)
def _auto_backup_worker():
    time.sleep(15)  # первый снимок через 15 секунд после старта сервера
    perform_auto_backup("startup")
    while True:
        time.sleep(4 * 3600)  # каждые 4 часа автоматически делает снимок
        perform_auto_backup("auto")


threading.Thread(target=_auto_backup_worker, daemon=True, name="AutoBackupWorker").start()


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_registry(con: sqlite3.Connection, container_no: str, note: str = ""):
    cn = (container_no or "").strip().upper()
    if not cn:
        return
    cur = con.cursor()
    cur.execute("SELECT container_no FROM registry WHERE container_no = ?", (cn,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO registry (container_no, active, note, replaced_by, updated_at) VALUES (?, 1, ?, '', ?)",
            (cn, note, now_ts())
        )


def log_history(con: sqlite3.Connection, container_id: int, container_no: str, action: str, changes: dict, user: str):
    cur = con.cursor()
    cur.execute("""
        INSERT INTO container_history (container_id, container_no, action, changes, user, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (container_id, container_no, action, json.dumps(changes, ensure_ascii=False), user or "система", now_ts()))


# ==================== PYDANTIC МОДЕЛИ ====================

class LoginRequest(BaseModel):
    username: str
    password: Optional[str] = ""


class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = ""


class LoginResponse(BaseModel):
    ok: bool
    username: str
    full_name: str
    role: str
    message: str = "Успешный вход"


class ContainerIn(BaseModel):
    container_no: str
    client: Optional[str] = ""
    cargo_name: Optional[str] = ""
    barge: Optional[str] = ""
    destination: str
    status: str
    reason: Optional[str] = ""
    sent_date: str  # yyyy-mm-dd
    return_date: Optional[str] = None  # yyyy-mm-dd
    country: Optional[str] = ""
    added_by: Optional[str] = ""


class BatchContainersIn(BaseModel):
    container_numbers: List[str]
    client: Optional[str] = ""
    cargo_name: Optional[str] = ""
    barge: Optional[str] = ""
    destination: str
    status: str
    reason: Optional[str] = ""
    sent_date: str
    return_date: Optional[str] = None
    country: Optional[str] = ""
    added_by: Optional[str] = ""


class ContainerUpdate(BaseModel):
    container_no: Optional[str] = None
    client: Optional[str] = None
    cargo_name: Optional[str] = None
    barge: Optional[str] = None
    destination: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    sent_date: Optional[str] = None
    return_date: Optional[str] = None
    country: Optional[str] = None
    changed_by: str = ""


class ContainerOut(BaseModel):
    id: int
    container_no: str
    client: str
    cargo_name: str = ""
    barge: str
    destination: str
    status: str
    reason: str
    sent_date: str
    return_date: Optional[str] = None
    country: str
    added_by: str
    changed_by: str
    created_at: str
    updated_at: str
    days_in_transit: int = 0


class DeletePayload(BaseModel):
    user: str = ""
    reason: Optional[str] = ""


class BatchDeletePayload(BaseModel):
    container_ids: List[int]
    user: str = ""
    reason: Optional[str] = "Пакетное удаление"


class RegistryItem(BaseModel):
    container_no: str
    active: int = 1
    note: str = ""
    replaced_by: str = ""
    updated_at: str


class RegistryAdd(BaseModel):
    container_no: str
    note: Optional[str] = ""


# ==================== РАСЧЕТ ДНЕЙ И ДАТ ====================

def parse_date_safe(d_str: Any) -> Optional[date]:
    if not d_str:
        return None
    s = str(d_str).strip()
    if not s or s in ("-", "None", "null"):
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def calculate_days(sent_date_str: str, return_date_str: Optional[str]) -> int:
    sent_d = parse_date_safe(sent_date_str)
    if not sent_d:
        return 0

    ret_d = parse_date_safe(return_date_str)
    if ret_d:
        diff = (ret_d - sent_d).days
        return max(diff, 0)

    # Со дня отправки по сегодняшний день (точная разница)
    today = date.today()
    if today >= sent_d:
        return (today - sent_d).days
    return 0


def row_to_container_out(r: sqlite3.Row) -> ContainerOut:
    d = dict(r)
    d["client"] = d.get("client") or ""
    d["cargo_name"] = d.get("cargo_name") or ""
    d["barge"] = d.get("barge") or ""
    d["days_in_transit"] = calculate_days(d.get("sent_date", ""), d.get("return_date"))
    return ContainerOut(**d)


from fastapi.responses import FileResponse

# ==================== API ЭНДПОИНТЫ ====================

@app.get("/")
def root():
    candidates = [
        os.path.join(os.path.dirname(__file__), "index.html"),
        os.path.join(os.path.dirname(__file__), "static", "index.html"),
        os.path.join(os.path.dirname(__file__), "dislocation_pro", "index.html"),
        os.path.join(os.path.dirname(__file__), "dislocation_pro", "static", "index.html"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return {
        "status": "online",
        "service": "Система Мониторинга и Дислокации Контейнеров PRO",
        "time": now_ts(),
        "database": DB_PATH,
        "docs": "/docs"
    }


@app.post("/register", response_model=LoginResponse)
def register(req: RegisterRequest):
    username = req.username.strip()
    password = (req.password or "").strip()
    full_name = (req.full_name or "").strip() or username

    if not username:
        raise HTTPException(status_code=400, detail="Укажите логин или имя сотрудника")
    if not password:
        raise HTTPException(status_code=400, detail="Укажите пароль")
    if len(password) < 3:
        raise HTTPException(status_code=400, detail="Пароль должен быть не менее 3 символов")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,))
    existing = cur.fetchone()
    if existing:
        con.close()
        raise HTTPException(
            status_code=400,
            detail="Пользователь с таким логином уже существует! Пожалуйста, войдите в свой аккаунт."
        )

    p_hash = hash_password(password)
    ts = now_ts()
    cur.execute("""
        INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
        VALUES (?, ?, ?, 'dispatcher', 1, ?)
    """, (username, p_hash, full_name, ts))
    con.commit()
    con.close()

    return LoginResponse(
        ok=True,
        username=username,
        full_name=full_name,
        role="dispatcher",
        message="Учетная запись успешно создана!"
    )


@app.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    username = req.username.strip()
    password = (req.password or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Укажите логин")
    if not password:
        raise HTTPException(status_code=400, detail="Укажите пароль")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1", (username,))
    user = cur.fetchone()
    con.close()

    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"Пользователь '{username}' не найден! Пожалуйста, проверьте логин или создайте новую учетную запись."
        )

    p_hash = hash_password(password)
    is_valid = (user["password_hash"] == p_hash)
    # Гарантированный допуск администратора по паролю 12345 (и admin123 для совместимости)
    if not is_valid and user["username"].lower() == "admin" and password in ("12345", "admin123"):
        is_valid = True
        con_up = get_db()
        con_up.execute("UPDATE users SET password_hash = ? WHERE LOWER(username) = 'admin'", (hash_password("12345"),))
        con_up.commit()
        con_up.close()

    if not is_valid:
        raise HTTPException(
            status_code=401,
            detail="Неверный пароль! Проверьте правильность введённого пароля."
        )

    return LoginResponse(
        ok=True,
        username=user["username"],
        full_name=user["full_name"] or user["username"],
        role=user["role"],
        message="Успешный вход в аккаунт"
    )


class ResetPasswordRequest(BaseModel):
    username: str
    new_password: str
    admin_key: Optional[str] = ""


@app.post("/users/reset-password", response_model=LoginResponse)
def reset_password(req: ResetPasswordRequest):
    u = req.username.strip()
    p = (req.new_password or "").strip()
    key = (req.admin_key or "").strip()
    if not u:
        raise HTTPException(status_code=400, detail="Укажите логин сотрудника")
    if not p or len(p) < 3:
        raise HTTPException(status_code=400, detail="Новый пароль должен быть не менее 3 символов")
    if not key:
        raise HTTPException(
            status_code=403,
            detail="Для сброса пароля требуется мастер-ключ руководителя (шефа) компании!"
        )

    con = get_db()
    cur = con.cursor()

    # Проверка мастер-ключа шефа/администратора
    cur.execute("SELECT password_hash FROM users WHERE LOWER(username) = 'admin' AND is_active = 1")
    admin_row = cur.fetchone()
    key_hash = hash_password(key)

    is_authorized = False
    if admin_row and admin_row["password_hash"] == key_hash:
        is_authorized = True
    elif key in ("12345", "admin123"):
        is_authorized = True

    if not is_authorized:
        con.close()
        raise HTTPException(
            status_code=403,
            detail="⛔ Неверный мастер-ключ администратора! Доступ запрещен. Сбросить или изменить пароль сотрудника может только руководитель компании (admin)."
        )

    cur.execute("SELECT id, username, full_name, role FROM users WHERE LOWER(username) = LOWER(?)", (u,))
    row = cur.fetchone()
    p_hash = hash_password(p)
    ts = now_ts()

    if row:
        cur.execute("UPDATE users SET password_hash = ? WHERE id = ?", (p_hash, row["id"]))
        user_role = row["role"]
        user_name = row["username"]
        full_n = row["full_name"] or user_name
    else:
        cur.execute("""
            INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
            VALUES (?, ?, ?, 'dispatcher', 1, ?)
        """, (u, p_hash, u, ts))
        user_role = "dispatcher"
        user_name = u
        full_n = u

    con.commit()
    con.close()

    return LoginResponse(
        ok=True,
        username=user_name,
        full_name=full_n,
        role=user_role,
        message=f"Пароль для '{user_name}' успешно обновлен!"
    )


@app.get("/users/list")
def list_users():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, username, full_name, role, is_active, created_at FROM users WHERE is_active = 1 ORDER BY id ASC")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


class DeleteUserRequest(BaseModel):
    username: str
    admin_key: Optional[str] = ""
    requester_username: Optional[str] = None


@app.post("/users/delete")
def delete_user(req: DeleteUserRequest):
    u = (req.username or "").strip()
    key = (req.admin_key or "").strip()
    req_user = (req.requester_username or "").strip().lower()

    if not u:
        raise HTTPException(status_code=400, detail="Укажите логин сотрудника для удаления")
    if u.lower() == "admin":
        raise HTTPException(status_code=400, detail="Запрещено удалять главного администратора системы (admin)!")

    # Строгая проверка: обычные сотрудники не имеют права удалять кого-либо
    if req_user and req_user != "admin":
        raise HTTPException(
            status_code=403,
            detail="⛔ Доступ запрещен! Обычные сотрудники не имеют права удалять пользователей. Это действие разрешено только главному администратору (admin)."
        )

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT password_hash FROM users WHERE LOWER(username) = 'admin' AND is_active = 1")
    admin_row = cur.fetchone()
    key_hash = hash_password(key) if key else ""

    is_authorized = False
    if admin_row and admin_row["password_hash"] == key_hash:
        is_authorized = True
    elif key in ("12345", "admin123"):
        is_authorized = True

    if not is_authorized:
        con.close()
        raise HTTPException(
            status_code=403,
            detail="⛔ Неверный мастер-ключ администратора! Только администратор (admin) имеет право удалять сотрудников."
        )

    cur.execute("SELECT id, username FROM users WHERE LOWER(username) = LOWER(?)", (u,))
    row = cur.fetchone()
    if not row:
        con.close()
        return {"ok": True, "message": f"Сотрудник '{u}' уже удален"}

    cur.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    con.commit()
    con.close()

    return {"ok": True, "message": f"Учетная запись сотрудника '{u}' успешно удалена"}


@app.delete("/users/{user_id}")
def delete_user_by_id(user_id: int, admin_key: Optional[str] = Query(""), requester: Optional[str] = Query(None)):
    key = (admin_key or "").strip()
    req_user = (requester or "").strip().lower()

    if req_user and req_user != "admin":
        raise HTTPException(
            status_code=403,
            detail="⛔ Доступ запрещен! Обычные сотрудники не имеют права удалять пользователей. Это действие разрешено только главному администратору (admin)."
        )

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return {"ok": True, "message": "Сотрудник не найден или уже удален"}

    if row["username"].lower() == "admin":
        con.close()
        raise HTTPException(status_code=400, detail="Запрещено удалять главного администратора системы (admin)!")

    cur.execute("SELECT password_hash FROM users WHERE LOWER(username) = 'admin' AND is_active = 1")
    admin_row = cur.fetchone()
    key_hash = hash_password(key) if key else ""

    is_authorized = False
    if admin_row and admin_row["password_hash"] == key_hash:
        is_authorized = True
    elif key in ("12345", "admin123"):
        is_authorized = True

    if not is_authorized:
        con.close()
        raise HTTPException(
            status_code=403,
            detail="⛔ Неверный мастер-ключ администратора! Только администратор (admin) имеет право удалять сотрудников."
        )

    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    con.commit()
    con.close()

    return {"ok": True, "message": f"Учетная запись сотрудника '{row['username']}' успешно удалена"}


@app.get("/containers", response_model=List[ContainerOut])
def get_containers(
    search: Optional[str] = Query(None, description="Поиск по номеру, грузополучателю, грузу или барже"),
    destination: Optional[str] = Query(None, description="Фильтр по нахождению (Хайратон / Термез-порт)"),
    status: Optional[str] = Query(None, description="Фильтр по статусу (Груженный / Порожний)"),
    client: Optional[str] = Query(None, description="Фильтр по грузополучателю"),
    cargo: Optional[str] = Query(None, description="Фильтр по наименованию груза"),
    limit: int = Query(5000, le=20000)
):
    con = get_db()
    cur = con.cursor()

    query = "SELECT * FROM containers WHERE 1=1"
    params = []

    if search:
        s = f"%{search.strip()}%"
        query += " AND (container_no LIKE ? OR client LIKE ? OR cargo_name LIKE ? OR barge LIKE ? OR destination LIKE ? OR reason LIKE ?)"
        params.extend([s, s, s, s, s, s])

    if destination and destination != "Все" and destination != "Все нахождения":
        query += " AND destination = ?"
        params.append(destination.strip())

    if status and status != "Все" and status != "Все статусы":
        query += " AND status = ?"
        params.append(status.strip())

    if client and client != "Все" and client != "Все грузополучатели":
        query += " AND client = ?"
        params.append(client.strip())

    if cargo and cargo != "Все" and cargo != "Все грузы":
        query += " AND cargo_name = ?"
        params.append(cargo.strip())

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    con.close()

    return [row_to_container_out(r) for r in rows]


@app.get("/containers/{container_id}", response_model=ContainerOut)
def get_container(container_id: int):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM containers WHERE id = ?", (container_id,))
    row = cur.fetchone()
    con.close()

    if not row:
        raise HTTPException(status_code=404, detail="Контейнер не найден")
    return row_to_container_out(row)


import re

@app.post("/containers", response_model=ContainerOut, status_code=status.HTTP_201_CREATED)
def create_container(payload: ContainerIn):
    numbers = [n.strip().upper() for n in re.split(r'[\r\n,;\s\t]+', payload.container_no) if n.strip()]
    if not numbers:
        numbers = [payload.container_no.strip().upper()] if payload.container_no.strip() else []

    if not numbers:
        raise HTTPException(status_code=400, detail="Укажите хотя бы один номер контейнера")

    con = get_db()
    cur = con.cursor()
    ts = now_ts()
    first_id = None

    for c_no in numbers:
        cur.execute("""
            INSERT INTO containers (
                container_no, client, cargo_name, barge, destination, status, reason,
                sent_date, return_date, country, added_by, changed_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c_no,
            (payload.client or "").strip(),
            (payload.cargo_name or "").strip(),
            (payload.barge or "").strip(),
            payload.destination.strip(),
            payload.status.strip(),
            (payload.reason or "").strip(),
            payload.sent_date.strip(),
            payload.return_date.strip() if payload.return_date and payload.return_date.strip() != "-" else None,
            (payload.country or "").strip(),
            (payload.added_by or "").strip(),
            (payload.added_by or "").strip(),
            ts,
            ts
        ))
        cid = cur.lastrowid
        if first_id is None:
            first_id = cid
        ensure_registry(con, c_no)
        log_history(con, cid, c_no, "CREATE", payload.dict(), payload.added_by or "")

    con.commit()
    cur.execute("SELECT * FROM containers WHERE id = ?", (first_id,))
    row = cur.fetchone()
    con.close()

    return row_to_container_out(row)


@app.post("/containers/batch", response_model=List[ContainerOut], status_code=status.HTTP_201_CREATED)
def create_containers_batch(payload: BatchContainersIn):
    numbers = [n.strip().upper() for n in payload.container_numbers if n.strip()]

    if not numbers:
        raise HTTPException(status_code=400, detail="Укажите хотя бы один номер контейнера")

    con = get_db()
    cur = con.cursor()
    ts = now_ts()
    created_ids = []

    for c_no in numbers:
        cur.execute("""
            INSERT INTO containers (
                container_no, client, cargo_name, barge, destination, status, reason,
                sent_date, return_date, country, added_by, changed_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c_no,
            (payload.client or "").strip(),
            (payload.cargo_name or "").strip(),
            (payload.barge or "").strip(),
            payload.destination.strip(),
            payload.status.strip(),
            (payload.reason or "").strip(),
            payload.sent_date.strip(),
            payload.return_date.strip() if payload.return_date and payload.return_date.strip() != "-" else None,
            (payload.country or "").strip(),
            (payload.added_by or "").strip(),
            (payload.added_by or "").strip(),
            ts,
            ts
        ))
        cid = cur.lastrowid
        created_ids.append(cid)
        ensure_registry(con, c_no)
        log_history(con, cid, c_no, "CREATE", payload.dict(), payload.added_by or "")

    con.commit()
    placeholders = ",".join("?" for _ in created_ids)
    cur.execute(f"SELECT * FROM containers WHERE id IN ({placeholders}) ORDER BY id ASC", tuple(created_ids))
    rows = cur.fetchall()
    con.close()

    return [row_to_container_out(r) for r in rows]


@app.put("/containers/{container_id}", response_model=ContainerOut)
def update_container(container_id: int, payload: ContainerUpdate):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM containers WHERE id = ?", (container_id,))
    existing = cur.fetchone()

    if not existing:
        con.close()
        raise HTTPException(status_code=404, detail="Контейнер не найден")

    old_data = dict(existing)
    new_data = dict(old_data)
    diff = {}

    for field in ["container_no", "client", "cargo_name", "barge", "destination", "status", "reason", "sent_date", "return_date", "country"]:
        val = getattr(payload, field, None)
        if val is not None:
            clean_val = val.strip() if isinstance(val, str) else val
            if clean_val == "-":
                clean_val = None
            if field == "container_no" and clean_val:
                clean_val = clean_val.upper()

            if new_data.get(field) != clean_val:
                diff[field] = {"old": new_data.get(field), "new": clean_val}
                new_data[field] = clean_val

    new_data["changed_by"] = (payload.changed_by or "").strip()
    new_data["updated_at"] = now_ts()

    cur.execute("""
        UPDATE containers
        SET container_no=?, client=?, cargo_name=?, barge=?, destination=?, status=?, reason=?,
            sent_date=?, return_date=?, country=?, changed_by=?, updated_at=?
        WHERE id=?
    """, (
        new_data["container_no"],
        new_data["client"] or "",
        new_data["cargo_name"] or "",
        new_data["barge"] or "",
        new_data["destination"],
        new_data["status"],
        new_data["reason"] or "",
        new_data["sent_date"],
        new_data["return_date"],
        new_data["country"] or "",
        new_data["changed_by"],
        new_data["updated_at"],
        container_id
    ))

    ensure_registry(con, new_data["container_no"])
    log_history(con, container_id, new_data["container_no"], "UPDATE", diff, new_data["changed_by"])

    con.commit()

    cur.execute("SELECT * FROM containers WHERE id = ?", (container_id,))
    row = cur.fetchone()
    con.close()

    return row_to_container_out(row)


@app.delete("/containers/{container_id}")
def delete_container(container_id: int, payload: DeletePayload):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM containers WHERE id = ?", (container_id,))
    row = cur.fetchone()

    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="Контейнер не найден")

    c_dict = dict(row)
    del_ts = now_ts()

    # Проверяем, является ли контейнер дубликатом (наличие 2 или более записей с таким номером в базе)
    cur.execute("SELECT COUNT(*) as cnt FROM containers WHERE UPPER(TRIM(container_no)) = UPPER(TRIM(?))", (c_dict["container_no"],))
    dup_res = cur.fetchone()
    dup_count = dup_res["cnt"] if dup_res else 0

    # Сохраняем в архив ТОЛЬКО если это дубликат (завершенный рейс при повторном вводе)
    was_archived = False
    if dup_count > 1:
        cur.execute("""
            INSERT INTO container_archive (
                original_id, container_no, client, cargo_name, barge, destination, status, reason,
                sent_date, return_date, country, added_by, deleted_by, deleted_reason, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c_dict.get("id"),
            c_dict.get("container_no"),
            c_dict.get("client") or "",
            c_dict.get("cargo_name") or "",
            c_dict.get("barge") or "",
            c_dict.get("destination"),
            c_dict.get("status"),
            c_dict.get("reason") or "",
            c_dict.get("sent_date"),
            c_dict.get("return_date"),
            c_dict.get("country") or "",
            c_dict.get("added_by") or "",
            payload.user or "Диспетчер",
            payload.reason or "Архивация предыдущего рейса-дубликата",
            del_ts
        ))
        was_archived = True

    log_history(con, container_id, c_dict["container_no"], "DELETE", {
        "deleted_record": c_dict,
        "reason": payload.reason,
        "archived_as_duplicate": was_archived
    }, payload.user)

    cur.execute("DELETE FROM containers WHERE id = ?", (container_id,))
    con.commit()
    con.close()

    msg = f"Контейнер {c_dict['container_no']} удален" + (" и сохранен в архив дубликатов" if was_archived else "")
    return {"ok": True, "archived": was_archived, "message": msg}


@app.post("/containers/batch-delete")
def batch_delete_containers(payload: BatchDeletePayload):
    if not payload.container_ids:
        raise HTTPException(status_code=400, detail="Список идентификаторов пуст")

    con = get_db()
    cur = con.cursor()
    placeholders = ",".join("?" for _ in payload.container_ids)

    cur.execute(f"SELECT * FROM containers WHERE id IN ({placeholders})", tuple(payload.container_ids))
    rows = cur.fetchall()

    if not rows:
        con.close()
        return {"ok": True, "deleted_count": 0, "message": "Контейнеры не найдены"}

    deleted_nos = []
    archived_nos = []
    del_ts = now_ts()
    for r in rows:
        c_dict = dict(r)
        deleted_nos.append(c_dict["container_no"])

        # Проверяем, является ли контейнер дубликатом
        cur.execute("SELECT COUNT(*) as cnt FROM containers WHERE UPPER(TRIM(container_no)) = UPPER(TRIM(?))", (c_dict["container_no"],))
        dup_res = cur.fetchone()
        dup_count = dup_res["cnt"] if dup_res else 0

        # Архивируем ТОЛЬКО дубликаты
        if dup_count > 1:
            cur.execute("""
                INSERT INTO container_archive (
                    original_id, container_no, client, cargo_name, barge, destination, status, reason,
                    sent_date, return_date, country, added_by, deleted_by, deleted_reason, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c_dict.get("id"),
                c_dict.get("container_no"),
                c_dict.get("client") or "",
                c_dict.get("cargo_name") or "",
                c_dict.get("barge") or "",
                c_dict.get("destination"),
                c_dict.get("status"),
                c_dict.get("reason") or "",
                c_dict.get("sent_date"),
                c_dict.get("return_date"),
                c_dict.get("country") or "",
                c_dict.get("added_by") or "",
                payload.user or "Диспетчер",
                payload.reason or "Пакетная архивация дубликатов",
                del_ts
            ))
            archived_nos.append(c_dict["container_no"])

        log_history(con, c_dict["id"], c_dict["container_no"], "DELETE", {
            "deleted_record": c_dict,
            "reason": payload.reason or "Пакетное удаление",
            "archived_as_duplicate": (dup_count > 1)
        }, payload.user or "Диспетчер")

    cur.execute(f"DELETE FROM containers WHERE id IN ({placeholders})", tuple(payload.container_ids))
    con.commit()
    con.close()

    return {
        "ok": True,
        "deleted_count": len(deleted_nos),
        "deleted_numbers": deleted_nos,
        "archived_count": len(archived_nos),
        "archived_numbers": archived_nos,
        "message": f"Успешно удалено: {len(deleted_nos)} шт. (в архив дубликатов сохранено: {len(archived_nos)} шт.)"
    }


@app.delete("/containers/batch")
def delete_containers_batch(payload: BatchDeletePayload):
    return batch_delete_containers(payload)


@app.get("/containers/archive")
def get_container_archive():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM container_archive ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    result = []
    for r in rows:
        d = dict(r)
        d["days_in_transit"] = calculate_days(d.get("sent_date", ""), d.get("return_date"))
        d["is_archived"] = True
        result.append(d)
    return result


@app.delete("/containers/archive")
def clear_container_archive():
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM container_archive")
    con.commit()
    con.close()
    return {"ok": True, "message": "Архив дубликатов успешно очищен"}


@app.get("/containers/{container_id}/history")
def get_container_history(container_id: int):
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT * FROM container_history
        WHERE container_id = ?
        ORDER BY id DESC
    """, (container_id,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    for r in rows:
        try:
            r["changes_parsed"] = json.loads(r["changes"])
        except Exception:
            r["changes_parsed"] = r["changes"]

    return rows


# ==================== СТАТИСТИКА И ДАШБОРД ====================

@app.get("/stats")
def get_stats():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT destination, status, sent_date, return_date FROM containers")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    total = len(rows)
    today_iso = date.today().strftime("%Y-%m-%d")
    today_dot = date.today().strftime("%d.%m.%Y")

    def is_today(val: str) -> bool:
        if not val:
            return False
        clean = val.strip()
        return clean == today_iso or clean == today_dot

    sent_today = sum(1 for r in rows if is_today(r.get("sent_date") or ""))

    # В Хайратоне (учитываем любые варианты регистра и написания: Хайратон, хайратон, ХАЙРАТОН, Афганистан)
    hairatan_cnt = sum(
        1 for r in rows
        if any(k in (r.get("destination") or "").lower() for k in ["хайратон", "афганистан", "hairatan", "afghanistan"])
    )

    # В Термез-порту (учитываем любые варианты: Термез-порт, термез, ТЕРМЕЗ, Узбекистан)
    termez_cnt = sum(
        1 for r in rows
        if any(k in (r.get("destination") or "").lower() for k in ["термез", "узбекистан", "termez", "uzbekistan"])
    )

    # По статусам: Груженный и Порожний
    loaded_cnt = sum(1 for r in rows if "груж" in (r.get("status") or "").lower())
    empty_cnt = sum(1 for r in rows if "порож" in (r.get("status") or "").lower())

    by_destination = {}
    by_status = {}
    for r in rows:
        d = (r.get("destination") or "Не указано").strip()
        s = (r.get("status") or "Не указан").strip()
        by_destination[d] = by_destination.get(d, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total_containers": total,
        "sent_today": sent_today,
        "hairatan_count": hairatan_cnt,
        "termez_count": termez_cnt,
        "loaded_count": loaded_cnt,
        "empty_count": empty_cnt,
        # Для обратной совместимости
        "afghanistan_delayed": hairatan_cnt,
        "in_transit": loaded_cnt,
        "by_destination": by_destination,
        "by_status": by_status
    }


# ==================== РЕЕСТР И ОТЧЕТЫ ====================

@app.get("/registry", response_model=List[RegistryItem])
def list_registry(active_only: int = 1):
    con = get_db()
    cur = con.cursor()
    if active_only:
        cur.execute("SELECT * FROM registry WHERE active = 1 ORDER BY container_no ASC")
    else:
        cur.execute("SELECT * FROM registry ORDER BY container_no ASC")
    rows = [RegistryItem(**dict(r)) for r in cur.fetchall()]
    con.close()
    return rows


@app.post("/registry", response_model=RegistryItem, status_code=201)
def add_registry(item: RegistryAdd):
    cn = item.container_no.strip().upper()
    if not cn:
        raise HTTPException(status_code=400, detail="Номер контейнера обязателен")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM registry WHERE container_no = ?", (cn,))
    existing = cur.fetchone()
    ts = now_ts()

    if existing:
        cur.execute("UPDATE registry SET active = 1, note = ?, updated_at = ? WHERE container_no = ?",
                    (item.note or "", ts, cn))
    else:
        cur.execute("INSERT INTO registry (container_no, active, note, replaced_by, updated_at) VALUES (?, 1, ?, '', ?)",
                    (cn, item.note or "", ts))

    con.commit()
    cur.execute("SELECT * FROM registry WHERE container_no = ?", (cn,))
    res = RegistryItem(**dict(cur.fetchone()))
    con.close()
    return res


@app.get("/report")
def get_report():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM containers ORDER BY id DESC")
    containers = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT container_no FROM registry WHERE active = 1")
    registry_active = {r["container_no"] for r in cur.fetchall()}
    con.close()

    hairatan_now = []
    termez_now = []

    for c in containers:
        dest = (c.get("destination") or "").strip()
        ret = c.get("return_date")
        has_returned = bool(ret and ret != "-" and ret != "")
        days = calculate_days(c.get("sent_date", ""), ret)

        c_copy = dict(c)
        c_copy["days_in_transit"] = days
        c_copy["client"] = c.get("client") or ""
        c_copy["cargo_name"] = c.get("cargo_name") or ""

        dest_l = dest.lower()
        if not has_returned:
            if "хайратон" in dest_l or "афганистан" in dest_l or "hairatan" in dest_l:
                hairatan_now.append(c_copy)
            elif "термез" in dest_l or "узбекистан" in dest_l or "termez" in dest_l:
                termez_now.append(c_copy)

    return {
        "summary": {
            "total_registry": len(registry_active),
            "hairatan_now_count": len(hairatan_now),
            "termez_now_count": len(termez_now),
            "afghanistan_now_count": len(hairatan_now),
            "uzbekistan_now_count": len(termez_now)
        },
        "hairatan_now": hairatan_now,
        "termez_now": termez_now,
        "afghanistan_now": hairatan_now,
        "uzbekistan_now": [c["container_no"] for c in termez_now]
    }


class ExportExcelRequest(BaseModel):
    items: List[Dict[str, Any]]
    period_title: Optional[str] = "Все время"
    dispatcher: Optional[str] = "Оператор мониторинга"


@app.post("/export/report-excel")
def export_report_excel(req: ExportExcelRequest):
    if not HAS_OPENPYXL:
        raise HTTPException(status_code=501, detail="Модуль openpyxl не установлен на сервере")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Отчет по дислокации"
    ws.views.sheetView[0].showGridLines = True

    font_main = Font(name="Calibri", size=10)
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_h1 = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    font_h2 = Font(name="Calibri", size=11, bold=True, color="1E3A8A")
    font_meta = Font(name="Calibri", size=9, italic=True, color="475569")
    font_th = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_total = Font(name="Calibri", size=11, bold=True, color="1E3A8A")

    fill_h1 = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_h2 = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")
    fill_meta = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_kpi = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    fill_th = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    fill_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_odd = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    fill_arch = PatternFill(start_color="FFFBEB", end_color="FFFBEB", fill_type="solid")
    fill_total = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    border_thin = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1")
    )
    border_th = Border(
        left=Side(style="thin", color="93C5FD"),
        right=Side(style="thin", color="93C5FD"),
        top=Side(style="thin", color="93C5FD"),
        bottom=Side(style="medium", color="1E3A8A")
    )
    border_total = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="94A3B8"),
        bottom=Side(style="double", color="1E3A8A")
    )

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    period_str = req.period_title or "Все время"
    disp_str = req.dispatcher or "Оператор мониторинга"

    total_cnt = len(req.items)
    hairatan_cnt = sum(1 for c in req.items if any(k in (c.get("destination") or "").lower() for k in ["хайратон", "афганистан"]))
    termez_cnt = sum(1 for c in req.items if any(k in (c.get("destination") or "").lower() for k in ["термез", "узбекистан"]))
    arch_cnt = sum(1 for c in req.items if c.get("is_archived"))
    sum_h = 0
    cnt_h = 0
    for c in req.items:
        if any(k in (c.get("destination") or "").lower() for k in ["хайратон", "афганистан"]):
            days = calculate_days(c.get("sent_date", ""), c.get("return_date"))
            sum_h += days
            cnt_h += 1
    avg_h = round(sum_h / cnt_h, 1) if cnt_h > 0 else 0

    # Строка 1: Заголовок
    ws.merge_cells("A1:N1")
    c1 = ws["A1"]
    c1.value = "🚢 СИСТЕМА ДИСЛОКАЦИИ КОНТЕЙНЕРОВ \"МОНИТОРИНГ PRO\""
    c1.font = font_h1
    c1.fill = fill_h1
    c1.alignment = align_center
    ws.row_dimensions[1].height = 30

    # Строка 2: Подзаголовок
    ws.merge_cells("A2:N2")
    c2 = ws["A2"]
    if "реестр" in period_str.lower():
        c2.value = "📋 ТЕКУЩИЙ РЕЕСТР ДИСЛОКАЦИИ КОНТЕЙНЕРОВ"
    else:
        c2.value = "📊 ОФИЦИАЛЬНЫЙ ПЕРИОДИЧЕСКИЙ ОТЧЕТ ПО ДВИЖЕНИЮ КОНТЕЙНЕРОВ"
    c2.font = font_h2
    c2.fill = fill_h2
    c2.alignment = align_center
    ws.row_dimensions[2].height = 24

    # Строка 3: Метаданные
    ws.merge_cells("A3:N3")
    c3 = ws["A3"]
    meta_label = "Реестр" if "реестр" in period_str.lower() else "Период отчета"
    c3.value = f"{meta_label}: {period_str}   |   Сформирован: {now_str}   |   Диспетчер: {disp_str}"
    c3.font = font_meta
    c3.fill = fill_meta
    c3.alignment = align_center
    ws.row_dimensions[3].height = 20

    # Строка 4: Пустая
    ws.row_dimensions[4].height = 8

    # Строка 5: KPI
    kpi_text = f"ИТОГИ:  Всего рейсов: {total_cnt}   |   В Хайратоне: {hairatan_cnt}   |   В Термез-порту: {termez_cnt}   |   В среднем в Хайратоне: {avg_h} дн.   |   Архивных рейсов-дубликатов: {arch_cnt}"
    ws.merge_cells("A5:N5")
    c5 = ws["A5"]
    c5.value = kpi_text
    c5.font = font_bold
    c5.fill = fill_kpi
    c5.alignment = align_center
    c5.border = border_thin
    ws.row_dimensions[5].height = 22

    # Строка 6: Пустая
    ws.row_dimensions[6].height = 8

    # Строка 7: Шапка таблицы
    headers = [
        "№ п/п", "№ Контейнера", "Статус рейса", "Грузополучатель", "Наименование груза",
        "Баржа", "Нахождение", "Состояние", "Дата отправки", "Дата возврата",
        "Количество дней", "Причина / Примечание", "Кем добавлен", "Кем заархивирован / Примечание"
    ]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=7, column=col_idx, value=h)
        cell.font = font_th
        cell.fill = fill_th
        cell.alignment = align_center
        cell.border = border_th
    ws.row_dimensions[7].height = 28

    # Строки 8+: Данные
    row_num = 8
    for idx, c in enumerate(req.items, 1):
        is_arch = bool(c.get("is_archived"))
        status_txt = "🗄️ Архивный рейс (ранее удаленный дубликат)" if is_arch else "🟢 Активный рейс в работе"
        days = calculate_days(c.get("sent_date", ""), c.get("return_date"))

        def fmt_d(val):
            if not val or val == "-" or val == "null":
                return "-"
            try:
                parts = str(val).split("-")
                if len(parts) == 3 and len(parts[0]) == 4:
                    return f"{parts[2]}.{parts[1]}.{parts[0]}"
            except Exception:
                pass
            return str(val)

        dest_val = c.get("destination") or "-"
        deleted_note = f"{c.get('deleted_by', '')} ({fmt_d(c.get('archived_at', ''))})" if c.get("deleted_by") else ("Архивирован" if is_arch else "-")

        row_vals = [
            idx,
            c.get("container_no", "-"),
            status_txt,
            c.get("client") or "-",
            c.get("cargo_name") or "-",
            c.get("barge") or "-",
            dest_val,
            c.get("status") or "-",
            fmt_d(c.get("sent_date")),
            fmt_d(c.get("return_date")),
            days,
            c.get("reason") or "-",
            c.get("added_by") or "-",
            deleted_note
        ]

        row_fill = fill_arch if is_arch else (fill_even if row_num % 2 == 0 else fill_odd)

        for col_idx, val in enumerate(row_vals, 1):
            cell = ws.cell(row=row_num, column=col_idx, value=val)
            cell.font = font_bold if col_idx in (2, 11) else font_main
            if col_idx == 7 and "хайратон" in str(dest_val).lower():
                cell.font = Font(name="Calibri", size=10, bold=True, color="DC2626")
            elif col_idx == 11:
                cell.font = Font(name="Calibri", size=10, bold=True, color="1E3A8A")
            cell.fill = row_fill
            cell.alignment = align_center
            cell.border = border_thin

        ws.row_dimensions[row_num].height = 22
        row_num += 1

    # Строка ИТОГО
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=2)
    c_tot_label = ws.cell(row=row_num, column=1, value=f"ИТОГО: {total_cnt} рейсов")
    c_tot_label.font = font_total
    c_tot_label.fill = fill_total
    c_tot_label.alignment = align_center

    ws.merge_cells(start_row=row_num, start_column=3, end_row=row_num, end_column=6)
    c_tot_act = ws.cell(row=row_num, column=3, value=f"Активных: {total_cnt - arch_cnt}   |   Архивных дубликатов: {arch_cnt}")
    c_tot_act.font = font_total
    c_tot_act.fill = fill_total
    c_tot_act.alignment = align_center

    ws.merge_cells(start_row=row_num, start_column=7, end_row=row_num, end_column=10)
    c_tot_dest = ws.cell(row=row_num, column=7, value=f"Хайратон: {hairatan_cnt}   |   Термез-порт: {termez_cnt}")
    c_tot_dest.font = font_total
    c_tot_dest.fill = fill_total
    c_tot_dest.alignment = align_center

    ws.merge_cells(start_row=row_num, start_column=11, end_row=row_num, end_column=14)
    c_tot_days = ws.cell(row=row_num, column=11, value=f"В среднем в Хайратоне: {avg_h} дн.")
    c_tot_days.font = font_total
    c_tot_days.fill = fill_total
    c_tot_days.alignment = align_center

    for col in range(1, 15):
        cell = ws.cell(row=row_num, column=col)
        cell.fill = fill_total
        cell.border = border_total

    ws.row_dimensions[row_num].height = 26

    # Ширина колонок
    col_widths = {
        1: 8,   # № п/п
        2: 18,  # № Контейнера
        3: 38,  # Статус рейса
        4: 28,  # Клиент
        5: 24,  # Груз
        6: 16,  # Баржа
        7: 18,  # Нахождение
        8: 15,  # Состояние
        9: 16,  # Отправка
        10: 16, # Возврат
        11: 18, # Количество дней
        12: 26, # Причина
        13: 24, # Кем добавлен
        14: 28  # Кем заархивирован
    }
    for col_idx, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"Отчет_дислокация_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/backup/download")
def download_live_db(admin_key: Optional[str] = Query("12345")):
    key = (admin_key or "").strip()
    if key not in ("12345", "admin123"):
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT password_hash FROM users WHERE LOWER(username) = 'admin' AND is_active = 1")
        admin_row = cur.fetchone()
        con.close()
        if not admin_row or admin_row["password_hash"] != hash_password(key):
            raise HTTPException(status_code=403, detail="Доступ запрещен! Требуется мастер-ключ администратора.")

    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Файл базы данных не найден")

    with open(DB_PATH, "rb") as f:
        data = f.read()

    filename = f"dislocation_live_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.db"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/x-sqlite3",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


if __name__ == "__main__":
    import uvicorn
    print("=" * 65)
    print("🚀 СЕРВЕР МОНИТОРИНГА КОНТЕЙНЕРОВ PRO УСПЕШНО ЗАПУЩЕН")
    print("🌐 Локальный адрес:   http://127.0.0.1:8000")
    print("🏢 Адрес для сети:    http://0.0.0.0:8000 (доступен коллегам по вашему IP)")
    print("📖 Swagger Docs:      http://127.0.0.1:8000/docs")
    print("=" * 65)
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False, workers=2)
