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

# Справочники для выпадающих списков
DESTINATIONS = [
    "Казахстан",
    "Афганистан",
    "Узбекистан",
    "Таджикистан",
    "Туркменистан",
    "Кыргызстан",
    "Россия",
    "Китай"
]

BARGES = [
    "Zarafshon",
    "Surxon",
    "O'zbekiston",
    "Samarkand",
    "Jizzax",
    "Toshkent"
]

STATUSES = [
    "В пути",
    "Задержан",
    "Доставлен",
    "Возвращен",
    "Погрузка"
]


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def parse_date(val):
    if not val or val == "-":
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
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
        return max((r - s).days, 0)
    return max((date.today() - s).days, 0)


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
    search_val = search_entry.get().strip()
    dest_val = filter_dest_combo.get()
    status_val = filter_status_combo.get()

    params = {}
    if search_val:
        params["search"] = search_val
    if dest_val and dest_val != "Все направления":
        params["destination"] = dest_val
    if status_val and status_val != "Все статусы":
        params["status"] = status_val

    try:
        resp = requests.get(f"{BASE_URL}/containers", params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            _raw_containers = resp.json()
            render_table(_raw_containers)
            update_stats()
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
        days = c.get("days_in_transit", 0)
        status = c.get("status", "")
        dest = c.get("destination", "")
        has_returned = bool(c.get("return_date") and c.get("return_date") != "-")

        # Определение цветового тега
        if status == "Задержан" or (dest == "Афганистан" and not has_returned and days > 30):
            tag = "late"
        elif status == "Доставлен" or status == "Возвращен":
            tag = "done"
        elif idx % 2 == 1:
            tag = "even"
        else:
            tag = "normal"

        row_vals = (
            c.get("container_no", ""),
            c.get("client", "") or "-",
            c.get("barge", "") or "-",
            c.get("destination", ""),
            c.get("status", ""),
            c.get("reason", "") or "-",
            c.get("sent_date", ""),
            c.get("return_date") or "-",
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
            lbl_slow_afg.config(text=f"Задерживаются в Афганистане: {st.get('afghanistan_delayed', 0)}")
            lbl_in_transit.config(text=f"Всего в пути: {st.get('in_transit', 0)}")
    except Exception:
        pass


# ================== МОДАЛЬНОЕ ОКНО: ДАННЫЕ КОНТЕЙНЕРА ==================
def open_container_dialog(container_data=None):
    """
    Открывает модальное окно 'Данные контейнера' как на скриншоте.
    Если container_data передан — режим редактирования.
    Если None — режим добавления нового контейнера.
    """
    is_edit = container_data is not None
    container_id = container_data["id"] if is_edit else None

    dialog = tk.Toplevel(main_window)
    dialog.title("Данные контейнера" if is_edit else "Добавить контейнер")
    dialog.geometry("540x540")
    dialog.resizable(False, False)
    dialog.grab_set()

    # Поля формы
    fields = {}

    grid_frame = tk.Frame(dialog, padx=25, pady=15)
    grid_frame.pack(fill="both", expand=True)

    def add_row(row_idx, label_text, widget):
        lbl = tk.Label(grid_frame, text=label_text + ":", font=("Segoe UI", 10), anchor="w")
        lbl.grid(row=row_idx, column=0, sticky="w", pady=6, padx=(0, 15))
        widget.grid(row=row_idx, column=1, sticky="ew", pady=6)
        grid_frame.columnconfigure(1, weight=1)

    # 1. Номер
    entry_no = ttk.Entry(grid_frame, font=("Segoe UI", 10))
    if is_edit:
        entry_no.insert(0, container_data.get("container_no", ""))
    add_row(0, "Номер", entry_no)

    # 2. Грузополучатель
    entry_client = ttk.Entry(grid_frame, font=("Segoe UI", 10))
    if is_edit and container_data.get("client") and container_data["client"] != "-":
        entry_client.insert(0, container_data.get("client", ""))
    add_row(1, "Грузополучатель", entry_client)

    # 3. Баржа
    combo_barge = ttk.Combobox(grid_frame, values=BARGES, font=("Segoe UI", 10), state="readonly")
    if is_edit and container_data.get("barge") and container_data["barge"] != "-":
        combo_barge.set(container_data.get("barge", ""))
    else:
        combo_barge.set(BARGES[0])
    add_row(2, "Баржа", combo_barge)

    # 4. Направление (Казахстан, Афганистан, Узбекистан и др.)
    combo_dest = ttk.Combobox(grid_frame, values=DESTINATIONS, font=("Segoe UI", 10), state="readonly")
    if is_edit:
        combo_dest.set(container_data.get("destination", DESTINATIONS[1]))
    else:
        combo_dest.set("Афганистан")
    add_row(3, "Направление", combo_dest)

    # 5. Статус
    combo_status = ttk.Combobox(grid_frame, values=STATUSES, font=("Segoe UI", 10), state="readonly")
    if is_edit:
        combo_status.set(container_data.get("status", STATUSES[0]))
    else:
        combo_status.set("В пути")
    add_row(4, "Статус", combo_status)

    # 6. Причина
    entry_reason = ttk.Entry(grid_frame, font=("Segoe UI", 10))
    if is_edit and container_data.get("reason") and container_data["reason"] != "-":
        entry_reason.insert(0, container_data.get("reason", ""))
    add_row(5, "Причина", entry_reason)

    # 7. Дата отправки
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
    add_row(6, "Дата отправки", date_sent)

    # 8. Дата возврата
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
    add_row(7, "Дата возврата", ret_frame)

    # 9. Инфо: Дней в пути & Добавил
    lbl_info_transit = tk.Label(
        grid_frame,
        text=f"Дней в пути: {container_data.get('days_in_transit', 0) if is_edit else 0} | Добавил: {container_data.get('added_by', current_user) if is_edit else current_user}",
        font=("Segoe UI", 9, "italic"), fg="#475569"
    )
    lbl_info_transit.grid(row=8, column=0, columnspan=2, pady=(10, 5), sticky="w")

    # ================== КНОПКИ ДЕЙСТВИЙ ==================
    btn_panel = tk.Frame(dialog, padx=25, pady=15)
    btn_panel.pack(fill="x", side="bottom")

    def save_action():
        c_no = entry_no.get().strip().upper()
        if not c_no:
            messagebox.showwarning("Внимание", "Укажите номер контейнера!", parent=dialog)
            return

        s_date = date_sent.get().strip()
        r_date = date_ret.get().strip()
        if not r_date or r_date == "-":
            r_date = None

        payload = {
            "container_no": c_no,
            "client": entry_client.get().strip(),
            "barge": combo_barge.get().strip(),
            "destination": combo_dest.get().strip(),
            "status": combo_status.get().strip(),
            "reason": entry_reason.get().strip(),
            "sent_date": s_date,
            "return_date": r_date
        }

        try:
            if is_edit:
                payload["changed_by"] = current_user
                res = requests.put(f"{BASE_URL}/containers/{container_id}", json=payload, timeout=HTTP_TIMEOUT)
            else:
                payload["added_by"] = current_user
                res = requests.post(f"{BASE_URL}/containers", json=payload, timeout=HTTP_TIMEOUT)

            if res.status_code in (200, 201):
                messagebox.showinfo("Успешно", "Данные успешно сохранены!", parent=dialog)
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
        "Номер", "Грузополучатель", "Баржа", "Направление", "Статус",
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
        "Афганистан":  "#ef4444",
        "Узбекистан":  "#3b82f6",
        "Таджикистан": "#f59e0b",
        "Казахстан":   "#10b981",
        "Туркменистан":"#8b5cf6",
        "Кыргызстан":  "#06b6d4",
        "Россия":      "#64748b",
        "Китай":       "#ec4899"
    }

    fig = px.bar(
        counts, x="date", y="count", color="destination",
        color_discrete_map=color_map, text="count",
        labels={"date": "Дата", "count": "Количество", "destination": "Направление"},
        title="Динамика отправок контейнеров по направлениям",
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
    rw.title("📋 Отчет по дислокации (Афганистан / Узбекистан)")
    rw.geometry("1100x650")
    rw.grab_set()

    summary = rep.get("summary", {})
    top = tk.Frame(rw, bg="#1e40af", padx=15, pady=12)
    top.pack(fill="x")

    tk.Label(
        top,
        text=f"Всего в реестре: {summary.get('total_registry', 0)}   |   Сейчас в Афганистане: {summary.get('afghanistan_now_count', 0)}   |   В Узбекистане (на базе): {summary.get('uzbekistan_now_count', 0)}",
        font=("Segoe UI", 11, "bold"), fg="#ffffff", bg="#1e40af"
    ).pack(side="left")

    body = tk.Frame(rw, padx=10, pady=10)
    body.pack(fill="both", expand=True)

    # Таблица Афганистан
    f_afg = ttk.LabelFrame(body, text=f"Задерживаются / в пути в Афганистане ({summary.get('afghanistan_now_count', 0)})")
    f_afg.pack(side="left", fill="both", expand=True, padx=(0, 5))

    t_afg = ttk.Treeview(
        f_afg,
        columns=("no", "client", "barge", "sent", "days", "reason"),
        show="headings"
    )
    for col, txt, w in [
        ("no", "Номер", 120), ("client", "Грузополучатель", 140),
        ("barge", "Баржа", 100), ("sent", "Отправлен", 100),
        ("days", "Дней", 70), ("reason", "Причина", 180)
    ]:
        t_afg.heading(col, text=txt)
        t_afg.column(col, width=w, anchor="center" if col != "reason" else "w")

    sb_afg = ttk.Scrollbar(f_afg, orient="vertical", command=t_afg.yview)
    t_afg.configure(yscrollcommand=sb_afg.set)
    t_afg.pack(side="left", fill="both", expand=True)
    sb_afg.pack(side="right", fill="y")

    for item in rep.get("afghanistan_now", []):
        t_afg.insert("", "end", values=(
            item.get("container_no", ""),
            item.get("client", "") or "-",
            item.get("barge", "") or "-",
            item.get("sent_date", ""),
            item.get("days_in_afghanistan", 0),
            item.get("reason", "") or "-"
        ))

    # Таблица Узбекистан
    f_uz = ttk.LabelFrame(body, text=f"В Узбекистане / на месте ({summary.get('uzbekistan_now_count', 0)})")
    f_uz.pack(side="left", fill="both", expand=True, padx=(5, 0))

    t_uz = ttk.Treeview(f_uz, columns=("no",), show="headings")
    t_uz.heading("no", text="Номер контейнера в реестре")
    t_uz.column("no", width=220, anchor="center")

    sb_uz = ttk.Scrollbar(f_uz, orient="vertical", command=t_uz.yview)
    t_uz.configure(yscrollcommand=sb_uz.set)
    t_uz.pack(side="left", fill="both", expand=True)
    sb_uz.pack(side="right", fill="y")

    for c_no in rep.get("uzbekistan_now", []):
        t_uz.insert("", "end", values=(c_no,))


# ================== ГЛАВНОЕ ОКНО ПРИЛОЖЕНИЯ ==================
def start_main_application():
    global main_window, tree, lbl_sent_today, lbl_slow_afg, lbl_in_transit
    global search_entry, filter_dest_combo, filter_status_combo, status_bar_label

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
    lbl_sent_today.pack(side="left", padx=20, pady=8)

    tk.Label(kpi_frame, text="|", fg="#93c5fd", bg="#eff6ff").pack(side="left")

    lbl_slow_afg = tk.Label(
        kpi_frame, text="Задерживаются в Афганистане: 0",
        font=("Segoe UI", 10, "bold"), fg="#dc2626", bg="#eff6ff"
    )
    lbl_slow_afg.pack(side="left", padx=20, pady=8)

    tk.Label(kpi_frame, text="|", fg="#93c5fd", bg="#eff6ff").pack(side="left")

    lbl_in_transit = tk.Label(
        kpi_frame, text="Всего в пути: 0",
        font=("Segoe UI", 10, "bold"), fg="#0369a1", bg="#eff6ff"
    )
    lbl_in_transit.pack(side="left", padx=20, pady=8)

    # 4. Панель быстрого поиска и фильтров
    filter_frame = tk.Frame(main_window, bg="#f8fafc", padx=15, pady=8)
    filter_frame.pack(fill="x")

    tk.Label(filter_frame, text="🔍 Поиск:", font=("Segoe UI", 9, "bold"), bg="#f8fafc").pack(side="left", padx=(0, 5))
    search_entry = ttk.Entry(filter_frame, width=24, font=("Segoe UI", 9))
    search_entry.pack(side="left", padx=(0, 15))
    search_entry.bind("<KeyRelease>", lambda _: load_data())

    tk.Label(filter_frame, text="Направление:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 5))
    filter_dest_combo = ttk.Combobox(filter_frame, values=["Все направления"] + DESTINATIONS, state="readonly", width=16)
    filter_dest_combo.set("Все направления")
    filter_dest_combo.pack(side="left", padx=(0, 15))
    filter_dest_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    tk.Label(filter_frame, text="Статус:", font=("Segoe UI", 9), bg="#f8fafc").pack(side="left", padx=(0, 5))
    filter_status_combo = ttk.Combobox(filter_frame, values=["Все статусы"] + STATUSES, state="readonly", width=14)
    filter_status_combo.set("Все статусы")
    filter_status_combo.pack(side="left", padx=(0, 15))
    filter_status_combo.bind("<<ComboboxSelected>>", lambda _: load_data())

    def reset_filters():
        search_entry.delete(0, "end")
        filter_dest_combo.set("Все направления")
        filter_status_combo.set("Все статусы")
        load_data()

    ttk.Button(filter_frame, text="Сбросить фильтры", command=reset_filters).pack(side="left")

    # 5. Основная таблица (Treeview)
    columns = (
        "Номер",
        "Грузополучатель",
        "Баржа",
        "Направление",
        "Статус",
        "Причина",
        "Дата отправки",
        "Дата возврата",
        "Дней в пути",
        "Добавлен пользователем"
    )

    col_widths = {
        "Номер": 130,
        "Грузополучатель": 160,
        "Баржа": 120,
        "Направление": 120,
        "Статус": 110,
        "Причина": 220,
        "Дата отправки": 110,
        "Дата возврата": 110,
        "Дней в пути": 90,
        "Добавлен пользователем": 160
    }

    tbl_container = tk.Frame(main_window, bg="#cbd5e1")
    tbl_container.pack(fill="both", expand=True, padx=12, pady=(4, 6))

    tree = ttk.Treeview(
        tbl_container,
        columns=columns,
        show="headings",
        style="Main.Treeview",
        selectmode="browse"
    )

    for col in columns:
        tree.heading(col, text=col, anchor="center")
        tree.column(
            col,
            width=col_widths.get(col, 120),
            anchor="w" if col in ("Причина", "Грузополучатель") else "center"
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
