import datetime as dt
import hashlib
import html
import json
import os
import queue
import random
import re
import subprocess
import sys
import time
import threading
import tkinter as tk
from html.parser import HTMLParser
from pathlib import Path
from tkinter import ttk
from urllib import error, parse, request


DEFAULT_BASE_URL = "http://39.100.83.141:81/admin-api"
DEFAULT_TOKEN = ""
DEFAULT_PROJECT_ID = "6"
DEFAULT_TASK_TYPE = 4
APP_VERSION = "1.0.0"
GITHUB_UPDATE_REPO = "Fiz2Z/daily-report-tool"
DEFAULT_UPDATE_ASSET_KEYWORD = "report-tool.exe"


def resolve_app_dir():
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / "DailyReportTool" / "daily_report_data"
        return Path(sys.executable).resolve().parent / "daily_report_data"
    return Path(__file__).with_name("daily_report_data")


APP_DIR = resolve_app_dir()
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "history.jsonl"

DEFAULT_STATES = [
    ("已完成", 65),
    ("进行中", 66),
    ("打开", 67),
    ("挂起", 68),
    ("已拒绝", 69),
    ("已发布", 70),
    ("重新打开", 71),
    ("已修复", 72),
    ("处理中", 73),
    ("新提交", 74),
    ("已打包", 79),
    ("关闭", 64),
    ("未完成", 63),
]

SLOTS_5 = [
    ("09:00:00", "11:00:00", 2.0),
    ("11:00:00", "14:30:00", 2.0),
    ("14:30:00", "16:30:00", 2.0),
    ("16:30:00", "18:30:00", 2.0),
    ("18:30:00", "20:30:00", 2.0),
]
SLOTS_4 = SLOTS_5[:4]


class ApiError(RuntimeError):
    pass


class PlainTextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self):
        text = html.unescape(" ".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(value):
    parser = PlainTextHTMLParser()
    parser.feed(value or "")
    return parser.get_text()


def today_string():
    return dt.date.today().strftime("%Y-%m-%d")


def tomorrow_string():
    return (dt.date.today() + dt.timedelta(days=1)).strftime("%Y-%m-%d")


def auto_count_for_date(date_text):
    day = dt.datetime.strptime(date_text, "%Y-%m-%d").date().weekday()
    if day in (0, 1, 2):
        return 5
    if day in (3, 4):
        return 4
    return 0


def build_slots(date_text, count):
    if count == 5:
        slots = SLOTS_5
    elif count == 4:
        slots = SLOTS_4
    else:
        slots = SLOTS_5[:count]
    rows = []
    for start_time, due_time, hours in slots:
        rows.append(
            {
                "start": f"{date_text} {start_time}",
                "due": f"{date_text} {due_time}",
                "hours": hours,
            }
        )
    return rows


def normalize_version(value):
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts[:4]) if parts else (0,)


def is_newer_version(remote_version, local_version):
    remote = normalize_version(remote_version)
    local = normalize_version(local_version)
    length = max(len(remote), len(local))
    remote += (0,) * (length - len(remote))
    local += (0,) * (length - len(local))
    return remote > local


def request_json(url, headers=None, timeout=20):
    req = request.Request(url, headers=headers or {}, method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def download_url_to_file(url, target_path, headers=None, timeout=60):
    req = request.Request(url, headers=headers or {}, method="GET")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with request.urlopen(req, timeout=timeout) as resp, target_path.open("wb") as file:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            file.write(chunk)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def github_headers():
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": "DailyReportTool-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def extract_asset_sha256(asset, release_body):
    digest = str(asset.get("digest") or "").strip()
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    match = re.search(r"sha256\s*[:=]\s*([a-fA-F0-9]{64})", release_body or "", re.IGNORECASE)
    return match.group(1).lower() if match else ""


def default_config():
    return {
        "base_url": DEFAULT_BASE_URL,
        "token": DEFAULT_TOKEN,
        "project_id": DEFAULT_PROJECT_ID,
        "tenant_id": "1",
        "user_id": "",
        "nickname": "",
        "dept_id": "",
        "dept_name": "",
        "post_names": "",
    }


def load_app_config():
    config = default_config()
    loaded_path = None
    for config_path in config_read_paths():
        if not config_path.exists():
            continue
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update({key: str(value) for key, value in saved.items() if value is not None})
                loaded_path = config_path
                break
        except (OSError, json.JSONDecodeError):
            pass
    if loaded_path and loaded_path != CONFIG_PATH:
        try:
            save_app_config(config)
        except OSError:
            pass
    return config


def config_read_paths():
    paths = [CONFIG_PATH]
    if getattr(sys, "frozen", False):
        paths.extend(
            [
                Path(sys.executable).resolve().parent / "daily_report_data" / "config.json",
                Path.cwd() / "daily_report_data" / "config.json",
            ]
        )
    seen = set()
    unique_paths = []
    for path in paths:
        normalized = str(path.resolve()) if path.exists() else str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_paths.append(path)
    return unique_paths


def save_app_config(config):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history_record(record):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_history_records():
    if not HISTORY_PATH.exists():
        return []
    rows = []
    with HISTORY_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


class ApiClient:
    def __init__(self, base_url, token, principal_id="", tenant_id="", timeout=20):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        if self.token and not self.token.lower().startswith("bearer "):
            self.token = f"Bearer {self.token}"
        self.principal_id = str(principal_id).strip()
        self.tenant_id = str(tenant_id).strip()
        self.timeout = timeout

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += "?" + parse.urlencode(query)
        return url

    def _request(self, method, path, query=None, body=None):
        data = None
        parsed_base = parse.urlparse(self.base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": origin,
            "Referer": origin + "/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.token:
            headers["Authorization"] = self.token
        if self.principal_id:
            headers["principalId"] = self.principal_id
        if self.tenant_id:
            headers["tenant-id"] = self.tenant_id
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self._url(path, query),
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"HTTP {exc.code}: {raw}") from exc
        except error.URLError as exc:
            raise ApiError(f"网络请求失败: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"响应不是 JSON: {raw[:300]}") from exc
        if payload.get("code") != 0:
            raise ApiError(f"接口返回错误: {payload}")
        return payload.get("data")

    def list_requirements(self, project_id, keyword="", page_no=1, page_size=50):
        body = {
            "showType": "2",
            "order": {"code": "", "dir": ""},
            "search": {"keywords": keyword, "scope": ["code", "title"]},
            "conditions": {"conditions": []},
        }
        query = {
            "pageNo": page_no,
            "pageSize": page_size,
            "projectId": project_id,
            "addon": "idea",
        }
        data = self._request("POST", "/project/work-item/page", query, body)
        return data.get("list", []) if isinstance(data, dict) else []

    def list_my_reports(self, project_id, user_id, keyword="", page_no=1, page_size=20):
        body = {
            "showType": "2",
            "order": {"code": "createTime", "dir": "-1"},
            "search": {"keywords": keyword, "scope": ["code", "title"]},
            "conditions": {
                "conditions": [
                    {
                        "logic": "1",
                        "code": "assignee",
                        "operator": "in",
                        "type": "single_member",
                        "param": [{"id": str(user_id), "type": "2"}],
                    }
                ],
                "logic": "1",
            },
            "principalId": str(project_id),
            "addonViewId": "1389",
        }
        query = {
            "pageNo": page_no,
            "pageSize": page_size,
            "projectId": project_id,
            "addon": "work_item",
        }
        data = self._request("POST", "/project/work-item/page", query, body)
        if isinstance(data, dict):
            return data.get("list", []), data.get("total", 0)
        return [], 0

    def get_work_item(self, item_id):
        return self._request("GET", "/project/work-item/get", {"id": item_id})

    def get_profile(self):
        return self._request("GET", "/system/user/profile/get")

    def create_task(self, project_id, parent_id, title):
        body = {
            "projectId": str(project_id),
            "parentId": int(parent_id),
            "title": title,
            "type": DEFAULT_TASK_TYPE,
        }
        return self._request("POST", "/project/work-item/create", body=body)

    def list_members(self, project_id):
        return self._request("GET", "/project/project/members", {"principalId": project_id})

    def set_assignee(self, item_id, assignee):
        return self._request(
            "PUT",
            "/project/work-item/assignee",
            body={"id": int(item_id), "assignee": int(assignee)},
        )

    def set_time_property(self, item_id, code, date_text):
        return self._request(
            "PUT",
            "/project/work-item/property",
            body={
                "id": int(item_id),
                "code": code,
                "value": {"date": date_text, "withTime": True},
            },
        )

    def selectable_states(self, item_id):
        return self._request("GET", "/project/work-item/selectable-states", {"id": item_id})

    def set_state(self, item_id, state_id):
        return self._request(
            "PUT",
            "/project/work-item/batch/state",
            body={"ids": [int(item_id)], "stateId": int(state_id)},
        )

    def set_workload(self, item_id, estimated_hours):
        return self._request(
            "PUT",
            "/project/work-item/workload",
            body={
                "id": int(item_id),
                "estimatedWorkload": float(estimated_hours),
                "remainingWorkload": "",
            },
        )

    def register_workload(self, item_id, hours, estimated_hours, register_date):
        body = {
            "type": "work_item",
            "instId": int(item_id),
            "sysModule": "work_item",
            "categoryId": "",
            "description": "<p><br></p>",
            "reportedWorkloadStr": float(hours),
            "remainingWorkloadStr": 0,
            "reportedWorkload": float(hours),
            "remainingWorkload": 0,
            "estimatedWorkload": f"{float(estimated_hours):.1f}",
            "registerDate": register_date,
            "id": int(item_id),
        }
        return self._request("POST", "/common/workload", body=body)


class DailyReportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("日报批量创建工具")
        self.geometry("1180x760")
        self.minsize(1040, 680)

        self.api = None
        self.requirements = {}
        self.members = {}
        self.generated_rows = []
        self.ui_queue = queue.Queue()
        self.current_step = 0
        self.step_frames = []
        self.history_dialog = None
        self.all_reports_dialog = None
        self.creation_in_progress = False
        self.app_config = load_app_config()

        self.base_url_var = tk.StringVar(value=self.app_config["base_url"])
        self.token_var = tk.StringVar(value=self.app_config["token"])
        self.project_id_var = tk.StringVar(value=self.app_config["project_id"])
        self.tenant_id_var = tk.StringVar(value=self.app_config["tenant_id"])
        self.user_id_var = tk.StringVar(value=self.app_config.get("user_id", ""))
        self.nickname_var = tk.StringVar(value=self.app_config.get("nickname", ""))
        self.dept_id_var = tk.StringVar(value=self.app_config.get("dept_id", ""))
        self.dept_name_var = tk.StringVar(value=self.app_config.get("dept_name", ""))
        self.post_names_var = tk.StringVar(value=self.app_config.get("post_names", ""))
        self.keyword_var = tk.StringVar()
        self.date_var = tk.StringVar(value=tomorrow_string())
        self.manual_count_var = tk.StringVar(value="5")
        self.assignee_var = tk.StringVar()
        self.state_var = tk.StringVar(value="67 - 打开")
        self.estimate_var = tk.DoubleVar(value=2.0)
        self.report_var = tk.DoubleVar(value=2.0)
        self.interval_min_var = tk.IntVar(value=5)
        self.interval_max_var = tk.IntVar(value=30)
        self.set_assignee_var = tk.BooleanVar(value=True)
        self.set_state_var = tk.BooleanVar(value=True)
        self.set_time_var = tk.BooleanVar(value=True)
        self.set_estimate_var = tk.BooleanVar(value=True)
        self.register_workload_var = tk.BooleanVar(value=True)
        self.continue_on_error_var = tk.BooleanVar(value=False)
        for variable in (self.date_var, self.manual_count_var, self.estimate_var, self.report_var):
            variable.trace_add("write", self.on_preview_config_changed)

        self._configure_styles()
        self._build_menu()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close_request)
        self.after(100, self._drain_queue)
        self.after(300, self.load_initial_data)

    def _configure_styles(self):
        self.colors = {
            "bg": "#eef3f8",
            "panel": "#ffffff",
            "panel_alt": "#f8fafc",
            "text": "#101828",
            "muted": "#667085",
            "border": "#d0d7e2",
            "primary": "#0f62fe",
            "primary_hover": "#0043ce",
            "soft": "#e8f1ff",
            "success": "#0f766e",
            "danger": "#b42318",
            "log_bg": "#111827",
            "log_fg": "#e5edff",
        }
        self.configure(bg=self.colors["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = ("Microsoft YaHei UI", 10)
        title_font = ("Microsoft YaHei UI", 18, "bold")
        section_font = ("Microsoft YaHei UI", 10, "bold")

        style.configure(".", font=base_font, foreground=self.colors["text"], background=self.colors["bg"])
        style.configure("App.TFrame", background=self.colors["bg"])
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("Alt.TFrame", background=self.colors["panel_alt"])
        style.configure("Sidebar.TFrame", background="#0f172a")
        style.configure("Header.TLabel", font=title_font, foreground=self.colors["text"], background=self.colors["bg"])
        style.configure("Subtle.TLabel", foreground=self.colors["muted"], background=self.colors["bg"])
        style.configure("Panel.TLabel", foreground=self.colors["text"], background=self.colors["panel"])
        style.configure("Muted.Panel.TLabel", foreground=self.colors["muted"], background=self.colors["panel"])
        style.configure("Sidebar.TLabel", foreground="#cbd5e1", background="#0f172a")
        style.configure("SidebarActive.TLabel", foreground="#ffffff", background="#1d4ed8", padding=(12, 10))
        style.configure("SidebarDone.TLabel", foreground="#bfdbfe", background="#0f172a", padding=(12, 10))
        style.configure("SidebarIdle.TLabel", foreground="#94a3b8", background="#0f172a", padding=(12, 10))
        style.configure("Card.TLabelframe", background=self.colors["panel"], bordercolor=self.colors["border"], relief="solid")
        style.configure(
            "Card.TLabelframe.Label",
            font=section_font,
            foreground=self.colors["text"],
            background=self.colors["bg"],
        )
        style.configure("TLabelFrame", background=self.colors["panel"])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=self.colors["border"], lightcolor=self.colors["border"])
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor=self.colors["border"], arrowcolor=self.colors["muted"])
        style.configure("TRadiobutton", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("TButton", padding=(13, 7), background="#ffffff", bordercolor=self.colors["border"])
        style.map("TButton", background=[("active", self.colors["soft"])])
        style.configure("Accent.TButton", padding=(14, 7), background=self.colors["primary"], foreground="#ffffff")
        style.map(
            "Accent.TButton",
            background=[
                ("disabled", "#e2e8f0"),
                ("active", self.colors["primary_hover"]),
                ("pressed", self.colors["primary_hover"]),
            ],
            foreground=[
                ("disabled", "#94a3b8"),
                ("active", "#ffffff"),
                ("pressed", "#ffffff"),
            ],
        )
        style.configure("Ghost.TButton", padding=(12, 7), background=self.colors["bg"], bordercolor=self.colors["bg"])
        style.configure(
            "Treeview",
            rowheight=30,
            background="#ffffff",
            fieldbackground="#ffffff",
            bordercolor=self.colors["border"],
            lightcolor=self.colors["border"],
        )
        style.configure(
            "Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            background="#eef2f7",
            foreground=self.colors["text"],
            relief="flat",
        )
        style.map("Treeview", background=[("selected", self.colors["primary"])], foreground=[("selected", "#ffffff")])

    def _build_menu(self):
        menu_bar = tk.Menu(self)
        menu_bar.add_command(label="全局配置", command=self.open_config_dialog)
        menu_bar.add_command(label="全部日报", command=self.open_all_reports_dialog)
        menu_bar.add_command(label="历史记录", command=self.open_history_dialog)
        menu_bar.add_command(label="检查更新", command=self.check_for_updates)
        self.config(menu=menu_bar)

    def _build_ui(self):
        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(root, style="Sidebar.TFrame", padding=(12, 18))
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        self.step_labels = []
        for index, text in enumerate(["1 选择需求", "2 日报配置", "3 内容预览"]):
            label = ttk.Label(sidebar, text=text, style="SidebarIdle.TLabel", width=16)
            label.pack(fill=tk.X, pady=(0, 8))
            self.step_labels.append(label)

        main = ttk.Frame(root, style="Panel.TFrame", padding=18)
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)

        self.step_container = ttk.Frame(main, style="Panel.TFrame")
        self.step_container.grid(row=0, column=0, sticky="nsew")
        self.step_container.rowconfigure(0, weight=1)
        self.step_container.columnconfigure(0, weight=1)

        self.nav = ttk.Frame(main, style="Panel.TFrame")
        self.nav.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        self.nav.columnconfigure(0, weight=1)
        self.back_button = ttk.Button(self.nav, text="上一步", command=self.previous_step)
        self.next_button = ttk.Button(self.nav, text="下一步", command=self.next_step, style="Accent.TButton")
        self.execute_button = ttk.Button(self.nav, text="确认执行", command=self.execute_reports, style="Accent.TButton")
        self.back_button.grid(row=0, column=1, padx=(0, 8))
        self.next_button.grid(row=0, column=2)
        self.execute_button.grid(row=0, column=2)

        self._build_requirement_step()
        self._build_settings_step()
        self._build_content_step()
        self.show_step(0)

    def make_checkbutton(self, parent, text, variable):
        label = tk.Label(
            parent,
            cursor="hand2",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 10),
            padx=2,
            pady=2,
        )

        def render(*args):
            label.configure(text=f"{'☑' if variable.get() else '☐'} {text}")

        def toggle(event=None):
            variable.set(not variable.get())
            render()

        label.bind("<Button-1>", toggle)
        variable.trace_add("write", render)
        render()
        return label

    def _build_requirement_step(self):
        frame = ttk.Frame(self.step_container, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(2, weight=1)
        frame.rowconfigure(4, weight=1)
        frame.columnconfigure(0, weight=1)
        self.step_frames.append(frame)

        header = ttk.Frame(frame, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="选择父级需求", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        self.current_user_label = ttk.Label(header, text=self.current_user_text(), style="Muted.Panel.TLabel")
        self.current_user_label.grid(row=0, column=1, sticky="e", padx=(12, 10))
        ttk.Button(header, text="刷新需求", command=self.refresh_requirements, style="Accent.TButton").grid(
            row=0, column=2, sticky="e"
        )
        ttk.Label(frame, text="日报会作为该需求下的任务创建。", style="Muted.Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 14)
        )

        search = ttk.Frame(frame, style="Panel.TFrame")
        search.grid(row=2, column=0, sticky="new")
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="关键词", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(search, textvariable=self.keyword_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(search, text="查询", command=self.load_requirements).grid(row=0, column=2)

        self.req_tree = ttk.Treeview(
            frame,
            columns=("id", "code", "title", "children", "assignee"),
            show="headings",
            height=12,
        )
        for col, text, width in [
            ("id", "ID", 70),
            ("code", "编号", 110),
            ("title", "标题", 430),
            ("children", "子任务", 70),
            ("assignee", "负责人ID", 80),
        ]:
            self.req_tree.heading(col, text=text)
            self.req_tree.column(col, width=width, anchor=tk.W)
        self.req_tree.grid(row=3, column=0, sticky="nsew", pady=(12, 12))
        self.req_tree.bind("<<TreeviewSelect>>", self.on_requirement_select)

        self.detail_text = tk.Text(
            frame,
            height=10,
            wrap=tk.WORD,
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            relief=tk.FLAT,
            bd=1,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        self.detail_text.grid(row=4, column=0, sticky="nsew")

    def _build_settings_step(self):
        frame = ttk.Frame(self.step_container, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        self.step_frames.append(frame)

        ttk.Label(frame, text="负责人和日报配置", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="设置每条日报共用的负责人、状态、工时和创建选项。", style="Muted.Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 18)
        )

        layout = ttk.Frame(frame, style="Panel.TFrame")
        layout.grid(row=2, column=0, sticky="nsew")
        layout.columnconfigure(0, weight=1)
        layout.columnconfigure(1, weight=1)
        layout.rowconfigure(0, weight=1)

        left_card = ttk.LabelFrame(layout, text="日报基础信息", padding=18, style="Card.TLabelframe")
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_card.columnconfigure(1, weight=1)

        ttk.Label(left_card, text="日期", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(left_card, textvariable=self.date_var, width=18).grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Label(left_card, text="条数", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
        count_row = ttk.Frame(left_card, style="Panel.TFrame")
        count_row.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(14, 0))
        self.count_combo = ttk.Combobox(
            count_row,
            textvariable=self.manual_count_var,
            values=("4", "5"),
            state="readonly",
            width=8,
        )
        self.count_combo.pack(side=tk.LEFT)
        ttk.Label(count_row, text="条", style="Panel.TLabel").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(left_card, text="负责人", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 0))
        self.assignee_combo = ttk.Combobox(left_card, textvariable=self.assignee_var, values=[], state="normal")
        self.assignee_combo.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=(14, 0))

        ttk.Label(left_card, text="状态", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(14, 0))
        self.state_combo = ttk.Combobox(
            left_card,
            textvariable=self.state_var,
            values=[f"{state_id} - {name}" for name, state_id in DEFAULT_STATES],
            state="normal",
            width=18,
        )
        self.state_combo.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=(14, 0))

        right_card = ttk.LabelFrame(layout, text="工时和自动设置", padding=18, style="Card.TLabelframe")
        right_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_card.columnconfigure(1, weight=1)

        ttk.Label(right_card, text="预估工时", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(right_card, textvariable=self.estimate_var, width=12).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(right_card, text="登记工时", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
        ttk.Entry(right_card, textvariable=self.report_var, width=12).grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(14, 0))

        ttk.Label(right_card, text="随机创建间隔", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 0))
        interval_row = ttk.Frame(right_card, style="Panel.TFrame")
        interval_row.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(14, 0))
        ttk.Spinbox(interval_row, from_=0, to=3600, textvariable=self.interval_min_var, width=6).pack(side=tk.LEFT)
        ttk.Label(interval_row, text=" - ", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(interval_row, from_=0, to=3600, textvariable=self.interval_max_var, width=6).pack(side=tk.LEFT)
        ttk.Label(interval_row, text="秒", style="Panel.TLabel").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(right_card, text="创建后自动设置", style="Panel.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(18, 8))
        actions = ttk.Frame(right_card, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.make_checkbutton(actions, "负责人", self.set_assignee_var).pack(side=tk.LEFT)
        self.make_checkbutton(actions, "时间", self.set_time_var).pack(side=tk.LEFT, padx=12)
        self.make_checkbutton(actions, "状态", self.set_state_var).pack(side=tk.LEFT)
        self.make_checkbutton(actions, "预估工时", self.set_estimate_var).pack(side=tk.LEFT, padx=12)
        self.make_checkbutton(actions, "登记工时", self.register_workload_var).pack(side=tk.LEFT)

        ttk.Label(right_card, text="异常处理", style="Panel.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(22, 8))
        self.make_checkbutton(right_card, "某条失败后继续创建后续日报", self.continue_on_error_var).grid(
            row=6, column=0, columnspan=2, sticky="w"
        )

        hint = ttk.LabelFrame(frame, text="时间规则", padding=16, style="Card.TLabelframe")
        hint.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(
            hint,
            text="5 条：09:00-11:00 / 11:00-14:30 / 14:30-16:30 / 16:30-18:30 / 18:30-20:30",
            style="Panel.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            hint,
            text="4 条：使用前四个时间段，截止到 18:30。日期默认明天，可手动修改。",
            style="Panel.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _build_content_step(self):
        frame = ttk.Frame(self.step_container, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(2, weight=0)
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)
        self.step_frames.append(frame)

        ttk.Label(frame, text="填写日报并预览", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="每行一条日报，生成预览后再确认执行。", style="Muted.Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 14)
        )

        work_area = ttk.Frame(frame, style="Panel.TFrame")
        work_area.grid(row=2, column=0, sticky="nsew")
        work_area.columnconfigure(0, weight=1)
        work_area.columnconfigure(1, weight=1)
        work_area.rowconfigure(0, weight=1)

        content_box = ttk.LabelFrame(work_area, text="日报内容", padding=12, style="Card.TLabelframe")
        content_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        content_box.rowconfigure(0, weight=1)
        content_box.columnconfigure(0, weight=1)
        self.title_text = tk.Text(
            content_box,
            height=9,
            wrap=tk.WORD,
            bg=self.colors["panel_alt"],
            fg=self.colors["text"],
            relief=tk.FLAT,
            bd=1,
            padx=10,
            pady=8,
            insertbackground=self.colors["primary"],
            font=("Microsoft YaHei UI", 9),
        )
        self.title_text.grid(row=0, column=0, sticky="nsew")
        self.title_text.bind("<<Modified>>", self.on_title_modified)

        preview_box = ttk.LabelFrame(work_area, text="日报预览", padding=12, style="Card.TLabelframe")
        preview_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        preview_box.rowconfigure(0, weight=1)
        preview_box.columnconfigure(0, weight=1)

        self.preview_tree = ttk.Treeview(
            preview_box,
            columns=("no", "title", "start", "due", "state", "estimate", "report"),
            show="headings",
            height=7,
        )
        for col, text, width in [
            ("no", "序号", 42),
            ("title", "日报内容", 220),
            ("start", "开始时间", 126),
            ("due", "结束时间", 126),
            ("state", "状态", 82),
            ("estimate", "预估", 54),
            ("report", "登记", 54),
        ]:
            self.preview_tree.heading(col, text=text)
            self.preview_tree.column(col, width=width, anchor=tk.W)
        preview_xscroll = ttk.Scrollbar(preview_box, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        self.preview_tree.configure(xscrollcommand=preview_xscroll.set)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_xscroll.grid(row=1, column=0, sticky="ew")

        log_box = ttk.LabelFrame(frame, text="执行日志", padding=10, style="Card.TLabelframe")
        log_box.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        log_box.rowconfigure(1, weight=1)
        log_box.columnconfigure(0, weight=1)
        ttk.Label(log_box, text="日志只显示本次操作结果；脚本不会自动测试接口。", style="Panel.TLabel").pack(anchor="w")
        log_scroll_frame = ttk.Frame(log_box, style="Panel.TFrame")
        log_scroll_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        log_scroll_frame.rowconfigure(0, weight=1)
        log_scroll_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_scroll_frame,
            height=13,
            wrap=tk.WORD,
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            relief=tk.FLAT,
            padx=10,
            pady=8,
            insertbackground="#ffffff",
            font=("Consolas", 9),
        )
        log_xscroll = ttk.Scrollbar(log_scroll_frame, orient=tk.HORIZONTAL, command=self.log_text.xview)
        log_yscroll = ttk.Scrollbar(log_scroll_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(xscrollcommand=log_xscroll.set, yscrollcommand=log_yscroll.set, wrap=tk.NONE)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_yscroll.grid(row=0, column=1, sticky="ns")
        log_xscroll.grid(row=1, column=0, sticky="ew")

    def show_step(self, index):
        self.current_step = max(0, min(index, len(self.step_frames) - 1))
        for frame in self.step_frames:
            frame.grid_remove()
        self.step_frames[self.current_step].grid()
        if self.current_step == 2:
            self.auto_generate_preview()
        for idx, label in enumerate(self.step_labels):
            if idx == self.current_step:
                label.configure(style="SidebarActive.TLabel")
            elif idx < self.current_step:
                label.configure(style="SidebarDone.TLabel")
            else:
                label.configure(style="SidebarIdle.TLabel")
        if self.creation_in_progress:
            self.back_button.configure(state=tk.DISABLED)
            self.next_button.configure(state=tk.DISABLED)
            self.execute_button.configure(state=tk.DISABLED)
            return
        self.back_button.configure(state=tk.NORMAL if self.current_step > 0 else tk.DISABLED)
        if self.current_step == len(self.step_frames) - 1:
            self.next_button.grid_remove()
            self.execute_button.grid()
            self.execute_button.configure(state=tk.NORMAL if self.generated_rows else tk.DISABLED)
        else:
            self.execute_button.grid_remove()
            self.next_button.grid()
            next_enabled = True
            if self.current_step == 0:
                next_enabled = bool(self.selected_requirement_id())
            self.next_button.configure(state=tk.NORMAL if next_enabled else tk.DISABLED)

    def previous_step(self):
        self.show_step(self.current_step - 1)

    def next_step(self):
        if self.current_step == 0 and not self.selected_requirement_id():
            self.show_centered_alert("无法继续", "请先选择一个需求。")
            return
        if self.current_step == 1:
            try:
                self.parse_count()
                self.parse_interval_range()
                if self.set_assignee_var.get():
                    self.parse_id(self.assignee_var.get(), "负责人")
                if self.set_state_var.get():
                    self.parse_id(self.state_var.get(), "状态")
                float(self.estimate_var.get())
                float(self.report_var.get())
            except Exception as exc:
                self.show_centered_alert("配置不完整", str(exc))
                return
        self.show_step(self.current_step + 1)

    def open_config_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("接口配置")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors["bg"])

        values = {
            "base_url": tk.StringVar(value=self.base_url_var.get()),
            "project_id": tk.StringVar(value=self.project_id_var.get()),
            "tenant_id": tk.StringVar(value=self.tenant_id_var.get()),
            "token": tk.StringVar(value=self.token_var.get()),
            "user_id": tk.StringVar(value=self.user_id_var.get()),
            "nickname": tk.StringVar(value=self.nickname_var.get()),
            "dept_id": tk.StringVar(value=self.dept_id_var.get()),
            "dept_name": tk.StringVar(value=self.dept_name_var.get()),
            "post_names": tk.StringVar(value=self.post_names_var.get()),
        }

        panel = ttk.Frame(dialog, padding=18, style="Panel.TFrame")
        panel.pack(fill=tk.BOTH, expand=True)
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="接口配置", style="Header.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(panel, text="保存后会写入本地配置文件。", style="Muted.Panel.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 16)
        )

        rows = [
            ("Base URL", "base_url", 52, False),
            ("Project ID", "project_id", 16, False),
            ("Tenant ID", "tenant_id", 16, False),
            ("Authorization", "token", 52, True),
        ]
        for row_index, (label, key, width, secret) in enumerate(rows, start=2):
            ttk.Label(panel, text=label, style="Panel.TLabel").grid(row=row_index, column=0, sticky="w", pady=6)
            ttk.Entry(panel, textvariable=values[key], width=width, show="*" if secret else "").grid(
                row=row_index, column=1, sticky="ew", padx=(10, 0), pady=6
            )

        user_box = ttk.LabelFrame(panel, text="当前登录人", padding=12, style="Card.TLabelframe")
        user_box.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        user_box.columnconfigure(1, weight=1)
        user_rows = [
            ("用户 ID", "user_id"),
            ("昵称", "nickname"),
            ("部门", "dept_name"),
            ("岗位", "post_names"),
        ]
        for row_index, (label, key) in enumerate(user_rows):
            ttk.Label(user_box, text=label, style="Panel.TLabel").grid(row=row_index, column=0, sticky="w", pady=4)
            ttk.Entry(user_box, textvariable=values[key], state="readonly").grid(
                row=row_index, column=1, sticky="ew", padx=(10, 0), pady=4
            )

        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def apply_config_values(config):
            self.base_url_var.set(config["base_url"])
            self.project_id_var.set(config["project_id"])
            self.tenant_id_var.set(config["tenant_id"])
            self.token_var.set(config["token"])
            self.user_id_var.set(config.get("user_id", ""))
            self.nickname_var.set(config.get("nickname", ""))
            self.dept_id_var.set(config.get("dept_id", ""))
            self.dept_name_var.set(config.get("dept_name", ""))
            self.post_names_var.set(config.get("post_names", ""))
            self.update_current_user_label()

        def save_config_from_dialog():
            config = {key: value.get().strip() for key, value in values.items()}
            if not config["base_url"] or not config["project_id"] or not config["tenant_id"] or not config["token"]:
                self.show_centered_alert("保存失败", "接口配置不能为空。")
                return
            apply_config_values(config)
            save_app_config(config)
            self.append_log("接口配置已保存")
            dialog.destroy()
            self.load_initial_data()

        def fetch_profile():
            config = {key: value.get().strip() for key, value in values.items()}
            if not config["base_url"] or not config["tenant_id"] or not config["token"]:
                self.show_centered_alert("获取失败", "请先填写 Base URL、Tenant ID 和 Authorization。")
                return
            try:
                api = ApiClient(config["base_url"], config["token"], principal_id=config["project_id"], tenant_id=config["tenant_id"])
                profile = api.get_profile()
                dept = profile.get("dept") or {}
                posts = profile.get("posts") or []
                values["user_id"].set(str(profile.get("id", "")))
                values["nickname"].set(str(profile.get("nickname", "")))
                values["dept_id"].set(str(dept.get("id", "")))
                values["dept_name"].set(str(dept.get("name", "")))
                values["post_names"].set("、".join(str(post.get("name", "")) for post in posts if post.get("name")))
                self.show_centered_alert("获取成功", f"当前登录人：{values['nickname'].get()}（ID: {values['user_id'].get()}）")
            except Exception as exc:
                self.show_centered_alert("获取失败", str(exc))

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="保存", command=save_config_from_dialog, style="Accent.TButton").pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        ttk.Button(buttons, text="获取当前登录人", command=fetch_profile).pack(side=tk.RIGHT, padx=(0, 8))
        self.center_dialog(dialog)
        dialog.wait_window()

    def open_history_dialog(self):
        if self.focus_existing_dialog("history_dialog"):
            return
        dialog = tk.Toplevel(self)
        self.history_dialog = dialog
        dialog.title("历史日报记录")
        dialog.geometry("1050x620")
        dialog.transient(self)
        dialog.configure(bg=self.colors["bg"])
        dialog.protocol("WM_DELETE_WINDOW", lambda: self.close_tracked_dialog("history_dialog"))

        date_filter = tk.StringVar()
        keyword_filter = tk.StringVar()
        rows = load_history_records()

        root = ttk.Frame(dialog, padding=14, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)

        ttk.Label(root, text="历史日报记录", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text=f"本地日志：{HISTORY_PATH}", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 12))

        filters = ttk.Frame(root, style="App.TFrame")
        filters.grid(row=2, column=0, sticky="new")
        filters.columnconfigure(3, weight=1)
        ttk.Label(filters, text="日期", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(filters, textvariable=date_filter, width=14).grid(row=0, column=1, padx=(6, 12))
        ttk.Label(filters, text="关键词", style="Subtle.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(filters, textvariable=keyword_filter).grid(row=0, column=3, sticky="ew", padx=(6, 12))

        table = ttk.Treeview(
            root,
            columns=("created", "date", "id", "title", "requirement", "assignee", "state", "result"),
            show="headings",
            height=16,
        )
        headings = [
            ("created", "创建时间", 140),
            ("date", "日报日期", 90),
            ("id", "任务ID", 80),
            ("title", "日报内容", 300),
            ("requirement", "父需求", 180),
            ("assignee", "负责人", 90),
            ("state", "状态", 80),
            ("result", "结果", 80),
        ]
        for column, text, width in headings:
            table.heading(column, text=text)
            table.column(column, width=width, anchor=tk.W)
        table.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        root.rowconfigure(3, weight=1)

        detail = tk.Text(
            root,
            height=5,
            bg="#ffffff",
            fg=self.colors["text"],
            relief=tk.FLAT,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        detail.grid(row=4, column=0, sticky="ew", pady=(10, 0))

        visible_records = []

        def record_matches(record):
            date_text = date_filter.get().strip()
            keyword = keyword_filter.get().strip().lower()
            if date_text and date_text not in str(record.get("register_date", "")):
                return False
            if keyword:
                haystack = " ".join(
                    str(record.get(key, ""))
                    for key in ("title", "task_id", "parent_title", "parent_code", "assignee_name", "state_name")
                ).lower()
                if keyword not in haystack:
                    return False
            return True

        def refresh():
            visible_records.clear()
            for item in table.get_children():
                table.delete(item)
            for record in reversed(rows):
                if not record_matches(record):
                    continue
                visible_records.append(record)
                table.insert(
                    "",
                    tk.END,
                    values=(
                        record.get("created_at", ""),
                        record.get("register_date", ""),
                        record.get("task_id", ""),
                        record.get("title", ""),
                        record.get("parent_title") or record.get("parent_code", ""),
                        record.get("assignee_name") or record.get("assignee_id", ""),
                        record.get("state_name") or record.get("state_id", ""),
                        record.get("result", ""),
                    ),
                )

        def show_selected_detail(event=None):
            selected = table.selection()
            if not selected:
                return
            index = table.index(selected[0])
            if index >= len(visible_records):
                return
            record = visible_records[index]
            detail.delete("1.0", tk.END)
            detail.insert(tk.END, json.dumps(record, ensure_ascii=False, indent=2))

        ttk.Button(filters, text="查询", command=refresh).grid(row=0, column=4)
        table.bind("<<TreeviewSelect>>", show_selected_detail)
        refresh()
        self.center_dialog(dialog)
        dialog.wait_window()
        if self.history_dialog is dialog:
            self.history_dialog = None

    def open_all_reports_dialog(self):
        if self.focus_existing_dialog("all_reports_dialog"):
            return
        if not self.user_id_var.get().strip():
            self.show_centered_alert("无法查询", "请先在全局配置中点击“获取当前登录人”，保存后再查询全部日报。")
            return

        dialog = tk.Toplevel(self)
        self.all_reports_dialog = dialog
        dialog.withdraw()
        dialog.title("全部日报")
        dialog.geometry("980x560")
        dialog.transient(self)
        dialog.configure(bg=self.colors["bg"])
        dialog.protocol("WM_DELETE_WINDOW", lambda: self.close_tracked_dialog("all_reports_dialog"))

        keyword_var = tk.StringVar()
        page_no_var = tk.IntVar(value=1)
        page_size_var = tk.IntVar(value=20)
        total_var = tk.StringVar(value="共 0 条")
        page_info_var = tk.StringVar(value="第 1 / 1 页")
        records = []

        root = ttk.Frame(dialog, padding=18, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)
        root.rowconfigure(3, weight=1)
        root.columnconfigure(0, weight=1)

        title = f"全部日报 - {self.nickname_var.get() or self.user_id_var.get()}"
        ttk.Label(root, text=title, style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="查询当前登录人负责的日报/任务数据。",
            style="Subtle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        toolbar = ttk.LabelFrame(root, text="查询条件", padding=12, style="Card.TLabelframe")
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="关键词", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(toolbar, textvariable=keyword_var).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        ttk.Button(toolbar, text="查询", command=lambda: search_reports(), style="Accent.TButton").grid(row=0, column=2)

        pager = ttk.Frame(toolbar, style="Panel.TFrame")
        pager.grid(row=0, column=3, sticky="e", padx=(20, 0))
        ttk.Button(pager, text="上一页", command=lambda: previous_page()).pack(side=tk.LEFT)
        ttk.Button(pager, text="下一页", command=lambda: next_page()).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(pager, textvariable=page_info_var, style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Label(pager, textvariable=total_var, style="Muted.Panel.TLabel").pack(side=tk.LEFT, padx=(10, 14))
        ttk.Label(pager, text="每页", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(pager, from_=5, to=100, textvariable=page_size_var, width=6).pack(side=tk.LEFT, padx=(6, 0))

        table_card = ttk.LabelFrame(root, text="查询结果", padding=10, style="Card.TLabelframe")
        table_card.grid(row=3, column=0, sticky="nsew")
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)
        table_frame = ttk.Frame(table_card, style="Panel.TFrame")
        table_frame.grid(row=0, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        table = ttk.Treeview(
            table_frame,
            columns=("code", "type", "leader_review", "title", "parent", "start", "due", "state"),
            show="headings",
            height=12,
        )
        headings = [
            ("code", "编号", 100),
            ("type", "类型", 70),
            ("leader_review", "组长审核", 90),
            ("title", "标题", 320),
            ("parent", "父需求", 260),
            ("start", "开始时间", 145),
            ("due", "结束时间", 145),
            ("state", "状态", 90),
        ]
        for column, text, width in headings:
            table.heading(column, text=text)
            table.column(column, width=width, anchor=tk.W)
        table.tag_configure("review_yes", background="#ecfdf3", foreground="#05603a")
        table.tag_configure("review_no", background="#fef3f2", foreground="#912018")
        xscroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=table.xview)
        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=table.yview)
        table.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        def date_value(value):
            if isinstance(value, dict):
                return value.get("date") or ""
            return value or ""

        def item_type(row):
            if row.get("typeGroup") == 6 or row.get("typeId") == 5 or row.get("icon") == "icon-defect":
                return "缺陷"
            return "任务"

        def leader_review(row):
            properties = row.get("properties") or {}
            return "有" if properties.get("zuchangshenhe") else "无"

        def fill_table(items):
            records.clear()
            for item in table.get_children():
                table.delete(item)
            for row in items:
                records.append(row)
                has_review = leader_review(row) == "有"
                table.insert(
                    "",
                    tk.END,
                    tags=("review_yes" if has_review else "review_no",),
                    values=(
                        row.get("code", ""),
                        item_type(row),
                        "有" if has_review else "无",
                        row.get("title", ""),
                        row.get("parentTitle") or "",
                        date_value(row.get("start")),
                        date_value(row.get("due")),
                        self.state_name_by_id(row.get("stateId", "")),
                    ),
                )

        def max_page(total, page_size):
            if page_size <= 0:
                return 1
            return max((total + page_size - 1) // page_size, 1)

        def load_reports():
            try:
                api = self.client()
                current_page = max(int(page_no_var.get()), 1)
                current_size = max(int(page_size_var.get()), 1)
                page_no_var.set(current_page)
                page_size_var.set(current_size)
                items, total = api.list_my_reports(
                    self.project_id_var.get().strip(),
                    self.user_id_var.get().strip(),
                    keyword=keyword_var.get().strip(),
                    page_no=current_page,
                    page_size=current_size,
                )
                fill_table(items)
                total_pages = max_page(total, current_size)
                total_var.set(f"共 {total} 条")
                page_info_var.set(f"第 {current_page} / {total_pages} 页")
            except Exception as exc:
                self.show_centered_alert("查询失败", str(exc))

        def search_reports():
            page_no_var.set(1)
            load_reports()

        def previous_page():
            current_page = max(int(page_no_var.get()), 1)
            if current_page <= 1:
                return
            page_no_var.set(current_page - 1)
            load_reports()

        def next_page():
            current_page = max(int(page_no_var.get()), 1)
            text = page_info_var.get()
            match = re.search(r"/\s*(\d+)", text)
            total_pages = int(match.group(1)) if match else current_page + 1
            if current_page >= total_pages:
                return
            page_no_var.set(current_page + 1)
            load_reports()

        self.center_dialog(dialog)
        dialog.deiconify()
        dialog.lift()
        dialog.after(50, load_reports)
        dialog.wait_window()
        if self.all_reports_dialog is dialog:
            self.all_reports_dialog = None

    def ask_centered_confirmation(self, title, message, confirm_text="确认执行"):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors["bg"])

        result = {"confirmed": False}
        panel = ttk.Frame(dialog, padding=18, style="Panel.TFrame")
        panel.pack(fill=tk.BOTH, expand=True)
        ttk.Label(panel, text=title, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text=message,
            style="Panel.TLabel",
            justify=tk.LEFT,
            wraplength=560,
        ).pack(anchor="w", pady=(14, 18))

        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.pack(anchor="e")

        def cancel():
            dialog.destroy()

        def confirm():
            result["confirmed"] = True
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=confirm_text, command=confirm, style="Accent.TButton").pack(side=tk.RIGHT, padx=(0, 8))
        dialog.bind("<Escape>", lambda event: cancel())
        dialog.bind("<Return>", lambda event: confirm())
        self.center_dialog(dialog)
        dialog.wait_window()
        return result["confirmed"]

    def show_centered_alert(self, title, message):
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors["bg"])

        panel = ttk.Frame(dialog, padding=18, style="Panel.TFrame")
        panel.pack(fill=tk.BOTH, expand=True)
        ttk.Label(panel, text=title, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text=message,
            style="Panel.TLabel",
            justify=tk.LEFT,
            wraplength=560,
        ).pack(anchor="w", pady=(14, 18))

        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="知道了", command=dialog.destroy, style="Accent.TButton").pack(side=tk.RIGHT)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.bind("<Return>", lambda event: dialog.destroy())
        self.center_dialog(dialog)
        dialog.wait_window()

    def center_dialog(self, dialog):
        dialog.update_idletasks()
        self.update_idletasks()

        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_width = self.winfo_width()
        parent_height = self.winfo_height()

        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = parent_x + max((parent_width - width) // 2, 0)
        y = parent_y + max((parent_height - height) // 2, 0)
        dialog.geometry(f"+{x}+{y}")

    def focus_existing_dialog(self, attr_name):
        dialog = getattr(self, attr_name, None)
        if dialog is None or not dialog.winfo_exists():
            setattr(self, attr_name, None)
            return False
        dialog.deiconify()
        dialog.lift()
        dialog.focus_force()
        return True

    def close_tracked_dialog(self, attr_name):
        dialog = getattr(self, attr_name, None)
        setattr(self, attr_name, None)
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()

    def current_user_text(self):
        nickname = self.nickname_var.get().strip()
        user_id = self.user_id_var.get().strip()
        if nickname and user_id:
            return f"当前登录人：{nickname}（ID: {user_id}）"
        if nickname:
            return f"当前登录人：{nickname}"
        if user_id:
            return f"当前登录人：ID {user_id}"
        return "当前登录人：未获取"

    def update_current_user_label(self):
        label = getattr(self, "current_user_label", None)
        if label is not None:
            label.configure(text=self.current_user_text())

    def refresh_requirements(self):
        self.keyword_var.set("")
        self.load_requirements()

    def on_close_request(self):
        if self.creation_in_progress:
            self.show_centered_alert("正在创建日报", "日报创建过程中请不要关闭软件，等待执行日志提示完成后再关闭。")
            return
        self.destroy()

    def client(self):
        self.api = ApiClient(
            self.base_url_var.get(),
            self.token_var.get(),
            principal_id=self.project_id_var.get(),
            tenant_id=self.tenant_id_var.get(),
        )
        return self.api

    def has_required_config(self):
        return all(
            value.strip()
            for value in (
                self.base_url_var.get(),
                self.token_var.get(),
                self.project_id_var.get(),
                self.tenant_id_var.get(),
            )
        )

    def run_worker(self, title, func):
        def wrapper():
            self.queue_log(f"[{title}] 开始")
            try:
                func()
            except Exception as exc:
                self.queue_error(str(exc))
            finally:
                self.queue_log(f"[{title}] 结束")

        threading.Thread(target=wrapper, daemon=True).start()

    def queue_log(self, text):
        self.ui_queue.put(("log", text))

    def queue_error(self, text):
        self.ui_queue.put(("error", text))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "error":
                    self.append_log("[错误] " + payload)
                    self.show_centered_alert("操作失败", payload)
                elif kind == "requirements":
                    self.fill_requirements(payload)
                elif kind == "members":
                    self.fill_members(payload)
                elif kind == "detail":
                    self.fill_detail(payload)
                elif kind == "update_checked":
                    self.handle_update_checked(payload)
                elif kind == "update_downloaded":
                    self.handle_update_downloaded(payload)
                elif kind == "creation_finished":
                    self.after_creation_finished()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def check_for_updates(self):
        repo = GITHUB_UPDATE_REPO
        asset_keyword = DEFAULT_UPDATE_ASSET_KEYWORD

        def work():
            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            release = request_json(api_url, headers=github_headers(), timeout=20)
            latest_version = str(release.get("tag_name") or release.get("name") or "").strip()
            assets = release.get("assets") or []
            asset = self.find_update_asset(assets, asset_keyword)
            self.ui_queue.put(
                (
                    "update_checked",
                    {
                        "repo": repo,
                        "release": release,
                        "version": latest_version,
                        "asset": asset,
                        "asset_keyword": asset_keyword,
                    },
                )
            )

        self.run_worker("检查更新", work)

    def find_update_asset(self, assets, asset_keyword):
        keyword = asset_keyword.lower()
        exe_assets = [
            asset
            for asset in assets
            if str(asset.get("name") or "").lower().endswith(".exe")
            and (not keyword or keyword in str(asset.get("name") or "").lower())
        ]
        if exe_assets:
            return exe_assets[0]
        fallback_assets = [
            asset for asset in assets if str(asset.get("name") or "").lower().endswith(".exe")
        ]
        return fallback_assets[0] if fallback_assets else None

    def handle_update_checked(self, payload):
        latest_version = payload["version"]
        release = payload["release"]
        asset = payload["asset"]
        if not latest_version:
            self.show_centered_alert("检查更新失败", "GitHub Release 没有可识别的版本号。")
            return
        if not is_newer_version(latest_version, APP_VERSION):
            self.show_centered_alert(
                "当前已是最新版本",
                f"当前版本：{APP_VERSION}\n最新版本：{latest_version}",
            )
            return
        if not asset:
            self.show_centered_alert(
                "发现新版本",
                f"最新版本：{latest_version}\n但 Release 附件里没有匹配的 exe 文件。\n"
                f"当前匹配关键词：{payload['asset_keyword']}",
            )
            return

        notes = str(release.get("body") or "").strip()
        if len(notes) > 700:
            notes = notes[:700] + "\n..."
        message = (
            f"当前版本：{APP_VERSION}\n"
            f"最新版本：{latest_version}\n"
            f"下载文件：{asset.get('name')}\n\n"
            f"更新内容：\n{notes or '本次 Release 未填写更新说明。'}\n\n"
            "是否立即下载？"
        )
        if not self.ask_centered_confirmation("发现新版本", message, confirm_text="立即下载"):
            return
        self.download_update(release, asset, latest_version)

    def download_update(self, release, asset, latest_version):
        def work():
            download_url = asset.get("browser_download_url")
            if not download_url:
                raise RuntimeError("GitHub Release 附件没有下载地址。")
            file_name = Path(str(asset.get("name") or f"daily-report-tool-{latest_version}.exe")).name
            target_path = APP_DIR / "updates" / latest_version.replace("/", "_") / file_name
            self.queue_log(f"正在下载更新：{file_name}")
            download_url_to_file(download_url, target_path, headers=github_headers(), timeout=120)
            expected_sha256 = extract_asset_sha256(asset, str(release.get("body") or ""))
            if expected_sha256:
                actual_sha256 = sha256_file(target_path)
                if actual_sha256.lower() != expected_sha256.lower():
                    try:
                        target_path.unlink()
                    except OSError:
                        pass
                    raise RuntimeError("更新文件校验失败，已删除下载文件。")
            self.ui_queue.put(
                (
                    "update_downloaded",
                    {
                        "path": str(target_path),
                        "version": latest_version,
                        "sha256_checked": bool(expected_sha256),
                    },
                )
            )

        self.run_worker("下载更新", work)

    def handle_update_downloaded(self, payload):
        update_path = Path(payload["path"])
        checked_text = "已完成 SHA256 校验。" if payload["sha256_checked"] else "Release 未提供 SHA256，已跳过校验。"
        if not getattr(sys, "frozen", False):
            self.show_centered_alert(
                "更新已下载",
                f"版本：{payload['version']}\n文件：{update_path}\n{checked_text}\n\n"
                "当前是脚本运行模式，不会自动覆盖源码。打包成 exe 后会自动替换并重启。",
            )
            return
        if self.ask_centered_confirmation(
            "更新已下载",
            f"版本：{payload['version']}\n{checked_text}\n\n程序将关闭并替换为新版本，然后自动重启。",
            confirm_text="立即重启更新",
        ):
            self.apply_update_and_restart(update_path)

    def apply_update_and_restart(self, update_path):
        target_path = Path(sys.executable).resolve()
        script_path = APP_DIR / "updates" / "apply_update.bat"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script = f"""@echo off
chcp 65001 >nul
set "SOURCE={update_path}"
set "TARGET={target_path}"
:retry
timeout /t 1 /nobreak >nul
copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 goto retry
timeout /t 2 /nobreak >nul
start "" "%TARGET%"
del "%SOURCE%" >nul 2>nul
del "%~f0" >nul 2>nul
"""
        script_path.write_text(script, encoding="utf-8-sig")
        subprocess.Popen(
            ["cmd", "/c", str(script_path)],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.destroy()

    def load_initial_data(self):
        if not self.has_required_config():
            self.append_log("全局配置未完成，请先在顶部菜单打开“全局配置”并保存。")
            return
        self.load_requirements()
        self.load_members()

    def append_log(self, text):
        self.log_text.insert(tk.END, f"{dt.datetime.now():%H:%M:%S} {text}\n")
        self.log_text.see(tk.END)

    def on_preview_config_changed(self, *args):
        if getattr(self, "current_step", None) == 2:
            self.auto_generate_preview()
            self.show_step(self.current_step)

    def on_title_modified(self, event=None):
        if self.title_text.edit_modified():
            self.title_text.edit_modified(False)
            self.auto_generate_preview()
            self.show_step(self.current_step)

    def load_requirements(self):
        if not self.has_required_config():
            self.show_centered_alert("配置不完整", "请先在“全局配置”中填写并保存接口配置。")
            return
        project_id = self.project_id_var.get().strip()
        keyword = self.keyword_var.get().strip()

        def work():
            items = self.client().list_requirements(project_id, keyword=keyword)
            self.ui_queue.put(("requirements", items))
            self.queue_log(f"已加载需求 {len(items)} 条")

        self.run_worker("加载需求", work)

    def fill_requirements(self, items):
        self.requirements = {}
        for row in self.req_tree.get_children():
            self.req_tree.delete(row)
        for item in items:
            item_id = item.get("id")
            self.requirements[str(item_id)] = item
            self.req_tree.insert(
                "",
                tk.END,
                iid=str(item_id),
                values=(
                    item_id,
                    item.get("code", ""),
                    item.get("title", ""),
                    item.get("childrenCount", ""),
                    item.get("assignee", ""),
                ),
            )
        self.show_step(self.current_step)

    def load_members(self):
        if not self.has_required_config():
            self.show_centered_alert("配置不完整", "请先在“全局配置”中填写并保存接口配置。")
            return
        project_id = self.project_id_var.get().strip()

        def work():
            members = self.client().list_members(project_id)
            self.ui_queue.put(("members", members))
            self.queue_log(f"已加载负责人 {len(members)} 个")

        self.run_worker("加载负责人", work)

    def fill_members(self, members):
        self.members = {str(item.get("id")): item for item in members}
        values = []
        current_user_value = ""
        for item in members:
            post = ",".join(item.get("postNames") or [])
            display = f"{item.get('id')} - {item.get('nickname', '')} - {post}"
            values.append(display)
            if str(item.get("id")) == self.user_id_var.get().strip():
                current_user_value = display
        self.assignee_combo["values"] = values
        if current_user_value:
            self.assignee_var.set(current_user_value)
        elif values and not self.assignee_var.get():
            self.assignee_var.set(values[0])

    def on_requirement_select(self, event=None):
        item_id = self.selected_requirement_id()
        self.show_step(self.current_step)
        if not item_id:
            return

        def work():
            detail = self.client().get_work_item(item_id)
            self.ui_queue.put(("detail", detail))
            self.queue_log(f"已查询需求详情 ID={item_id}")

        self.run_worker("查询详情", work)

    def fill_detail(self, detail):
        props = detail.get("properties") or {}
        lines = [
            f"ID: {detail.get('id')}",
            f"编号: {detail.get('code', '')}",
            f"标题: {detail.get('title', '')}",
            f"项目: {detail.get('projectName', '')} / projectId={detail.get('projectId', '')}",
            f"父级: {detail.get('parentTitle', '')} / parentId={detail.get('parentId', '')}",
            f"子任务数: {detail.get('childrenCount', '')}",
            f"负责人ID: {detail.get('assignee', '')}",
            f"状态ID: {detail.get('stateId', '')}",
            f"子任务预估合计: {self.workload_desc(props.get('estimatedWorkload_children'))}",
            "",
            "需求描述:",
            html_to_text(detail.get("description", ""))[:5000],
        ]
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, "\n".join(lines))

    @staticmethod
    def workload_desc(value):
        if isinstance(value, dict):
            return value.get("desc") or value.get("hour") or ""
        return value or ""

    def selected_requirement_id(self):
        selected = self.req_tree.selection()
        return selected[0] if selected else None

    def parse_count(self):
        date_text = self.date_var.get().strip()
        dt.datetime.strptime(date_text, "%Y-%m-%d")
        count = int(self.manual_count_var.get())
        if count not in (4, 5):
            raise ValueError("日报条数只能选择 4 条或 5 条。")
        return count

    def parse_interval_range(self):
        min_seconds = int(self.interval_min_var.get())
        max_seconds = int(self.interval_max_var.get())
        if min_seconds < 0 or max_seconds < 0:
            raise ValueError("随机创建间隔不能小于 0 秒。")
        if min_seconds > max_seconds:
            raise ValueError("随机创建间隔的最小值不能大于最大值。")
        return min_seconds, max_seconds

    def read_titles(self, count):
        raw = self.title_text.get("1.0", tk.END)
        titles = [line.strip() for line in raw.splitlines() if line.strip()]
        if not titles:
            raise ValueError("请先填写日报内容，每行一条。")
        if len(titles) < count:
            raise ValueError(f"当前需要 {count} 条日报，但只填写了 {len(titles)} 行内容。")
        return titles[:count]

    def preview_titles(self, count):
        raw = self.title_text.get("1.0", tk.END)
        titles = [line.strip() for line in raw.splitlines() if line.strip()]
        return titles[:count]

    def build_preview_rows(self, titles, count):
        date_text = self.date_var.get().strip()
        slots = build_slots(date_text, count)
        rows = []
        for index, title in enumerate(titles):
            rows.append(
                {
                    "title": title,
                    "start": slots[index]["start"],
                    "due": slots[index]["due"],
                    "estimate": float(self.estimate_var.get()),
                    "report": float(self.report_var.get()),
                    "register_date": date_text,
                }
            )
        return rows

    def auto_generate_preview(self):
        try:
            count = self.parse_count()
            titles = self.preview_titles(count)
            self.generated_rows = self.build_preview_rows(titles, count) if titles else []
            self.fill_preview()
        except Exception:
            self.generated_rows = []
            self.fill_preview()

    def generate_preview(self):
        try:
            self.generated_rows = []
            count = self.parse_count()
            titles = self.read_titles(count)
            self.generated_rows = self.build_preview_rows(titles, count)
            self.fill_preview()
            self.show_step(self.current_step)
            self.append_log(f"已生成预览 {count} 条")
            return True
        except Exception as exc:
            self.generated_rows = []
            self.fill_preview()
            self.show_step(self.current_step)
            self.show_centered_alert("生成预览失败", str(exc))
            return False

    def fill_preview(self):
        for row_id in self.preview_tree.get_children():
            self.preview_tree.delete(row_id)
        for index, row in enumerate(self.generated_rows, start=1):
            self.preview_tree.insert(
                "",
                tk.END,
                values=(
                    index,
                    row["title"],
                    row["start"],
                    row["due"],
                    self.parse_display_name(self.state_var.get()),
                    row["estimate"],
                    row["report"],
                ),
            )

    def after_creation_finished(self):
        self.creation_in_progress = False
        self.generated_rows = []
        self.title_text.delete("1.0", tk.END)
        self.fill_preview()
        self.show_step(0)
        self.append_log("已回到选择需求页面，可以继续准备下一次日报。")

    @staticmethod
    def parse_id(value, field_name):
        match = re.match(r"\s*(\d+)", value or "")
        if not match:
            raise ValueError(f"请填写或选择有效的{field_name} ID。")
        return int(match.group(1))

    @staticmethod
    def parse_display_name(value):
        parts = [part.strip() for part in (value or "").split("-")]
        return parts[1] if len(parts) > 1 else value

    @staticmethod
    def state_name_by_id(state_id):
        normalized = str(state_id)
        for name, item_id in DEFAULT_STATES:
            if str(item_id) == normalized:
                return name
        return normalized

    def execute_reports(self):
        parent_id = self.selected_requirement_id()
        if not parent_id:
            self.show_centered_alert("无法执行", "请先选择一个需求。")
            return
        if not self.generate_preview():
            return
        assignee_id = None
        state_id = None
        try:
            if self.set_assignee_var.get():
                assignee_id = self.parse_id(self.assignee_var.get(), "负责人")
            if self.set_state_var.get():
                state_id = self.parse_id(self.state_var.get(), "状态")
            interval_min, interval_max = self.parse_interval_range()
        except Exception as exc:
            self.show_centered_alert("无法执行", str(exc))
            return

        rows = list(self.generated_rows)
        project_id = self.project_id_var.get().strip()
        parent_info = self.requirements.get(str(parent_id), {})
        assignee_text = self.assignee_var.get()
        state_text = self.state_var.get()
        options = {
            "assignee_id": assignee_id,
            "assignee_name": self.parse_display_name(assignee_text),
            "state_id": state_id,
            "state_name": self.parse_display_name(state_text),
            "set_assignee": self.set_assignee_var.get(),
            "set_state": self.set_state_var.get(),
            "set_time": self.set_time_var.get(),
            "set_estimate": self.set_estimate_var.get(),
            "register_workload": self.register_workload_var.get(),
            "continue_on_error": self.continue_on_error_var.get(),
            "interval_min": interval_min,
            "interval_max": interval_max,
        }

        confirm_lines = [
            "即将真实写入公司平台，请确认：",
            "",
            f"父需求：{parent_info.get('code', '')} {parent_info.get('title', '')}".strip(),
            f"日报日期：{rows[0]['register_date'] if rows else self.date_var.get().strip()}",
            f"创建条数：{len(rows)} 条",
            f"负责人：{options['assignee_name'] if options['set_assignee'] else '不设置'}",
            f"状态：{options['state_name'] if options['set_state'] else '不设置'}",
            f"预估工时：{rows[0]['estimate'] if rows else ''}",
            f"登记工时：{rows[0]['report'] if rows else ''}",
            f"随机创建间隔：{interval_min} - {interval_max} 秒",
            "",
            "创建过程中请不要关闭软件。",
            "是否确认执行？",
        ]
        if not self.ask_centered_confirmation("二次确认", "\n".join(confirm_lines)):
            return
        self.creation_in_progress = True
        self.show_step(self.current_step)

        def save_history(row, created_id, result, error_message=""):
            record = {
                "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "result": result,
                "error": error_message,
                "task_id": created_id or "",
                "title": row["title"],
                "project_id": project_id,
                "tenant_id": self.tenant_id_var.get().strip(),
                "parent_id": int(parent_id),
                "parent_code": parent_info.get("code", ""),
                "parent_title": parent_info.get("title", ""),
                "start": row["start"],
                "due": row["due"],
                "register_date": row["register_date"],
                "estimated_workload": row["estimate"],
                "reported_workload": row["report"],
                "assignee_id": options["assignee_id"] or "",
                "assignee_name": options["assignee_name"] if options["set_assignee"] else "",
                "state_id": options["state_id"] or "",
                "state_name": options["state_name"] if options["set_state"] else "",
            }
            append_history_record(record)

        def work():
            try:
                api = self.client()
                for index, row in enumerate(rows, start=1):
                    created_id = None
                    try:
                        self.queue_log(f"第 {index} 条创建中: {row['title']}")
                        create_preview = {
                            "projectId": str(project_id),
                            "parentId": int(parent_id),
                            "title": row["title"],
                            "type": DEFAULT_TASK_TYPE,
                        }
                        self.queue_log("新建任务参数: " + json.dumps(create_preview, ensure_ascii=False))
                        created = api.create_task(project_id, parent_id, row["title"])
                        created_id = created.get("id") or created.get("instId")
                        if not created_id:
                            raise ApiError(f"创建成功但没有返回 id: {created}")
                        self.queue_log(f"第 {index} 条已创建 ID={created_id}")

                        if options["set_assignee"]:
                            api.set_assignee(created_id, options["assignee_id"])
                            self.queue_log(f"ID={created_id} 已设置负责人 {options['assignee_id']}")
                        if options["set_time"]:
                            api.set_time_property(created_id, "start", row["start"])
                            api.set_time_property(created_id, "due", row["due"])
                            self.queue_log(f"ID={created_id} 已设置时间 {row['start']} ~ {row['due']}")
                        if options["set_state"]:
                            api.set_state(created_id, options["state_id"])
                            self.queue_log(f"ID={created_id} 已设置状态 {options['state_id']}")
                        if options["set_estimate"]:
                            api.set_workload(created_id, row["estimate"])
                            self.queue_log(f"ID={created_id} 已设置预估工时 {row['estimate']}")
                        if options["register_workload"]:
                            api.register_workload(
                                created_id,
                                row["report"],
                                row["estimate"],
                                row["register_date"],
                            )
                            self.queue_log(f"ID={created_id} 已登记工时 {row['report']}")
                        save_history(row, created_id, "成功")
                    except Exception as exc:
                        prefix = f"第 {index} 条失败"
                        if created_id:
                            prefix += f"（已创建 ID={created_id}）"
                            save_history(row, created_id, "失败", str(exc))
                        self.queue_log(f"[失败] {prefix}: {exc}")
                        if not options["continue_on_error"]:
                            raise
                    if index < len(rows):
                        wait_seconds = random.randint(options["interval_min"], options["interval_max"])
                        if wait_seconds > 0:
                            self.queue_log(f"等待 {wait_seconds} 秒后创建下一条，请不要关闭软件。")
                            time.sleep(wait_seconds)
                self.queue_log("全部处理完成")
            finally:
                self.ui_queue.put(("creation_finished", None))

        self.append_log("创建任务已开始，创建过程中请不要关闭软件。")
        self.run_worker("执行创建", work)


if __name__ == "__main__":
    app = DailyReportApp()
    app.mainloop()
