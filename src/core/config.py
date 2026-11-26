# Maestro — core/config.py: Cấu hình, hằng số, đường dẫn dữ liệu (HOME/DATA/STORE), máy người dùng.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

"""AI Orchestrator: Claude làm PM, giao việc cho ChatGPT/Codex và Gemini/Antigravity.
Một file duy nhất. Chạy trên máy, dữ liệu nằm trên máy.

  pythonw app.py [--background]       chạy nền + mở giao diện (http://127.0.0.1:8765)
  python app.py worker codex|gemini   (Claude dùng) giao 1 sub-task cho worker
  python app.py check files|png ...   (Claude dùng) kiểm tra file kết quả
"""
import base64
import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

UI_B64 = ""  # filled at build time
ICO_B64 = ""  # icon Windows (shortcut), filled at build time
FONTS_B64 = ""  # font Geist/Geist Mono (src/fonts/*.woff2, OFL) dạng JSON {tên: base64}, filled at build time
VERSION = "dev"  # filled at build time
RELEASE = "dev"  # số phiên bản hiển thị (vd 1.6), filled at build time
UPDATE_BASE = ""  # vd https://raw.githubusercontent.com/<owner>/<repo>/main/ (filled at build time)
LEARN_MD = ""  # bài học chung đã duyệt (src/learnings.md), filled at build time
LEARN_FORM = ""  # nơi nhận bài học gửi về (src/learn_form.json: Google Form), filled at build time

APP = Path(__file__).resolve()
HOME = Path(os.environ.get("AIO_HOME") or APP.parent)
DATA = HOME / "data"
CFG_FILE = DATA / "config.json"
PROJ = DATA / "projects"
PORTS = [int(os.environ["AIO_PORT"])] if os.environ.get("AIO_PORT") else [80, 8765]
PORT = PORTS[0]
HOSTNAME = "ai-orchestrator.localhost"   # trình duyệt tự trỏ *.localhost về máy này, không cần sửa file hosts


def app_url(port=None):
    port = port or PORT
    return f"http://{HOSTNAME}{'' if port == 80 else ':' + str(port)}/"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0
# Gemini CLI (Antigravity) tự cập nhật nền ~15-40 phút/lần bằng tiến trình mở console riêng -> trên máy dùng Windows Terminal
# làm console mặc định sẽ bật cửa sổ đen. Tắt cơ chế đó (mọi lệnh agy do app / Claude / worker gọi đều kế thừa biến này);
# Maestro tự cập nhật Gemini CLI ngầm mỗi ngày 1 lần (agy_update_loop).
os.environ.setdefault("AGY_CLI_DISABLE_AUTO_UPDATE", "true")
def machine():
    """Số luồng CPU và RAM (GB) của máy."""
    cpu, ram = os.cpu_count() or 2, 8.0
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                                                      "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            ram = m.ullTotalPhys / 2 ** 30
        else:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:
        pass
    return cpu, round(ram, 1)


CPU, RAM_GB = machine()


def machine_tier(cpu, ram):
    """Mức máy -> (số task chạy cùng lúc, worker song song mỗi task, chạy AI ưu tiên thấp?).
    AI chạy trên máy chủ của hãng; máy chủ yếu tốn RAM (~1–1,5 GB/Claude, 0,5–1 GB/worker) và CPU lúc ghép ảnh/chạy script."""
    low = ram <= 8.5                         # chỉ máy 8 GB RAM trở xuống chạy AI ưu tiên thấp
    if cpu >= 16 and ram >= 30:
        return 6, 3, low
    if cpu >= 10 and ram >= 15:
        return 4, 3, low
    if cpu >= 6 and ram >= 11:
        return 3, 2, low
    return 1, 1, low


MAX_RUNNING, MAX_WORKERS, LOW_PRIO = machine_tier(CPU, RAM_GB)
# Trần số worker 1 task được chạy cùng lúc (Claude tự chọn số thật theo độ nặng việc + hạn mức còn lại + giờ reset).
# Worker chủ yếu chờ máy chủ AI; máy chỉ tốn ~0,5–1 GB RAM/worker -> trần theo RAM.
MAX_PER_TASK = 1 if MAX_RUNNING == 1 else max(MAX_WORKERS, min(10, int(RAM_GB // 2)))
LOW_SPEC = MAX_RUNNING == 1
MODES = {"pro": "Pro (mạnh nhất, chấm kỹ)", "balanced": "Balanced (cân bằng)", "turbo": "Turbo (nhanh nhất, giảm thẩm định)",
         "eco": "Eco (tiết kiệm hạn mức)", "code": "Code (sửa code, chạy thử)"}
MODE_ALIAS = {"fast": "pro", "saver": "eco"}          # tên cũ (task/template/trình duyệt của bản trước)


def norm_mode(m):
    m = MODE_ALIAS.get(m, m)
    return m if m in MODES else "balanced"
CATEGORIES = ["video", "ảnh/asset", "playable", "code", "phân tích/research", "ý tưởng/kịch bản", "bảng tính/dữ liệu",
              "tài liệu/văn bản", "sửa file", "hỏi đáp", "khác"]
BELOW_NORMAL = 0x00004000 if IS_WIN and LOW_PRIO else 0     # máy yếu: AI chạy ưu tiên thấp để máy vẫn mượt
PING = "Reply with the single word PONG and nothing else."

CODEX_SANDBOX = (["--sandbox", "workspace-write", "-c", 'windows.sandbox="unelevated"'] if IS_WIN
                 else ["--sandbox", "workspace-write"])
AIS = {
    "claude": {"bin": "claude", "login": ["auth", "login"], "test": ["-p", PING]},
    "codex": {"bin": "codex", "login": ["login"], "test": ["exec", "--skip-git-repo-check", *CODEX_SANDBOX, PING]},
    "gemini": {"bin": "agy", "login": [], "test": ["-p", PING]},
}
ICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="103 103 1049 1049"><rect x="103" y="103" width="1049" height="1049" rx="250" fill="#1A1A1A"/><g fill="#FFF"><path id="L" d="M380 345H628A15 15 0 0 1 643 360V480A15 15 0 0 1 628 495H515A15 15 0 0 0 500 510V707A15 15 0 0 1 485 722H380A65 65 0 0 1 315 657V410A65 65 0 0 1 380 345Z"/><use href="#L" transform="rotate(180 627.5 627)"/><rect x="560" y="559" width="133" height="136" rx="34"/></g></svg>'


_cfg_lock = threading.Lock()
CFG = None
TASKS_DIR = DATA / "tasks"
STORE = DATA                 # nơi giữ task/lịch sử: <thư mục nhận kết quả>/_AI-Orchestrator (không mất khi cập nhật/gỡ/cài lại)
STORE_NAME = "_AI-Orchestrator"


def set_store():
    """Trỏ dữ liệu task về thư mục nhận kết quả; chuyển dữ liệu cũ sang nếu có."""
    global STORE, TASKS_DIR
    o = CFG.get("output", "")
    new = Path(o) / STORE_NAME if o and Path(o).is_dir() else DATA
    new_tasks = new / "tasks"
    new_tasks.mkdir(parents=True, exist_ok=True)
    for src in {TASKS_DIR, DATA / "tasks"}:
        if src.exists() and src.resolve() != new_tasks.resolve():
            for d in list(src.iterdir()):
                if d.is_dir() and not (new_tasks / d.name).exists():
                    try:
                        shutil.move(str(d), str(new_tasks / d.name))
                    except Exception as e:
                        dlog(f"move {d}: {e}")
    STORE, TASKS_DIR = new, new_tasks
    try:
        wjson(STORE / "settings-backup.json", {k: CFG.get(k) for k in ("output", "instructions")})
    except Exception:
        pass
OLD_DEFAULT_PREFIX = "Xưng hô: tôi - bạn. Trả lời tiếng Việt, ngắn gọn"


def load_cfg():
    c = rjson(CFG_FILE, {}) or {}
    c.setdefault("token", secrets.token_urlsafe(18))
    c.setdefault("output", "")
    c.setdefault("instructions", "")
    if c["instructions"].startswith(OLD_DEFAULT_PREFIX):     # bỏ hướng dẫn mặc định của bản cũ
        c["instructions"] = ""
    c.setdefault("ais", {})
    for a in c["ais"].values():
        a.pop("pending", None)
        a.pop("code", None)
        a.pop("url", None)
        a.pop("note", None)
        a.pop("auto", None)
    return c


def save_cfg():
    with _cfg_lock:
        wjson(CFG_FILE, CFG)


def pick_folder(title="Chọn thư mục nhận kết quả"):
    title = re.sub(r'["\\]', "", title)[:80]
    if IS_MAC:     # tkinter không chạy được ngoài luồng chính trên Mac -> dùng hộp thoại của macOS
        try:
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "' + title + '")'],
                               capture_output=True, text=True, timeout=600)
            return r.stdout.strip().rstrip("/") if r.returncode == 0 else ""
        except Exception:
            return ""
    try:
        import tkinter
        from tkinter import filedialog
        r = tkinter.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        d = filedialog.askdirectory(parent=r, title=title, mustexist=True)
        r.destroy()
        return os.path.normpath(d) if d else ""
    except Exception:
        return ""


_pick_lock = threading.Lock()


def drives():
    """Mọi ổ đĩa Claude được đọc: ổ trong máy + ổ mạng đã map (Windows), thư mục home (khác)."""
    if IS_WIN:
        import string
        return [f"{c}:\\" for c in string.ascii_uppercase[2:] if os.path.isdir(f"{c}:\\")]
    if IS_MAC:
        return [str(Path.home())] + [str(p) for p in Path("/Volumes").glob("*") if p.is_dir() and not p.is_symlink()]
    return [str(Path.home())]
