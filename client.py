import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import datetime, date
import json
import os
import requests

# Пытаемся импортировать опциональные библиотеки
try:
    from tkcalendar import DateEntry
    HAS_TKCALENDAR = True
except ImportError:
    HAS_TKCALENDAR = False

try:
    import pandas as pd
    import openpyxl
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False

try:
    import plotly.express as px
    import webbrowser
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


# ================== КОНФИГУРАЦИЯ И СЕТЬ ==================
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "client_config.json")

DEFAULT_CONFIG = {
    "server_url": "http://127.0.0.1:8000",
    "last_username": "operator",
    "auto_refresh_sec": 30
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


app_config = load_config()
BASE_URL = app_config["server_url"].rstrip("/")
HTTP_TIMEOUT = 10
current_user = None
user_role = "dispatcher"
auto_refresh_job = None

# Справочники для выпадающих списков (строго по 2 варианта согласно требованиям)
DESTINATIONS = [
    "Хайратон",
    "Термез-порт"
]

STATUSES = [
    "Груженный",
    "Порожний"
]

BARGES = [
    "Zarafshon",
    "Surxon",
    "O'zbekiston",
    "Samarkand",
    "Jizzax",
    "Toshkent"
]


# ================== ЗАПОМИНАНИЕ ГРУЗОПОЛУЧАТЕЛЕЙ И ГРУЗОВ (С ОЧИСТКОЙ В ЛЮБОЕ ВРЕМЯ) ==================
def get_remembered_clients():
    history = app_config.get("clients_history", [])
    from_containers = [c.get("client", "").strip() for c in _raw_containers if c.get("client")]
    hidden = set(app_config.get("clients_hidden", []))
    combined = sorted(list((set(history + from_containers) - hidden)))
    return combined if combined else ["OOO 'Global Trade'", "Asia Trans Logistics", "Silk Road Express"]


def remember_client(name):
    if not name or not name.strip():
        return
    clean = name.strip()
    hidden = set(app_config.get("clients_hidden", []))
    if clean in hidden:
        hidden.remove(clean)
        app_config["clients_hidden"] = list(hidden)
    history = app_config.get("clients_history", [])
    if clean not in history:
        history.append(clean)
        app_config["clients_history"] = history
    save_config(app_config)


def remove_remembered_client(name):
    clean = (name or "").strip()
    if not clean:
        return
    hidden = set(app_config.get("clients_hidden", []))
    hidden.add(clean)
    app_config["clients_hidden"] = list(hidden)
    history = [x for x in app_config.get("clients_history", []) if x != clean]
    app_config["clients_history"] = history
    save_config(app_config)


def clear_all_remembered_clients():
    all_current = get_remembered_clients()
    hidden = set(app_config.get("clients_hidden", []))
    hidden.update(all_current)
    app_config["clients_hidden"] = list(hidden)
    app_config["clients_history"] = []
    save_config(app_config)


def get_remembered_cargos():
    history = app_config.get("cargos_history", [])
    from_containers = [c.get("cargo_name", "").strip() for c in _raw_containers if c.get("cargo_name")]
    hidden = set(app_config.get("cargos_hidden", []))
    combined = sorted(list((set(history + from_containers) - hidden)))
    return combined if combined else ["Мука пшеничная 1 сорт", "Сахар-песок", "Строительные материалы"]


def remember_cargo(name):
    if not name or not name.strip():
        return
    clean = name.strip()
    hidden = set(app_config.get("cargos_hidden", []))
    if clean in hidden:
        hidden.remove(clean)
        app_config["cargos_hidden"] = list(hidden)
    history = app_config.get("cargos_history", [])
    if clean not in history:
        history.append(clean)
        app_config["cargos_history"] = history
    save_config(app_config)


def remove_remembered_cargo(name):
    clean = (name or "").strip()
    if not clean:
        return
    hidden = set(app_config.get("cargos_hidden", []))
    hidden.add(clean)
    app_config["cargos_hidden"] = list(hidden)
    history = [x for x in app_config.get("cargos_history", []) if x != clean]
    app_config["cargos_history"] = history
    save_config(app_config)


def clear_all_remembered_cargos():
    all_current = get_remembered_cargos()
    hidden = set(app_config.get("cargos_hidden", []))
    hidden.update(all_current)
    app_config["cargos_hidden"] = list(hidden)
    app_config["cargos_history"] = []
    save_config(app_config)


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def parse_date(val):
    if not val or val == "-":
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except Exception:
            pass
    return None


def calculate_days_local(sent_str, return_str):
    s = parse_date(sent_str)
    if not s:
        return 0
    r = parse_date(return_str)
    if r:
        diff = (r - s).days
        return max(diff + 1, 1) if diff >= 0 else 0
    today = date.today()
    if today >= s:
        return (today - s).days + 1
    return 0


def format_date_ru(val):
    d = parse_date(val)
    if not d:
        return "-"
    return d.strftime("%d.%m.%Y")


# ================== ОКНО НАСТРОЕК СЕРВЕРА ==================
def open_settings_popup():
    global BASE_URL
    win = tk.Toplevel(main_window)
    win.title("⚙️ Настройка подключения к серверу")
    win.geometry("460x220")
    win.resizable(False, False)
    win.grab_set()

    tk.Label(
        win,
        text="Укажите адрес сервера в локальной сети компании:",
        font=("Segoe UI", 10, "bold")
    ).pack(anchor="w", padx=20, pady=(15, 5))

    tk.Label(
        win,
        text="Пример: http://192.168.1.50:8000 или http://127.0.0.1:8000",
        font=("Segoe UI", 9), fg="#64748b"
    ).pack(anchor="w", padx=20, pady=(0, 10))

    e_url = ttk.Entry(win, width=42, font=("Segoe UI", 10))
    e_url.pack(padx=20, pady=5)
    e_url.insert(0, BASE_URL)

    def test_and_save():
        global BASE_URL
        new_url = e_url.get().strip().rstrip("/")
        if not new_url.startswith("http://") and not new_url.startswith("https://"):
            new_url = "http://" + new_url

        try:
            r = requests.get(f"{new_url}/", timeout=4)
            if r.status_code == 200:
                BASE_URL = new_url
                app_config["server_url"] = new_url
                save_config(app_config)
                messagebox.showinfo("Успех", f"Соединение успешно установлено!\nСервер: {new_url}", parent=win)
                win.destroy()
                load_data()
            else:
                messagebox.showwarning("Внимание", f"Сервер ответил кодом {r.status_code}", parent=win)
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Не удалось подключиться:\n{ex}", parent=win)

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=18)
    ttk.Button(btn_frame, text="Проверить и сохранить", style="Accent.TButton", command=test_and_save).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Отмена", command=win.destroy).pack(side="left", padx=5)


# ================== АВТОРИЗАЦИЯ ==================
def show_login_window():
    global current_user, user_role
    win = tk.Tk()
    win.title("Вход в систему — Дислокация PRO")
    win.geometry("380x250")
    win.resizable(False, False)
    win.configure(bg="#f8fafc")

    # Центрирование окна
    win.eval('tk::PlaceWindow . center')

    tk.Label(
        win, text="🚢 Мониторинг Контейнеров PRO",
        font=("Segoe UI", 12, "bold"), fg="#1e40af", bg="#f8fafc"
    ).pack(pady=(18, 5))

    tk.Label(
        win, text="Вход для сотрудников компании",
        font=("Segoe UI", 9), fg="#64748b", bg="#f8fafc"
    ).pack(pady=(0, 15))

    form = tk.Frame(win, bg="#f8fafc")
    form.pack(padx=25)

    tk.Label(form, text="Логин:", font=("Segoe UI", 10), bg="#f8fafc").grid(row=0, column=0, sticky="w", pady=6)
    e_user = ttk.Entry(form, width=22, font=("Segoe UI", 10))
    e_user.grid(row=0, column=1, pady=6, padx=(10, 0))
    e_user.insert(0, app_config.get("last_username", ""))

    tk.Label(form, text="Пароль:", font=("Segoe UI", 10), bg="#f8fafc").grid(row=1, column=0, sticky="w", pady=6)
    e_pass = ttk.Entry(form, width=22, font=("Segoe UI", 10), show="*")
    e_pass.grid(row=1, column=1, pady=6, padx=(10, 0))

    def do_login():
        global current_user, user_role
        u = e_user.get().strip()
        p = e_pass.get()
        if not u:
            messagebox.showwarning("Внимание", "Введите имя пользователя / логин", parent=win)
            return
        if not p:
            messagebox.showwarning("Внимание", "Введите пароль", parent=win)
            return

        try:
            r = requests.post(f"{BASE_URL}/login", json={"username": u, "password": p}, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                current_user = data["username"]
                user_role = data.get("role", "dispatcher")
                app_config["last_username"] = current_user
                save_config(app_config)
                win.destroy()
                start_main_application()
            else:
                err_msg = r.json().get("detail", r.text) if r.headers.get("content-type") == "application/json" else r.text
                messagebox.showerror("Ошибка входа", f"Сервер отклонил вход:\n{err_msg}", parent=win)
        except requests.exceptions.ConnectionError:
            ans = messagebox.askyesno(
                "Сервер недоступен",
                f"Не удалось связаться с сервером по адресу:\n{BASE_URL}\n\nХотите изменить адрес сервера?",
                parent=win
            )
            if ans:
                open_settings_popup()
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Ошибка авторизации: {ex}", parent=win)

    def do_register():
        global current_user, user_role
        u = e_user.get().strip()
        p = e_pass.get()
        if not u:
            messagebox.showwarning("Внимание", "Введите имя пользователя / логин", parent=win)
            return
        if not p:
            messagebox.showwarning("Внимание", "Введите пароль для новой учетной записи", parent=win)
            return
        if len(p) < 3:
            messagebox.showwarning("Внимание", "Пароль должен содержать минимум 3 символа", parent=win)
            return

        try:
            r = requests.post(f"{BASE_URL}/register", json={"username": u, "password": p, "full_name": u}, timeout=HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                current_user = data["username"]
                user_role = data.get("role", "dispatcher")
                app_config["last_username"] = current_user
                save_config(app_config)
                messagebox.showinfo("Успех", f"Учетная запись '{u}' успешно создана!\nДобро пожаловать в систему.", parent=win)
                win.destroy()
                start_main_application()
            else:
                err_msg = r.json().get("detail", r.text) if r.headers.get("content-type") == "application/json" else r.text
                messagebox.showerror("Ошибка регистрации", f"Не удалось создать учетную запись:\n{err_msg}", parent=win)
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Ошибка связи с сервером: {ex}", parent=win)

    btn_f = tk.Frame(win, bg="#f8fafc")
    btn_f.pack(pady=15)
    ttk.Button(btn_f, text="🔑 Войти", command=do_login, style="Accent.TButton").pack(side="left", padx=4)
    ttk.Button(btn_f, text="✨ Создать аккаунт", command=do_register).pack(side="left", padx=4)

    e_pass.bind("<Return>", lambda _: do_login())
    e_user.bind("<Return>", lambda _: do_login())

    win.mainloop()


# ================== ЗАГРУЗКА И ОТОБРАЖЕНИЕ ДАННЫХ ==================
_raw_containers = []
_row_id_map = {}


def load_data():
    global _raw_containers
    search_val = search_entry.get().strip() if 'search_entry' in globals() else ""
    dest_val = filter_dest_combo.get() if 'filter_dest_combo' in globals() else ""
    status_val = filter_status_combo.get() if 'filter_status_combo' in globals() else ""
    client_val = filter_client_combo.get().strip() if 'filter_client_combo' in globals() else ""
    cargo_val = filter_cargo_combo.get().strip() if 'filter_cargo_combo' in globals() else ""

    params = {}
    if search_val:
        params["search"] = search_val
    if dest_val and dest_val not in ("Все нахождения", "Все направления"):
        params["destination"] = dest_val
    if status_val and status_val not in ("Все статусы", "Все"):
        params["status"] = status_val
    if client_val and client_val != "Все клиенты":
        params["client"] = client_val
    if cargo_val and cargo_val != "Все грузы":
        params["cargo"] = cargo_val

    try:
        resp = requests.get(f"{BASE_URL}/containers", params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            _raw_containers = resp.json()
            render_table(_raw_containers)
            update_stats()
            if 'filter_client_combo' in globals():
                filter_client_combo["values"] = ["Все клиенты"] + get_remembered_clients()
            if 'filter_cargo_combo' in globals():
                filter_cargo_combo["values"] = ["Все грузы"] + get_remembered_cargos()
        else:
            status_bar_label.config(text=f"Ошибка сервера: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        status_bar_label.config(text=f"⚠️ Сервер {BASE_URL} недоступен! Проверьте подключение.", fg="#dc2626")
    except Exception as e:
        status_bar_label.config(text=f"Ошибка: {e}", fg="#dc2626")


def render_table(containers):
    global _row_id_map
    for item in tree.get_children():
        tree.delete(item)
    _row_id_map.clear()

    for idx, c in enumerate(containers):
        days = calculate_days_local(c.get("sent_date", ""), c.get("return_date"))
        status = c.get("status", "")
        dest = c.get("destination", "")
        has_returned = bool(c.get("return_date") and c.get("return_date") != "-")

        # Определение цветового тега
        if (dest in ("Хайратон", "Афганистан") and not has_returned and days > 30):
            tag = "late"
        elif status == "Порожний":
            tag = "done"
        elif idx % 2 == 1:
            tag = "even"
        else:
            tag = "normal"

        row_vals = (
            c.get("container_no", ""),
            c.get("client", "") or "-",
            c.get("cargo_name", "") or "-",
            c.get("barge", "") or "-",
            c.get("destination", ""),
            c.get("status", ""),
            c.get("reason", "") or "-",
            format_date_ru(c.get("sent_date", "")),
            format_date_ru(c.get("return_date") or "-"),
            days,
            c.get("added_by") or "-"
        )

        iid = tree.insert("", tk.END, values=row_vals, tags=(tag,))
        _row_id_map[iid] = c

    status_bar_label.config(
        text=f"Всего отображается: {len(containers)} контейнеров | Обновлено: {datetime.now().strftime('%H:%M:%S')}",
        fg="#1e293b"
    )


def update_stats():
    try:
        resp = requests.get(f"{BASE_URL}/stats", timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            st = resp.json()
            lbl_sent_today.config(text=f"Отправлено сегодня: {st.get('sent_today', 0)}")
            lbl_hairatan.config(text=f"В Хайратоне: {st.get('hairatan_count', st.get('afghanistan_delayed', 0))}")
            lbl_termez.config(text=f"В Термез-порту: {st.get('termez_count', 0)}")
            lbl_status_kpi.config(text=f"Груженных: {st.get('loaded_count', 0)} | Порожних: {st.get('empty_count', 0)}")
    except Exception:
        pass


# ================== МОДАЛЬНОЕ ОКНО: ДАННЫЕ КОНТЕЙНЕРА ==================
def open_container_dialog(container_data=None):
    """
    Открывает модальное окно 'Данные контейнера'.
    Если container_data передан — режим редактирования (один контейнер).
    Если None — режим добавления новых контейнеров (до 20 номеров).
    """
    import re
    is_edit = container_data is not None
    container_id = container_data["id"] if is_edit else None

    dialog = tk.Toplevel(main_window)
    dialog.title("Данные контейнера" if is_edit else "➕ Добавить контейнеры / вагоны")
    dialog.geometry("580x620")
    dialog.resizable(False, False)
    dialog.grab_set()

    grid_frame = tk.Frame(dialog, padx=25, pady=15)
    grid_frame.pack(fill="both", expand=True)

    def add_row(row_idx, label_text, widget):
        lbl = tk.Label(grid_frame, text=label_text + ":", font=("Segoe UI", 10, "bold" if "Номер" in label_text else "normal"), anchor="w")
        lbl.grid(row=row_idx, column=0, sticky="nw" if "Номер" in label_text and not is_edit else "w", pady=5, padx=(0, 15))
        widget.grid(row=row_idx, column=1, sticky="ew", pady=5)
        grid_frame.columnconfigure(1, weight=1)

    # 1. Номер / Номера (любое количество)
    if is_edit:
        entry_no = ttk.Entry(grid_frame, font=("Segoe UI", 10))
        entry_no.insert(0, container_data.get("container_no", ""))
        add_row(0, "Номер контейнера *", entry_no)
    else:
        no_frame = tk.Frame(grid_frame)
        text_no = tk.Text(no_frame, height=3, width=35, font=("Consolas", 10), wrap="word")
        text_no.pack(fill="x", expand=True)
        lbl_batch_cnt = tk.Label(no_frame, text="Распознано: 0 шт. (вставляйте через Enter, пробел или запятую)", font=("Segoe UI", 8), fg="#2563eb")
        lbl_batch_cnt.pack(anchor="w", pady=(2, 0))

        def on_no_change(*_):
            val = text_no.get("1.0", "end")
            nums = [n.strip().upper() for n in re.split(r'[\r\n,;\s\t]+', val) if n.strip()]
            cnt = len(nums)
            lbl_batch_cnt.config(text=f"Распознано номеров: {cnt} шт.", fg="#1e40af" if cnt > 0 else "#64748b")

        text_no.bind("<KeyRelease>", on_no_change)
        text_no.bind("<FocusOut>", on_no_change)
        add_row(0, "Номера контейнеров / вагонов *", no_frame)

    # 2. Грузополучатель (с запоминанием)
    f_cl = tk.Frame(grid_frame)
    combo_client = ttk.Combobox(f_cl, values=get_remembered_clients(), font=("Segoe UI", 10))
    combo_client.pack(side="left", fill="x", expand=True)
    if is_edit and container_data.get("client") and container_data["client"] != "-":
        combo_client.set(container_data.get("client", ""))
    ttk.Button(f_cl, text="🧹 Очистить", width=12, command=lambda: (open_dictionary_window(), combo_client.config(values=get_remembered_clients()))).pack(side="right", padx=(5, 0))
    add_row(1, "Грузополучатель", f_cl)

    # 3. Наименование груза (с запоминанием)
    f_cg = tk.Frame(grid_frame)
    combo_cargo = ttk.Combobox(f_cg, values=get_remembered_cargos(), font=("Segoe UI", 10))
    combo_cargo.pack(side="left", fill="x", expand=True)
    if is_edit and container_data.get("cargo_name") and container_data["cargo_name"] != "-":
        combo_cargo.set(container_data.get("cargo_name", ""))
    ttk.Button(f_cg, text="🧹 Очистить", width=12, command=lambda: (open_dictionary_window(), combo_cargo.config(values=get_remembered_cargos()))).pack(side="right", padx=(5, 0))
    add_row(2, "Наименование груза", f_cg)

    # 4. Баржа
    combo_barge = ttk.Combobox(grid_frame, values=BARGES, font=("Segoe UI", 10), state="readonly")
    if is_edit and container_data.get("barge") and container_data["barge"] != "-":
        combo_barge.set(container_data.get("barge", ""))
    else:
        combo_barge.set(BARGES[0])
    add_row(3, "Баржа", combo_barge)

    # 5. Нахождение (Хайратон / Термез-порт)
    combo_dest = ttk.Combobox(grid_frame, values=DESTINATIONS, font=("Segoe UI", 10), state="readonly")
    if is_edit:
        combo_dest.set(container_data.get("destination", DESTINATIONS[0]))
    else:
        combo_dest.set(DESTINATIONS[0])
    add_row(4, "Нахождение", combo_dest)

    # 6. Статус (Груженный / Порожний)
    combo_status = ttk.Combobox(grid_frame, values=STATUSES, font=("Segoe UI", 10), state="readonly")
    if is_edit:
        combo_status.set(container_data.get("status", STATUSES[0]))
    else:
        combo_status.set(STATUSES[0])
    add_row(5, "Статус", combo_status)

    # 7. Причина
    entry_reason = ttk.Entry(grid_frame, font=("Segoe UI", 10))
    if is_edit and container_data.get("reason") and container_data["reason"] != "-":
        entry_reason.insert(0, container_data.get("reason", ""))
    add_row(6, "Причина / Прим.", entry_reason)

    # 8. Дата отправки
    if HAS_TKCALENDAR:
        date_sent = DateEntry(grid_frame, width=18, date_pattern="yyyy-mm-dd", font=("Segoe UI", 10))
        if is_edit and container_data.get("sent_date"):
            try:
                date_sent.set_date(container_data["sent_date"])
            except Exception:
                pass
    else:
        date_sent = ttk.Entry(grid_frame, font=("Segoe UI", 10))
        date_sent.insert(0, container_data.get("sent_date", date.today().strftime("%Y-%m-%d")) if is_edit else date.today().strftime("%Y-%m-%d"))
    add_row(7, "Дата отправки", date_sent)

    # 9. Дата возврата
    ret_frame = tk.Frame(grid_frame)
    if HAS_TKCALENDAR:
        date_ret = DateEntry(ret_frame, width=14, date_pattern="yyyy-mm-dd", font=("Segoe UI", 10))
        if is_edit and container_data.get("return_date") and container_data["return_date"] != "-":
            try:
                date_ret.set_date(container_data["return_date"])
            except Exception:
                pass
        else:
            date_ret.delete(0, "end")
    else:
        date_ret = ttk.Entry(ret_frame, font=("Segoe UI", 10))
        if is_edit and container_data.get("return_date") and container_data["return_date"] != "-":
            date_ret.insert(0, container_data["return_date"])

    date_ret.pack(side="left", fill="x", expand=True)

    def clear_ret_date():
        date_ret.delete(0, "end")
    ttk.Button(ret_frame, text="✖ Очистить", width=10, command=clear_ret_date).pack(side="left", padx=(5, 0))
    add_row(8, "Дата возврата", ret_frame)

    # Инфо: Дней в пути & Добавил
    cur_days = calculate_days_local(
        container_data.get("sent_date", date.today().strftime("%Y-%m-%d")) if is_edit else date.today().strftime("%Y-%m-%d"),
        container_data.get("return_date") if is_edit else None
    )
    lbl_info_transit = tk.Label(
        grid_frame,
        text=f"Дней в пути: {cur_days} | Добавил: {container_data.get('added_by', current_user) if is_edit else current_user}",
        font=("Segoe UI", 9, "italic"), fg="#475569"
    )
    lbl_info_transit.grid(row=9, column=0, columnspan=2, pady=(10, 5), sticky="w")

    def update_transit_label(*_):
        s = date_sent.get().strip()
        r = date_ret.get().strip()
        days = calculate_days_local(s, r)
        lbl_info_transit.config(text=f"Дней в пути: {days} | Добавил: {container_data.get('added_by', current_user) if is_edit else current_user}")

    date_sent.bind("<KeyRelease>", update_transit_label)
    date_ret.bind("<KeyRelease>", update_transit_label)

    # ================== КНОПКИ ДЕЙСТВИЙ ==================
    btn_panel = tk.Frame(dialog, padx=25, pady=15)
    btn_panel.pack(fill="x", side="bottom")

    def save_action():
        client_val = combo_client.get().strip()
        cargo_val = combo_cargo.get().strip()
        barge_val = combo_barge.get().strip()
        dest_val = combo_dest.get().strip()
        status_val = combo_status.get().strip()
        reason_val = entry_reason.get().strip()
        s_date = date_sent.get().strip()
        r_date = date_ret.get().strip()
        if not r_date or r_date == "-":
            r_date = None

        if client_val:
            remember_client(client_val)
        if cargo_val:
            remember_cargo(cargo_val)

        if is_edit:
            c_no = entry_no.get().strip().upper()
            if not c_no:
                messagebox.showwarning("Внимание", "Укажите номер контейнера!", parent=dialog)
                return

            payload = {
                "container_no": c_no,
                "client": client_val,
                "cargo_name": cargo_val,
                "barge": barge_val,
                "destination": dest_val,
                "status": status_val,
                "reason": reason_val,
                "sent_date": s_date,
                "return_date": r_date,
                "changed_by": current_user
            }

            try:
                res = requests.put(f"{BASE_URL}/containers/{container_id}", json=payload, timeout=HTTP_TIMEOUT)
                if res.status_code == 200:
                    messagebox.showinfo("Успешно", "Данные успешно сохранены!", parent=dialog)
                    dialog.destroy()
                    load_data()
                else:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить: {res.text}", parent=dialog)
            except Exception as ex:
                messagebox.showerror("Ошибка сети", str(ex), parent=dialog)
        else:
            raw_val = text_no.get("1.0", "end")
            numbers = [n.strip().upper() for n in re.split(r'[\r\n,;\s\t]+', raw_val) if n.strip()]

            if not numbers:
                messagebox.showwarning("Внимание", "Укажите хотя бы один номер контейнера или вагона!", parent=dialog)
                return

            payload = {
                "container_numbers": numbers,
                "client": client_val,
                "cargo_name": cargo_val,
                "barge": barge_val,
                "destination": dest_val,
                "status": status_val,
                "reason": reason_val,
                "sent_date": s_date,
                "return_date": r_date,
                "added_by": current_user
            }

            try:
                res = requests.post(f"{BASE_URL}/containers/batch", json=payload, timeout=HTTP_TIMEOUT)
                if res.status_code in (200, 201):
                    messagebox.showinfo("Успешно", f"Успешно добавлено контейнеров: {len(numbers)} шт!", parent=dialog)
                    dialog.destroy()
                    load_data()
                else:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить: {res.text}", parent=dialog)
            except Exception as ex:
                messagebox.showerror("Ошибка сети", str(ex), parent=dialog)

    def delete_action():
        if not is_edit:
            dialog.destroy()
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить контейнер {container_data['container_no']}?", parent=dialog):
            return
        try:
            res = requests.delete(
                f"{BASE_URL}/containers/{container_id}",
                json={"user": current_user, "reason": "Удалено из карточки"},
                timeout=HTTP_TIMEOUT
            )
            if res.status_code == 200:
                messagebox.showinfo("Успешно", "Контейнер удален", parent=dialog)
                dialog.destroy()
                load_data()
            else:
                messagebox.showerror("Ошибка", res.text, parent=dialog)
        except Exception as ex:
            messagebox.showerror("Ошибка", str(ex), parent=dialog)

    def show_history_action():
        if not is_edit:
            return
        try:
            res = requests.get(f"{BASE_URL}/containers/{container_id}/history", timeout=HTTP_TIMEOUT)
            if res.status_code != 200:
                messagebox.showerror("Ошибка", res.text, parent=dialog)
                return
            history_rows = res.json()
            hw = tk.Toplevel(dialog)
            hw.title(f"История изменений: {container_data['container_no']}")
            hw.geometry("650x380")

            txt = tk.Text(hw, wrap="word", font=("Consolas", 10), padx=10, pady=10)
            txt.pack(fill="both", expand=True)

            if not history_rows:
                txt.insert("end", "История изменений отсутствует.")
            else:
                for h in history_rows:
                    dt = h.get("created_at", "")
                    usr = h.get("user", "")
                    act = h.get("action", "")
                    ch = json.dumps(h.get("changes_parsed", {}), ensure_ascii=False, indent=2)
                    txt.insert("end", f"[{dt}] Пользователь: {usr} | Действие: {act}\n{ch}\n{'-'*60}\n")
            txt.config(state="disabled")
        except Exception as ex:
            messagebox.showerror("Ошибка", str(ex), parent=dialog)

    ttk.Button(btn_panel, text="💾 Сохранить", style="Accent.TButton", width=16, command=save_action).pack(side="left", padx=5)
    if is_edit:
        ttk.Button(btn_panel, text="🗑 Удалить", width=14, command=delete_action).pack(side="left", padx=5)
        ttk.Button(btn_panel, text="📜 История", width=14, command=show_history_action).pack(side="left", padx=5)

    ttk.Button(btn_panel, text="Закрыть", width=12, command=dialog.destroy).pack(side="right", padx=5)


# ================== ПАКЕТНОЕ УДАЛЕНИЕ ВЫБРАННЫХ ==================
def delete_selected_containers():
    selected_items = tree.selection()
    if not selected_items:
        messagebox.showinfo("Инфо", "Выберите один или несколько контейнеров в таблице (удерживайте Ctrl или Shift для выбора нескольких).", parent=main_window)
        return

    containers_to_delete = []
    for item in selected_items:
        c = _row_id_map.get(item)
        if c and "id" in c:
            containers_to_delete.append(c)

    if not containers_to_delete:
        return

    count = len(containers_to_delete)
    confirm_msg = (
        f"Вы точно хотите удалить выбранный контейнер {containers_to_delete[0].get('container_no', '')}?"
        if count == 1
        else f"Вы точно хотите удалить выбранные контейнеры ({count} шт.)?"
    )
    if not messagebox.askyesno("Подтверждение удаления", confirm_msg, parent=main_window):
        return

    ids = [c["id"] for c in containers_to_delete]
    try:
        resp = requests.post(
            f"{BASE_URL}/containers/batch-delete",
            json={"container_ids": ids, "user": current_user, "reason": "Пакетное удаление"},
            timeout=HTTP_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            messagebox.showinfo("Успех", f"Успешно удалено контейнеров: {data.get('deleted_count', len(ids))} шт.", parent=main_window)
            load_data()
        else:
            deleted_cnt = 0
            for cid in ids:
                r = requests.delete(
                    f"{BASE_URL}/containers/{cid}",
                    json={"user": current_user, "reason": "Пакетное удаление"},
                    timeout=HTTP_TIMEOUT
                )
                if r.status_code == 200:
                    deleted_cnt += 1
            messagebox.showinfo("Результат", f"Успешно удалено: {deleted_cnt} из {len(ids)} шт.", parent=main_window)
            load_data()
    except Exception as ex:
        messagebox.showerror("Ошибка сети", f"Не удалось удалить контейнеры:\n{ex}", parent=main_window)


# ================== ОКНО УПРАВЛЕНИЯ СПРАВОЧНИКАМИ (ОЧИСТКА В ЛЮБОЕ ВРЕМЯ) ==================
def open_dictionary_window():
    win = tk.Toplevel(main_window)
    win.title("🗂 Управление памятью ввода (Справочники)")
    win.geometry("560x520")
    win.resizable(False, False)
    win.grab_set()

    nb = ttk.Notebook(win)
    nb.pack(fill="both", expand=True, padx=15, pady=15)

    # Вкладка Клиенты
    f_clients = ttk.Frame(nb, padding=10)
    nb.add(f_clients, text="🏢 Грузополучатели")

    lbl_cl_info = tk.Label(f_clients, text="Сохраненные грузополучатели (выберите для удаления):", font=("Segoe UI", 9, "bold"))
    lbl_cl_info.pack(anchor="w", pady=(0, 5))

    frame_cl_list = tk.Frame(f_clients)
    frame_cl_list.pack(fill="both", expand=True)

    lb_clients = tk.Listbox(frame_cl_list, font=("Segoe UI", 10), height=14)
    lb_clients.pack(fill="both", expand=True, side="left")
    sb_cl = ttk.Scrollbar(frame_cl_list, orient="vertical", command=lb_clients.yview)
    sb_cl.pack(side="right", fill="y")
    lb_clients.config(yscrollcommand=sb_cl.set)

    def refresh_clients_lb():
        lb_clients.delete(0, "end")
        for item in get_remembered_clients():
            lb_clients.insert("end", item)

    refresh_clients_lb()

    def do_remove_client():
        sel = lb_clients.curselection()
        if not sel:
            messagebox.showinfo("Инфо", "Выберите грузополучателя из списка для удаления", parent=win)
            return
        val = lb_clients.get(sel[0])
        remove_remembered_client(val)
        refresh_clients_lb()
        if 'filter_client_combo' in globals():
            filter_client_combo["values"] = ["Все клиенты"] + get_remembered_clients()

    def do_clear_clients():
        if messagebox.askyesno("Подтверждение", "Вы точно хотите очистить всех сохраненных грузополучателей?", parent=win):
            clear_all_remembered_clients()
            refresh_clients_lb()
            if 'filter_client_combo' in globals():
                filter_client_combo["values"] = ["Все клиенты"] + get_remembered_clients()
            messagebox.showinfo("Успешно", "Список грузополучателей очищен!", parent=win)

    btn_cl_box = tk.Frame(f_clients)
    btn_cl_box.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_cl_box, text="❌ Удалить выбранного", command=do_remove_client).pack(side="left", padx=4)
    ttk.Button(btn_cl_box, text="🗑 Очистить весь список", command=do_clear_clients).pack(side="right", padx=4)

    # Вкладка Грузы
    f_cargos = ttk.Frame(nb, padding=10)
    nb.add(f_cargos, text="📦 Наименования грузов")

    lbl_cg_info = tk.Label(f_cargos, text="Сохраненные грузы (выберите для удаления):", font=("Segoe UI", 9, "bold"))
    lbl_cg_info.pack(anchor="w", pady=(0, 5))

    frame_cg_list = tk.Frame(f_cargos)
    frame_cg_list.pack(fill="both", expand=True)

    lb_cargos = tk.Listbox(frame_cg_list, font=("Segoe UI", 10), height=14)
    lb_cargos.pack(fill="both", expand=True, side="left")
    sb_cg = ttk.Scrollbar(frame_cg_list, orient="vertical", command=lb_cargos.yview)
    sb_cg.pack(side="right", fill="y")
    lb_cargos.config(yscrollcommand=sb_cg.set)

    def refresh_cargos_lb():
        lb_cargos.delete(0, "end")
        for item in get_remembered_cargos():
            lb_cargos.insert("end", item)

    refresh_cargos_lb()

    def do_remove_cargo():
        sel = lb_cargos.curselection()
        if not sel:
            messagebox.showinfo("Инфо", "Выберите наименование груза из списка для удаления", parent=win)
            return
        val = lb_cargos.get(sel[0])
        remove_remembered_cargo(val)
        refresh_cargos_lb()
        if 'filter_cargo_combo' in globals():
            filter_cargo_combo["values"] = ["Все грузы"] + get_remembered_cargos()

    def do_clear_cargos():
        if messagebox.askyesno("Подтверждение", "Вы точно хотите очистить все сохраненные наименования грузов?", parent=win):
            clear_all_remembered_cargos()
            refresh_cargos_lb()
            if 'filter_cargo_combo' in globals():
                filter_cargo_combo["values"] = ["Все грузы"] + get_remembered_cargos()
            messagebox.showinfo("Успешно", "Список наименований грузов очищен!", parent=win)

    btn_cg_box = tk.Frame(f_cargos)
    btn_cg_box.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_cg_box, text="❌ Удалить выбранный", command=do_remove_cargo).pack(side="left", padx=4)
    ttk.Button(btn_cg_box, text="🗑 Очистить весь список", command=do_clear_cargos).pack(side="right", padx=4)


# ================== ЭКСПОРТ В EXCEL ==================
def export_to_excel():
    if not HAS_EXCEL:
        messagebox.showerror("Ошибка", "Для экспорта в Excel установите библиотеки:\npip install pandas openpyxl")
        return

    path = filedialog.asksaveasfilename(
        defaultextension=".xlsx",
        filetypes=[("Excel Files", "*.xlsx")],
        initialfile=f"Дислокация_контейнеров_{date.today().strftime('%Y%m%d')}.xlsx"
    )
    if not path:
        return

    headers = [
        "Номер", "Грузополучатель", "Наименование груза", "Баржа", "Нахождение", "Статус",
        "Причина", "Дата отправки", "Дата возврата", "Дней в пути", "Добавлен пользователем"
    ]
    rows = []
    for iid in tree.get_children():
        rows.append(list(tree.item(iid, "values")))

    df = pd.DataFrame(rows, columns=headers)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Дислокация", index=False)
            ws = writer.book["Дислокация"]

            from openpyxl.styles import Font, Alignment, PatternFill
            from openpyxl.utils import get_column_letter

            header_fill = PatternFill("solid", fgColor="1E40AF")
            header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")

            for cell in ws[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")

            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions

            for col_i in range(1, ws.max_column + 1):
                letter = get_column_letter(col_i)
                max_len = max([len(str(ws.cell(row=r, column=col_i).value or "")) for r in range(1, ws.max_row + 1)] or [10])
                ws.column_dimensions[letter].width = min(max(max_len + 4, 13), 50)

        messagebox.showinfo("Успешно", f"Файл успешно сохранен:\n{path}")
    except Exception as ex:
        messagebox.showerror("Ошибка экспорта", str(ex))


# ================== ИНТЕРАКТИВНЫЙ ГРАФИК ==================
def show_chart():
    if not HAS_PLOTLY:
        messagebox.showerror("Ошибка", "Для интерактивного графика требуется plotly:\npip install plotly pandas")
        return

    if not _raw_containers:
        messagebox.showinfo("Инфо", "Нет данных для построения графика")
        return

    df = pd.DataFrame(_raw_containers)
    if "sent_date" not in df.columns or "destination" not in df.columns:
        messagebox.showwarning("Внимание", "Недостаточно данных для графика")
        return

    df["sent_date"] = pd.to_datetime(df["sent_date"], errors="coerce")
    df = df.dropna(subset=["sent_date"])
    if df.empty:
        messagebox.showinfo("Инфо", "Нет записей с корректными датами")
        return

    counts = df.groupby([df["sent_date"].dt.date, "destination"]).size().reset_index(name="count")
    counts.rename(columns={counts.columns[0]: "date"}, inplace=True)
    counts["date"] = pd.to_datetime(counts["date"])

    color_map = {
        "Хайратон":    "#ef4444",
        "Термез-порт": "#3b82f6",
        "Афганистан":  "#f59e0b",
        "Узбекистан":  "#10b981"
    }

    fig = px.bar(
        counts, x="date", y="count", color="destination",
        color_discrete_map=color_map, text="count",
        labels={"date": "Дата", "count": "Количество", "destination": "Нахождение"},
        title="Динамика отправок контейнеров по нахождениям (Хайратон / Термез-порт)",
        barmode="stack"
    )
    fig.update_layout(
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#ffffff",
        font=dict(family="Segoe UI, Arial", size=12),
        hovermode="x unified"
    )

    chart_file = os.path.join(os.path.dirname(__file__), "dislocation_chart.html")
    fig.write_html(chart_file)
    webbrowser.open("file://" + os.path.abspath(chart_file))


# ================== ОТЧЕТ ПО ДИСЛОКАЦИИ ==================
def open_report_window():
    try:
        resp = requests.get(f"{BASE_URL}/report", timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            messagebox.showerror("Ошибка", resp.text)
            return
        rep = resp.json()
    except Exception as ex:
        messagebox.showerror("Ошибка сети", str(ex))
        return

    rw = tk.Toplevel(main_window)
    rw.title("📋 Отчет по нахождениям (Хайратон / Термез-порт)")
    rw.geometry("1180x660")
    rw.grab_set()

    summary = rep.get("summary", {})
    top = tk.Frame(rw, bg="#1e40af", padx=15, pady=12)
    top.pack(fill="x")

    tk.Label(
        top,
        text=f"Всего в реестре: {summary.get('total_registry', 0)}   |   В Хайратоне: {summary.get('hairatan_now_count', 0)}   |   В Термез-порту: {summary.get('termez_now_count', 0)}",
        font=("Segoe UI", 11, "bold"), fg="#ffffff", bg="#1e40af"
    ).pack(side="left")

    body = tk.Frame(rw, padx=10, pady=10)
    body.pack(fill="both", expand=True)

    # Таблица Хайратон
    f_afg = ttk.LabelFrame(body, text=f"В Хайратоне ({summary.get('hairatan_now_count', 0)})")
    f_afg.pack(side="left", fill="both", expand=True, padx=(0, 5))

    t_afg = ttk.Treeview(
        f_afg,
        columns=("no", "client", "cargo", "barge", "sent", "days", "status"),
        show="headings"
    )
    for col, txt, w in [
        ("no", "Номер", 110), ("client", "Грузополучатель", 120),
        ("cargo", "Груз", 110), ("barge", "Баржа", 80),
        ("sent", "Отправлен", 90), ("days", "Дней", 60), ("status", "Статус", 90)
    ]:
        t_afg.heading(col, text=txt)
        t_afg.column(col, width=w, anchor="center" if col not in ("client", "cargo") else "w")

    sb_afg = ttk.Scrollbar(f_afg, orient="vertical", command=t_afg.yview)
    t_afg.configure(yscrollcommand=sb_afg.set)
    t_afg.pack(side="left", fill="both", expand=True)
    sb_afg.pack(side="right", fill="y")

    for item in rep.get("hairatan_now", []):
        t_afg.insert("", "end", values=(
            item.get("container_no", ""),
            item.get("client", "") or "-",
            item.get("cargo_name", "") or "-",
            item.get("barge", "") or "-",
            item.get("sent_date", ""),
            item.get("days_in_transit", item.get("days_in_afghanistan", 0)),
            item.get("status", "") or "-"
        ))

    # Таблица Термез-порт
    f_uz = ttk.LabelFrame(body, text=f"В Термез-порту ({summary.get('termez_now_count', 0)})")
    f_uz.pack(side="left", fill="both", expand=True, padx=(5, 0))

    t_uz = ttk.Treeview(
        f_uz,
        columns=("no", "client", "cargo", "barge", "sent", "days", "status"),
        show="headings"
    )
    for col, txt, w in [
        ("no", "Номер", 110), ("client", "Грузополучатель", 120),
        ("cargo", "Груз", 110), ("barge", "Баржа", 80),
        ("sent", "Отправлен", 90), ("days", "Дней", 60), ("status", "Статус", 90)
    ]:
        t_uz.heading(col, text=txt)
        t_uz.column(col, width=w, anchor="center" if col not in ("client", "cargo") else "w")

    sb_uz = ttk.Scrollbar(f_uz, orient="vertical", command=t_uz.yview)
    t_uz.configure(yscrollcommand=sb_uz.set)
    t_uz.pack(side="left", fill="both", expand=True)
    sb_uz.pack(side="right", fill="y")

    for item in rep.get("termez_now", []):
        t_uz.insert("", "end", values=(
            item.get("container_no", ""),
            item.get("client", "") or "-",
            item.get("cargo_name", "") or "-",
            item.get("barge", "") or "-",
            item.get("sent_date", ""),
            item.get("days_in_transit", 0),
            item.get("status", "") or "-"
        ))


# ================== ГЛАВНОЕ ОКНО ПРИЛОЖЕНИЯ ==================
def start_main_application():
    global main_window, tree, lbl_sent_today, lbl_hairatan, lbl_termez, lbl_status_kpi
    global search_entry, filter_dest_combo, filter_status_combo, filter_client_combo, filter_cargo_combo, status_bar_label

    main_window = tk.Tk()
    main_window.title("Мониторинг контейнеров PRO")
    main_window.geometry("1420x780")
    main_window.configure(bg="#f1f5f9")

    # Стилизация ttk
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        "Accent.TButton",
        font=("Segoe UI", 10, "bold"),
        foreground="#ffffff",
        background="#2563eb",
        padding=(12, 7)
    )
    style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])

    style.configure(
        "Main.Treeview",
        font=("Segoe UI", 10),
        rowheight=28,
        fieldbackground="#ffffff",
        background="#ffffff",
        foreground="#0f172a"
    )
    style.configure(
        "Main.Treeview.Heading",
        background="#1e40af",
        foreground="#ffffff",
        font=("Segoe UI", 10, "bold"),
        padding=(4, 6)
    )
    style.map("Main.Treeview.Heading", background=[("active", "#1d4ed8")])

    # 1. Верхняя синяя панель заголовка
    header_bar = tk.Frame(main_window, bg="#1e40af", height=50)
    header_bar.pack(fill="x")
    header_bar.pack_propagate(False)

    tk.Label(
        header_bar,
        text="🚢  Мониторинг контейнеров PRO",
        font=("Segoe UI", 14, "bold"),
        fg="#ffffff", bg="#1e40af"
    ).pack(side="left", padx=20, pady=10)

    user_info_text = f"👤 Пользователь: {current_user} ({user_role})"
    tk.Label(
        header_bar,
        text=user_info_text,
        font=("Segoe UI", 10),
        fg="#bfdbfe", bg="#1e40af"
    ).pack(side="right", padx=20, pady=12)

    # 2. Панель кнопок действий
    btn_panel = tk.Frame(main_window, bg="#e2e8f0", height=52)
    btn_panel.pack(fill="x")
    btn_panel.pack_propagate(False)

    ttk.Button(btn_panel, text="➕  Добавить контейнер", style="Accent.TButton", command=lambda: open_container_dialog(None)).pack(side="left", padx=8, pady=9)
    ttk.Button(btn_panel, text="🗑  Удалить выбранные", command=delete_selected_containers).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="🗂  Память / Очистить", command=open_dictionary_window).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="🔄  Обновить", command=load_data).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="📊  Экспорт в Excel", command=export_to_excel).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="📈  Интерактивный график", command=show_chart).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="📋  Отчет по дислокации", command=open_report_window).pack(side="left", padx=5, pady=9)
    ttk.Button(btn_panel, text="⚙️  Сервер", command=open_settings_popup).pack(side="right", padx=15, pady=9)

    # 3. Информационные плашки (KPI)
    kpi_frame = tk.Frame(main_window, bg="#eff6ff", height=38)
    kpi_frame.pack(fill="x")
    kpi_frame.pack_propagate(False)

    lbl_sent_today = tk.Label(
        kpi_frame, text="Отправлено сегодня: 0",
        font=("Segoe UI", 10, "bold"), fg="#1e40af", bg="#eff6ff"
    )
    lbl_sent_today.pack(side="left", padx=15, pady=8)

    tk.Label(kpi_frame, text="|", fg="#93c5fd", bg="#eff6ff").pack(side="left")

    lbl_hairatan = tk.Label(
        kpi_frame, text="В Хайратоне: 0",
        font=("Segoe UI", 10, "bold"), fg="#dc2626", bg="#eff6ff"
    )
    lbl_hairatan.pack(side="left", padx=15, pady=8)

    tk.Label(kpi_frame, text="|", fg="#93c5fd", bg="#eff6ff").pack(side="left")

    lbl_termez = tk.Label(
        kpi_frame, text="В Термез-порту: 0",
        font=("Segoe UI", 10, "bold"), fg="#0284c7", bg="#eff6ff"
    )
    lbl_termez.pack(side="left", padx=15, pady=8)

    tk.Label(kpi_frame, text="|", fg="#93c5fd", bg="#eff6ff").pack(side="left")

    lbl_status_kpi = tk.Label(
        kpi_frame, text="Груженных: 0 | Порожних: 0",
        font=("Segoe UI", 10, "bold"), fg="#059669", bg="#eff6ff"
    )
    lbl_status_kpi.pack(side="left", padx=15, pady=8)

    # 4. Панель быстрого поиска и фильтров
    filter_frame = tk.Frame(main_window, bg="#f8fafc", padx=10, pady=8)
    filter_frame.pack(fill="x")

    tk.Label(filter_frame, text="🔍 Поиск:", font=("Segoe UI", 9, "bold"), bg="#f8fafc").pack(side="left", padx=(0, 4))
    search_entry = ttk.Entry(filter_frame, width=18, font=("Segoe UI", 9))
    search_entry.pack(side="left", padx=(0, 10))
    search_entry.bind("<KeyRelease>", lambda _: load_data())

    tk.Label(filter_frame, text="Нахождение:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 4))
    filter_dest_combo = ttk.Combobox(filter_frame, values=["Все нахождения"] + DESTINATIONS, state="readonly", width=14)
    filter_dest_combo.set("Все нахождения")
    filter_dest_combo.pack(side="left", padx=(0, 10))
    filter_dest_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    tk.Label(filter_frame, text="Статус:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 4))
    filter_status_combo = ttk.Combobox(filter_frame, values=["Все статусы"] + STATUSES, state="readonly", width=12)
    filter_status_combo.set("Все статусы")
    filter_status_combo.pack(side="left", padx=(0, 10))
    filter_status_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    tk.Label(filter_frame, text="Клиент:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 4))
    filter_client_combo = ttk.Combobox(filter_frame, values=["Все клиенты"] + get_remembered_clients(), state="readonly", width=14)
    filter_client_combo.set("Все клиенты")
    filter_client_combo.pack(side="left", padx=(0, 10))
    filter_client_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    tk.Label(filter_frame, text="Груз:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 4))
    filter_cargo_combo = ttk.Combobox(filter_frame, values=["Все грузы"] + get_remembered_cargos(), state="readonly", width=14)
    filter_cargo_combo.set("Все грузы")
    filter_cargo_combo.pack(side="left", padx=(0, 10))
    filter_cargo_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    def reset_filters():
        search_entry.delete(0, "end")
        filter_dest_combo.set("Все нахождения")
        filter_status_combo.set("Все статусы")
        filter_client_combo.set("Все клиенты")
        filter_cargo_combo.set("Все грузы")
        load_data()

    ttk.Button(filter_frame, text="✕ Сбросить", command=reset_filters).pack(side="left")

    # 5. Основная таблица (Treeview)
    columns = (
        "Номер",
        "Грузополучатель",
        "Наименование груза",
        "Баржа",
        "Нахождение",
        "Статус",
        "Причина",
        "Дата отправки",
        "Дата возврата",
        "Дней в пути",
        "Добавлен пользователем"
    )

    col_widths = {
        "Номер": 120,
        "Грузополучатель": 140,
        "Наименование груза": 140,
        "Баржа": 100,
        "Нахождение": 110,
        "Статус": 100,
        "Причина": 180,
        "Дата отправки": 100,
        "Дата возврата": 100,
        "Дней в пути": 90,
        "Добавлен пользователем": 140
    }

    tbl_container = tk.Frame(main_window, bg="#cbd5e1")
    tbl_container.pack(fill="both", expand=True, padx=12, pady=(4, 6))

    tree = ttk.Treeview(
        tbl_container,
        columns=columns,
        show="headings",
        style="Main.Treeview",
        selectmode="extended"
    )

    for col in columns:
        tree.heading(col, text=col, anchor="center")
        tree.column(
            col,
            width=col_widths.get(col, 120),
            anchor="w" if col in ("Причина", "Грузополучатель", "Наименование груза") else "center"
        )

    # Цветовая подсветка строк
    tree.tag_configure("late", background="#fee2e2", foreground="#991b1b")      # Красный (задержан / >30 дней в Афганистане)
    tree.tag_configure("done", background="#dcfce7", foreground="#166534")      # Зеленый (доставлен / возвращен)
    tree.tag_configure("even", background="#f8fafc", foreground="#0f172a")      # Чередование строк
    tree.tag_configure("normal", background="#ffffff", foreground="#0f172a")

    vsb = ttk.Scrollbar(tbl_container, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(tbl_container, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    tbl_container.rowconfigure(0, weight=1)
    tbl_container.columnconfigure(0, weight=1)

    # Двойной клик на строку — открыть модальное окно деталей и редактирования
    def on_double_click(event):
        item = tree.identify_row(event.y)
        if not item:
            return
        c_data = _row_id_map.get(item)
        if c_data:
            open_container_dialog(c_data)

    tree.bind("<Double-1>", on_double_click)

    # 6. Статус-бар внизу окна
    status_bar = tk.Frame(main_window, bg="#f1f5f9", height=24)
    status_bar.pack(fill="x", side="bottom", padx=15, pady=4)
    status_bar_label = tk.Label(status_bar, text="Подключение...", font=("Segoe UI", 9), fg="#64748b", bg="#f1f5f9")
    status_bar_label.pack(side="left")

    # Первоначальная загрузка
    load_data()

    # Фоновое автообновление (каждые 30 секунд для синхронизации всей компании)
    def auto_refresh():
        try:
            load_data()
        except Exception:
            pass
        main_window.after(30000, auto_refresh)

    main_window.after(30000, auto_refresh)

    main_window.mainloop()


if __name__ == "__main__":
    show_login_window()
