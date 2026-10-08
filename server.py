from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date
import sqlite3
import hashlib
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "dislocation.db")

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

    # Создание базовых пользователей при первой инициализации
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    if cur.fetchone()["cnt"] == 0:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
        INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
        VALUES 
            ('admin', ?, 'Главный диспетчер (Админ)', 'admin', 1, ?),
            ('operator', ?, 'Оператор мониторинга', 'dispatcher', 1, ?)
        """, (hash_password("admin123"), ts, hash_password("12345"), ts))

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
        os.path.join(os.path.dirname(__file__), "static", "index.html"),
        os.path.join(os.path.dirname(__file__), "index.html"),
        os.path.join(os.path.dirname(__file__), "dislocation_pro", "static", "index.html"),
        os.path.join(os.path.dirname(__file__), "dislocation_pro", "index.html"),
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
    # Строгая проверка пароля
    if user["password_hash"] != p_hash:
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

    today_str = date.today().strftime("%Y-%m-%d")

    # Всего записей
    cur.execute("SELECT COUNT(*) as total FROM containers")
    total = cur.fetchone()["total"]

    # Отправлено сегодня
    cur.execute("SELECT COUNT(*) as cnt FROM containers WHERE sent_date = ?", (today_str,))
    sent_today = cur.fetchone()["cnt"]

    # В Хайратоне
    cur.execute("""
        SELECT COUNT(*) as cnt FROM containers
        WHERE LOWER(destination) LIKE '%хайратон%' OR LOWER(destination) LIKE '%афганистан%'
    """)
    hairatan_cnt = cur.fetchone()["cnt"]

    # В Термез-порту
    cur.execute("""
        SELECT COUNT(*) as cnt FROM containers
        WHERE LOWER(destination) LIKE '%термез%' OR LOWER(destination) LIKE '%узбекистан%'
    """)
    termez_cnt = cur.fetchone()["cnt"]

    # По статусам: Груженный и Порожний
    cur.execute("SELECT COUNT(*) as cnt FROM containers WHERE status = 'Груженный'")
    loaded_cnt = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) as cnt FROM containers WHERE status = 'Порожний'")
    empty_cnt = cur.fetchone()["cnt"]

    # По нахождениям
    cur.execute("""
        SELECT destination, COUNT(*) as cnt
        FROM containers
        GROUP BY destination
        ORDER BY cnt DESC
    """)
    by_destination = {r["destination"]: r["cnt"] for r in cur.fetchall()}

    # По всем статусам
    cur.execute("""
        SELECT status, COUNT(*) as cnt
        FROM containers
        GROUP BY status
    """)
    by_status = {r["status"]: r["cnt"] for r in cur.fetchall()}

    con.close()

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

        if not has_returned:
            if dest in ("Хайратон", "Афганистан"):
                hairatan_now.append(c_copy)
            elif dest in ("Термез-порт", "Узбекистан"):
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


if __name__ == "__main__":
    import uvicorn
    print("=" * 65)
    print("🚀 СЕРВЕР МОНИТОРИНГА КОНТЕЙНЕРОВ PRO УСПЕШНО ЗАПУЩЕН")
    print("🌐 Локальный адрес:   http://127.0.0.1:8000")
    print("🏢 Адрес для сети:    http://0.0.0.0:8000 (доступен коллегам по вашему IP)")
    print("📖 Swagger Docs:      http://127.0.0.1:8000/docs")
    print("=" * 65)
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False, workers=2)
