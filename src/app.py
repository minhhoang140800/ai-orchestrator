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
# mỗi task đang chạy cần khoảng 4 luồng + 4 GB (Claude Code + worker); tối thiểu 1, tối đa 4
MAX_RUNNING = max(1, min(4, CPU // 4, int(RAM_GB // 4)))
LOW_SPEC = MAX_RUNNING == 1
MODES = {"pro": "Pro (mạnh nhất, chấm kỹ)", "balanced": "Balanced (cân bằng)", "turbo": "Turbo (nhanh nhất, giảm thẩm định)",
         "eco": "Eco (tiết kiệm hạn mức)", "code": "Code (sửa code, chạy thử)"}
MODE_ALIAS = {"fast": "pro", "saver": "eco"}          # tên cũ (task/template/trình duyệt của bản trước)


def norm_mode(m):
    m = MODE_ALIAS.get(m, m)
    return m if m in MODES else "balanced"
CATEGORIES = ["video", "ảnh/asset", "playable", "code", "phân tích/research", "ý tưởng/kịch bản", "bảng tính/dữ liệu",
              "tài liệu/văn bản", "sửa file", "hỏi đáp", "khác"]
BELOW_NORMAL = 0x00004000 if IS_WIN else 0     # ưu tiên thấp: máy vẫn mượt khi AI đang chạy
PING = "Reply with the single word PONG and nothing else."

CODEX_SANDBOX = (["--sandbox", "workspace-write", "-c", 'windows.sandbox="unelevated"'] if IS_WIN
                 else ["--sandbox", "workspace-write"])
AIS = {
    "claude": {"bin": "claude", "login": ["auth", "login"], "test": ["-p", PING]},
    "codex": {"bin": "codex", "login": ["login"], "test": ["exec", "--skip-git-repo-check", *CODEX_SANDBOX, PING]},
    "gemini": {"bin": "agy", "login": [], "test": ["-p", PING]},
}
ICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="21.3" fill="#1E1E1E"/><g fill="none" stroke="#FFF" stroke-width="6.6"><path d="M23.5 33.8V50.9L50 77.3L76.5 50.9V33.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M50 33.8V74"/></g><circle cx="50" cy="33.8" r="3.3" fill="#FFF"/></svg>'


# ---------------------------------------------------------------- helpers
def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def rjson(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def wjson(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def slug(s, n=40):
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:n].lower() or "x"


def safe_name(s, n=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s).strip(" .")
    return re.sub(r"\s+", " ", s)[:n].strip(" .") or "Untitled"


def find_bin(name):
    p = shutil.which(name)
    if p:
        return p
    cands = [Path.home() / ".local" / "bin", Path(os.environ.get("APPDATA", "")) / "npm",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity-cli",
             Path.home() / ".antigravity" / "bin"]
    for d in cands:
        for ext in ([".exe", ".cmd", ""] if IS_WIN else [""]):
            c = d / (name + ext)
            if c.exists():
                return str(c)
    return None


def py_console():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        c = exe.with_name("python.exe")
        if c.exists():
            return str(c)
    return str(exe)


def kill_tree(p):
    if p and p.poll() is None:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
        else:
            p.kill()


def kill_orphan(pid):
    """Tiến trình claude cũ còn sót khi app bị tắt đột ngột: dừng trước khi chạy tiếp."""
    if not pid:
        return
    if not IS_WIN:
        try:
            r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10)
            if any(k in (r.stdout or "").lower() for k in ("claude", "codex")):
                os.kill(pid, 9)
        except Exception:
            pass
        return
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                       text=True, creationflags=NO_WINDOW)
    if any(k in (r.stdout or "").lower() for k in ("claude", "codex")):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)


PS_OPEN_FOLDER = r"""
$p = $env:AIO_OPEN.TrimEnd('\')
$sel = $env:AIO_SELECT
Add-Type -Namespace AIO -Name W -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h); [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);'
function Find { foreach ($x in (New-Object -ComObject Shell.Application).Windows()) { try { if ($x.Document.Folder.Self.Path.TrimEnd('\') -ieq $p) { return $x } } catch {} } return $null }
$w = Find
if ($w -and $sel) { try { $w.Document.SelectItem($w.Document.Folder.ParseName((Split-Path $sel -Leaf)), 29) } catch {} }
if (-not $w) {
  if ($sel) { Start-Process explorer.exe -ArgumentList ('/select,"' + $sel + '"') } else { Start-Process explorer.exe -ArgumentList ('"' + $p + '"') }
  for ($i = 0; $i -lt 30 -and -not $w; $i++) { Start-Sleep -Milliseconds 150; $w = Find }
}
if ($w) {
  $h = [IntPtr]$w.HWND
  if ([AIO.W]::IsIconic($h)) { [void][AIO.W]::ShowWindow($h, 9) }
  (New-Object -ComObject WScript.Shell).SendKeys('%')
  [void][AIO.W]::BringWindowToTop($h)
  [void][AIO.W]::SetForegroundWindow($h)
}
"""


def open_path(p, select=""):
    p = str(p)
    if IS_WIN and os.path.isdir(p):      # thư mục đang mở sẵn -> đưa lên trên cùng; mở mới cũng đưa lên trên cùng
        try:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS_OPEN_FOLDER],
                             env=dict(os.environ, AIO_OPEN=p, AIO_SELECT=select), creationflags=NO_WINDOW)
            return
        except Exception:
            pass
    if IS_MAC:                            # Finder: mở và đưa lên trên cùng; select -> chọn sẵn file
        subprocess.Popen(["open", "-R", select] if select else ["open", p])
        return
    if IS_WIN:
        os.startfile(p)  # noqa
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", p])


def refresh_path():
    """Lấy PATH mới nhất từ registry (CLI vừa cài xong vẫn tìm thấy) + tìm Node.js ở các nơi hay cài
    (Program Files, nvm, volta, fnm, scoop...) để Codex (chạy bằng node) luôn chạy được."""
    if IS_MAC:     # app mở từ Launchpad/đăng nhập máy có PATH rất ngắn -> thêm chỗ Homebrew, npm, Claude, agy hay cài
        h = str(Path.home())
        extra = ["/opt/homebrew/bin", "/usr/local/bin", h + "/.local/bin", h + "/.npm-global/bin", h + "/.agy/bin",
                 h + "/.local/share/agy/bin", "/opt/homebrew/opt/node/bin"]
        cur = os.environ.get("PATH", "").split(":")
        os.environ["PATH"] = ":".join([x for x in extra if os.path.isdir(x) and x not in cur] + cur)
        return
    if not IS_WIN:
        return
    import glob
    try:
        import winreg
        vals = {}
        for root, key in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")]:
            try:
                with winreg.OpenKey(root, key) as k:
                    for n in range(winreg.QueryInfoKey(k)[1]):
                        name, v, _ = winreg.EnumValue(k, n)
                        if isinstance(v, str):
                            vals.setdefault(name.upper(), []).append(v)
            except OSError:
                pass
        for name, vs in vals.items():          # biến như NVM_SYMLINK, NVM_HOME phải có trước khi mở rộng PATH
            if name != "PATH" and name not in (k.upper() for k in os.environ):
                os.environ[name] = os.path.expandvars(vs[-1])
        parts = [os.path.expandvars(v) for v in vals.get("PATH", [])]
        if parts:
            os.environ["PATH"] = ";".join(parts + [os.environ.get("PATH", "")])
    except Exception:
        pass
    # Bỏ trùng: mỗi lần tự cập nhật app khởi động lại với PATH cũ + PATH registry -> PATH phình to qua nhiều bản,
    # vượt giới hạn của cmd.exe (~8 KB) nên các .cmd (codex) không thấy node. Giữ thứ tự, bỏ mục trùng/rỗng.
    seen, keep = set(), []
    for x in os.environ.get("PATH", "").split(";"):
        k = x.strip().strip('"').rstrip("\\").lower()
        if k and k not in seen:
            seen.add(k)
            keep.append(x.strip())
    os.environ["PATH"] = ";".join(keep)
    if shutil.which("node"):
        return
    E = os.environ.get
    cands = [os.path.join(E("ProgramFiles", r"C:\Program Files"), "nodejs"), os.path.join(E("ProgramFiles(x86)", ""), "nodejs"),
             os.path.join(E("LOCALAPPDATA", ""), "Programs", "nodejs"), E("NVM_SYMLINK", ""), os.path.join(E("LOCALAPPDATA", ""), "Volta", "bin"),
             os.path.join(E("ProgramFiles", ""), "Volta"), os.path.join(E("USERPROFILE", ""), "scoop", "apps", "nodejs", "current"),
             os.path.join(E("USERPROFILE", ""), "scoop", "apps", "nodejs-lts", "current"), r"C:\ProgramData\chocolatey\bin"]
    cands += sorted(glob.glob(os.path.join(E("APPDATA", ""), "nvm", "v*")), reverse=True)
    cands += sorted(glob.glob(os.path.join(E("LOCALAPPDATA", ""), "fnm_multishells", "*")), reverse=True)[:1]
    try:
        cands.insert(0, (HOME / "node_dir.txt").read_text("utf-8-sig").strip())     # file cài ghi lại chỗ node
    except Exception:
        pass
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "node.exe")):
            os.environ["PATH"] = d + ";" + os.environ.get("PATH", "")
            return


LIBS = {"PIL": "pillow", "openpyxl": "openpyxl", "docx": "python-docx", "pptx": "python-pptx",
        **({"winpty": "pywinpty"} if IS_WIN else {})}     # đọc/sửa ảnh, Excel, Word, PowerPoint; terminal ảo (đăng nhập)
LIBS_READY = threading.Event()


def ensure_libs():
    """Mở app: cài thư viện Python còn thiếu (bản cập nhật qua nút Cập nhật không chạy lại bộ cài). Tối đa 1 lần/ngày."""
    import importlib.util
    try:
        miss = [pkg for mod, pkg in LIBS.items() if importlib.util.find_spec(mod) is None]
        last = (CFG or {}).get("libs_at", 0)
        if not miss or time.time() - last < 86400:
            return
        CFG["libs_at"] = int(time.time())
        save_cfg()
        base = [py_console(), "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check"]
        for extra in ([], ["--break-system-packages"]) if IS_MAC else ([],):
            r = subprocess.run(base + extra + miss, capture_output=True, text=True, errors="replace", timeout=600,
                               creationflags=NO_WINDOW)
            dlog(f"pip install {miss} -> exit {r.returncode}\n{(r.stderr or '')[-1200:]}")
            if r.returncode == 0:
                break
        import site
        importlib.invalidate_caches()
        if site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())
    except Exception as e:
        dlog(f"ensure_libs: {e}")
    finally:
        LIBS_READY.set()


# ---------------------------------------------------------------- config
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


# ---------------------------------------------------------------- tasks
TASKS = {}          # task_id -> dict (persisted in task.json)
PROCS = {}          # task_id -> Popen
_tlock = threading.RLock()


def tdir(t):
    return TASKS_DIR / t["id"]


def wdir(t):
    """Thư mục nháp của task. Task mới: <thư mục kết quả của task>/.work (cùng ổ với sản phẩm, xóa khi task xong).
    Task tạo từ bản cũ (đã có phiên Claude gắn với thư mục lịch sử): giữ thư mục lịch sử để Claude nối tiếp được."""
    if t.get("legacy") or not t.get("folder") or not CFG.get("output"):
        return tdir(t)
    return out_dir(t) / ".work"


def finish_clean(t):
    """Task xong: lưu lại bảng sub-task + feedback/quyết định vào lịch sử, rồi xóa toàn bộ nháp/hướng dẫn/bản trùng."""
    learn_queue(t)                         # bài học chung (đã lọc dữ liệu riêng) -> hàng đợi gửi về team
    try:
        subs = subtasks(t)
        works = [m for m in t.get("messages", []) if m["role"] == "work"]
        for s_ in subs:
            for m in reversed(works):
                if s_["ts"] >= m.get("started_ts", 0) - 1:
                    m.setdefault("subs", [])
                    if not any(x.get("id") == s_["id"] for x in m["subs"]):
                        m["subs"].append({**s_, "status": s_["status"] if s_["status"] != "running" else "stopped"})
                    break
        dec = rjson(wdir(t) / "tasks" / "decisions.json", None)
        if isinstance(dec, list):
            t["decisions"] = dec
        cf = wdir(t) / "tasks" / "criteria.md"
        if cf.exists():
            t["recipe"] = {"criteria": cf.read_text("utf-8", "replace")[:8000]}
        t["brain_suggest"] = brain_suggest(t)
        tsave(t)
        w = wdir(t)
        if w != tdir(t):
            shutil.rmtree(w, ignore_errors=True)
        else:                        # task cũ: chỉ giữ task.json, file đính kèm, CLAUDE.md
            for x in ("work", "tasks"):
                shutil.rmtree(w / x, ignore_errors=True)
            for x in ("run.log", "timeline.log", "question.md"):
                (w / x).unlink(missing_ok=True)
    except Exception as e:
        dlog(f"finish_clean {t.get('id')}: {e}")


def tsave(t):
    with _tlock:
        wjson(tdir(t) / "task.json", {k: v for k, v in t.items() if not k.startswith("_")})


def tmsg(t, role, text, **kw):
    t.setdefault("messages", []).append({"role": role, "text": text, "time": now(), **kw})
    return len(t["messages"]) - 1


def work(t):
    i = t.get("wi")
    if i is not None and i < len(t.get("messages", [])) and t["messages"][i]["role"] == "work":
        return t["messages"][i]
    return None


def step(t, line):
    w = work(t)
    line = line.strip()
    if line:
        try:      # nhật ký có giờ cho từng bước (dùng khi xuất log)
            with open(wdir(t) / "timeline.log", "a", encoding="utf-8") as f:
                f.write(f"{now()}\t{line[:500]}\n")
        except Exception:
            pass
    if w is not None and line:
        w["steps"].append(line[:300])
        w["steps"] = w["steps"][-400:]


def end_work(t, status):
    w = work(t)
    if w is not None and w.get("status") == "running":
        w["status"] = status
        w["ended"] = now()
        w["ended_ts"] = time.time()


def migrate_old():
    """Chuyển task của bản trước (data/projects/<p>/tasks/<id>) sang data/tasks/<id>."""
    old = DATA / "projects"
    if not old.exists():
        return
    for f in old.glob("*/tasks/*/task.json"):
        t = rjson(f)
        if not t or (TASKS_DIR / t["id"]).exists():
            continue
        pname = (rjson(f.parents[2] / "project.json") or {}).get("name", "")
        if not t.get("folder"):
            t["folder"] = str(Path(safe_name(pname)) / safe_name(t["id"] + " " + t.get("title", ""), 80)) if pname else t["id"]
        msgs = t.get("messages") or [{"role": "user", "text": t.get("text", ""), "time": t.get("created", "")}]
        t["messages"] = [m for m in msgs if m.get("role") in ("user", "claude")]
        t.pop("log", None)
        wjson(f, t)
        shutil.move(str(f.parent), str(TASKS_DIR / t["id"]))


def load_tasks():
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        migrate_old()
    except Exception:
        pass
    for f in TASKS_DIR.glob("*/task.json"):
        t = rjson(f)
        if not t:
            continue
        for m in t.get("messages", []):          # câu trả lời cũ xuất file ra chỗ khác: gắn nút Open folder
            if m.get("role") == "claude" and not m.get("files") and not m.get("chk"):
                m["chk"] = 1
                try:
                    d = path_in_text(m.get("text", ""))
                    if d:
                        fs = _scan(d, 0)
                        if fs:
                            m["files"], m["dir"] = fs[:30], str(d)
                except Exception:
                    pass
        if not t.get("w2") and t.get("session_started"):
            t["legacy"] = True
        t["mode"] = norm_mode(t.get("mode"))
        t.pop("installing", None)              # app tắt giữa lúc cài: cho bấm Cài lại
        if t.get("status") == "running":      # app bị tắt khi đang chạy -> tự chạy tiếp
            kill_orphan(t.get("pid"))
            end_work(t, "interrupted")
            t["status"] = "queued"
            t["next"] = {"kind": "resume"}
        if t.pop("auto_paused", None) and t.get("status") == "paused":    # bản cũ tự dừng task khi đóng tab -> chạy tiếp
            t["status"] = "queued"
            t.setdefault("next", {"kind": "resume"})
        TASKS[t["id"]] = t
        tsave(t)


def out_dir(t):
    return Path(CFG["output"]) / t.get("folder", t["id"])


def subtasks(t):
    """Sub-task Claude đã giao cho worker: đọc từ work/<ID>/vN/status.json."""
    out = []
    w = wdir(t) / "work"
    if not w.exists():
        return out
    for d in sorted(x for x in w.iterdir() if x.is_dir()):
        vs = sorted((v for v in d.iterdir() if v.is_dir() and re.fullmatch(r"v\d+", v.name)),
                    key=lambda v: int(v.name[1:]))
        if not vs:
            continue
        v = vs[-1]
        st = rjson(v / "status.json") or {}
        title = ""
        tf = v / "TASK.md"
        if tf.exists():
            for line in tf.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    title = line.strip().lstrip("#").strip()[:90]
                    break
        out.append({"id": d.name, "title": title, "attempt": len(vs), "worker": st.get("worker") or "",
                    "model": st.get("model") or "", "status": st.get("status") or "running",
                    "seconds": st.get("seconds"), "error": (st.get("error") or "")[:200],
                    "ts": (v / "status.json").stat().st_mtime if (v / "status.json").exists() else v.stat().st_mtime})
    return out


MODE_RULES = {
    "code": "",          # Code mode có quy trình riêng (CODE_MD)
    "pro": """- Ưu tiên THỜI GIAN và chất lượng. Chạy song song tối đa {n} worker/sub-agent cho các phần độc lập.
- Dùng model mạnh ngay từ đầu để ít phải sửa. Giám khảo được dùng model mạnh, tối đa 2 vòng sửa.""",
    "balanced": """- Cân bằng chất lượng, thời gian, hạn mức. Song song tối đa {n} worker/sub-agent cho phần độc lập; tuần tự khi phụ thuộc.
- Model nhỏ/nhanh cho việc đơn giản; model mạnh cho việc khó. Nâng model khi kết quả không đạt, hạ khi việc lặp lại.""",
    "turbo": """- Ưu tiên TỐC ĐỘ TỐI ĐA. Bắt tay làm ngay, kế hoạch tối giản (vài dòng), không hỏi lại trừ khi thiếu dữ kiện bắt buộc.
- Song song tối đa {n} worker/sub-agent cho mọi phần độc lập; chọn model nhanh (sonnet/haiku, model nhỏ cho worker).
- GIẢM THẨM ĐỊNH: KHÔNG gọi giám khảo, không làm mẫu thử trước lô, không vòng sửa. Chỉ chạy check kỹ thuật (kích thước,
  định dạng, số file) bằng lệnh check; sai thì sửa đúng chỗ sai. Không viết criteria.md dài — vài dòng là đủ.
- Tự xem ảnh/sản phẩm tối đa 1 lần; lỗi nhỏ về thẩm mỹ thì ghi chú cho người dùng, không làm lại.
- Không làm thêm ngoài yêu cầu (không preview/ghép so sánh, không cập nhật ghi chú phụ).
- Trả kết quả ngay khi đủ dùng; ghi rõ phần chưa được kiểm tra kỹ.""",
    "eco": """- Ưu tiên TIẾT KIỆM HẠN MỨC. Làm tuần tự, 1 worker tại một thời điểm. Việc nào tự làm được bằng lệnh/script thì không gọi AI.
- Dùng model nhỏ nhất đủ làm được việc cho worker và sub-agent (--model); chỉ nâng khi đã thất bại 1 lần.
- Giám khảo: dùng model nhỏ, chỉ chấm sản phẩm cuối, tối đa 1 vòng sửa. Không làm thêm ngoài yêu cầu.""",
}


def write_claude_md(t):
    py, app = py_console(), str(APP)
    mode = norm_mode(t.get("mode"))
    mode_name = MODES[mode]
    mode_rules = MODE_RULES[mode].format(n="1 (máy cấu hình thấp)" if LOW_SPEC else str(min(3, MAX_RUNNING + 1)))
    pm = t.get("pm") or "claude"
    agy_ok = bool(find_bin("agy")) and CFG["ais"].get("gemini", {}).get("ok") is not False
    can = {"codex": bool(find_bin("codex")) and CFG["ais"].get("codex", {}).get("ok") is not False,
           "gemini": agy_ok or bool(CFG.get("gemini_key"))}
    on = [w for w in ("codex", "gemini") if w != pm and can[w]]
    desc = {"codex": "codex — ChatGPT/Codex: code, CSV/Excel/Word/PowerPoint, script, sửa file, tạo ảnh ($imagegen)",
            "gemini": "gemini — Gemini/Antigravity: research web, tổng hợp, ý tưởng; tạo/sửa ảnh, tạo/sửa file (--write / --edit)"
            + (" (hết lượt/lỗi thì app tự chuyển sang Gemini API key: chỉ trả lời chữ)" if CFG.get("gemini_key") else "")
            if agy_ok else "gemini — Gemini qua API key: CHỈ trả lời bằng chữ (research web, tổng hợp, ý tưởng, viết, đọc ảnh/PDF "
            "gửi kèm: ghi đúng tên file đính kèm trong tasks/<ID>.md); KHÔNG tạo/sửa file -> không dùng --write/--edit"}
    pj = project_of(t)
    brain_md = (f"""## Dự án: {pj['name']} — bộ nhớ dự án (người dùng đã duyệt, áp dụng cho mọi task của dự án)
{pj.get('brain', '').strip() or '(chưa có)'}
- Tuân theo bộ nhớ này; giám khảo cũng chấm theo nó (đưa các mục liên quan vào tiêu chí).
- Thấy quy tắc/quyết định nên áp dụng cho CẢ dự án (style, màu, size chuẩn, đặt tên file, KPI, cách đọc file data...) mà
  bộ nhớ chưa có: ghi đề xuất vào ./brain_suggest.json dạng ["<1 câu ngắn>", ...]. KHÔNG tự sửa bộ nhớ; người dùng duyệt.

""") if pj else ""
    tp = t.get("template")
    tpl_md = (f"""## Quy trình mẫu "{tp['name']}" (đã làm đạt trước đây) — làm theo, thay tham số bằng yêu cầu mới
Yêu cầu khi đó: {' / '.join(x[:300] for x in tp.get('asks', [])[:5])}
Các bước: {'; '.join(f"{x['title']} ({x['worker']} {x['model']})".strip() for x in tp.get('steps', []))}
Tiêu chí đã dùng:
{tp.get('criteria', '').strip() or '(không lưu)'}

""") if tp else ""
    txt = f"""# Task {t['id']}

## Hướng dẫn của người dùng (ưu tiên áp dụng)
{CFG.get('instructions', '').strip() or '(không)'}

## Vai trò
Bạn là PM. Phân tích yêu cầu, tự quyết cách làm, chia sub-task, chọn worker và model, giao việc,
nghiệm thu trên file thật, sửa đúng phần lỗi, báo cáo. Task này là một cuộc trò chuyện nhiều lượt.

## Đường dẫn
- Thư mục làm việc (nháp, brief, log): thư mục hiện tại. Bị XÓA TOÀN BỘ khi task xong -> không để sản phẩm ở đây.
- File người dùng gửi: {tdir(t) / "input"}
- Được ĐỌC mọi ổ đĩa của máy: {', '.join(drives())}. Người dùng nhắc tới file/thư mục nào thì tự tìm.
- OUTPUT (nơi lưu sản phẩm giao người dùng): {out_dir(t)}
  Người dùng chỉ định nơi lưu cụ thể -> lưu đúng nơi đó, ghi đường dẫn đầy đủ trong câu trả lời.
- Sửa file có sẵn trên máy: CHỈ khi người dùng yêu cầu sửa chính file/thư mục đó. Không xóa/di chuyển file nào ngoài
  thư mục làm việc và OUTPUT. Ghi rõ trong câu trả lời đã sửa file nào.
- Sản phẩm của các task trước (để dùng lại, tránh làm lại): danh mục ở {STORE / "registry.json"}

## Worker sẵn có trên máy
{chr(10).join('- ' + desc[w] for w in on) or '- (không có — tự làm)'}
Tự chọn worker cho từng sub-task, không bắt buộc dùng. Việc nhỏ hoặc cần phán đoán thì tự làm.
Worker lỗi đăng nhập/không chạy: coi như không có, chuyển worker khác hoặc tự làm.

Giao sub-task: viết yêu cầu đầy đủ ngữ cảnh vào tasks/<ID>.md (dòng đầu = tên ngắn; worker không thấy
cuộc trò chuyện), rồi chạy:
  "{py}" "{app}" worker codex  --task tasks/S1.md --out work/S1 [--edit <thư mục>] [--drive] [--model <model>] [--timeout 900]
  "{py}" "{app}" worker gemini --task tasks/S2.md --out work/S2 [--write] [--edit <thư mục>] [--hard]
  --write: Gemini được tạo file/ảnh trong work/S2/vN. --hard: việc khó -> Gemini mức high. Giao lại cùng --out tự nâng mức.
  --edit <thư mục>: worker SỬA TRỰC TIẾP file trong thư mục đó (file người dùng yêu cầu sửa, OUTPUT, hoặc file Drive đã tải về).
     Dùng khi việc sửa nhiều/lặp lại (nhiều file, bảng tính lớn, sửa hàng loạt) để khỏi tự chép/sửa lại. Chọn thư mục NHỎ NHẤT
     chứa file cần sửa (không phải cả ổ/thư mục người dùng — app sẽ từ chối). App tự sao lưu trước khi sửa vào
     work/<ID>/vN/_backup (hoàn tác bằng cách chép lại), status.json có "edited" = danh sách file đã đổi.
Kết quả ở work/<ID>/vN/ (result.md, status.json, worker.log, file do worker tạo). Sửa lỗi: giao lại cùng --out.

## Chia việc (Gemini nhanh, GPT chính xác hơn)
- Research web, đối thủ, xu hướng: Gemini (mọi chế độ).
- Pro: GPT làm trước mọi việc còn lại.
- Balanced/Turbo/Eco — "Gemini trước, lỗi mới GPT": idea, kịch bản, brief, tạo/sửa ảnh giao Gemini; Balanced chia đều
  idea/câu chữ: Gemini phần cần nhanh/rộng, GPT phần cần sắc (idea chính, hook) — không dồn hết cho GPT.
- Code, script, phân tích số liệu, sửa Excel/Word/PowerPoint: GPT (việc rất đơn giản ở Turbo/Eco: Gemini).
- Phần nào Gemini làm chưa đạt (sai size, sai nội dung, chữ lỗi, thiếu chi tiết bắt buộc) -> chỉ giao lại PHẦN đó cho GPT.
- Việc phụ thuộc nhau (vd idea cần dựa trên research): giao research trước; còn lại chạy song song.
- Nhiều sản phẩm độc lập cùng loại: chia 2–3 worker chạy song song (mỗi worker --out riêng), không dồn cả lô vào 1 worker.
  Hai worker KHÔNG được --edit cùng một thư mục cùng lúc.

## Tốc độ (quan trọng)
- VIỆC ĐƠN GIẢN (hỏi đáp, 1 thao tác, sửa nhỏ — làm xong trong 1–2 bước): làm NGAY, không viết criteria.md/plan.json/decisions.json,
  không gọi giám khảo, không chạy thử trước; giao worker thì giao thẳng việc thật (không giao bước "kiểm tra quyền" riêng).
- Sản phẩm là VĂN BẢN do worker viết: dặn worker ghi TOÀN BỘ nội dung vào câu trả lời cuối (result.md), không chỉ liệt kê file.
- Không chép lại/viết lại dài nội dung worker đã viết đạt. Viết nội dung cuối MỘT lần (câu trả lời cuối hoặc 1 file trong OUTPUT).
- Ít bước nhỏ lẻ: gộp nhiều lệnh đọc/kiểm tra vào một lần chạy.

## Nghiệm thu
- status "completed" của worker chưa phải là xong: luôn kiểm tra file thật. Với --edit: mở lại file đã sửa để kiểm tra.
- TIÊU CHÍ: trước khi làm, viết tasks/criteria.md ngắn, kiểm chứng được, từ chính yêu cầu (phải có gì, định dạng/kích thước,
  style, lỗi cần tránh; đánh dấu BẮT BUỘC). Người dùng đổi yêu cầu -> cập nhật. Không hỏi duyệt. Turbo: vài dòng là đủ.
- Đo được: "{py}" "{app}" check files|png ... (xem --help); bảng tính/dữ liệu: tự đọc lại bằng Python.
- Chấm chất lượng (Turbo: bỏ qua, chỉ check kỹ thuật):
  - Văn bản/task nhẹ (phân tích, research, idea, kịch bản, báo cáo): chấm 1 LẦN, nhanh — judge --model haiku hoặc giao
    gemini đọc soát theo criteria.md. Chỉ sửa lỗi nêu, không chấm lại.
  - Hình/âm thanh/code/bảng tính: check kỹ thuật trước; rồi gọi giám khảo 1 LẦN cho CẢ LÔ ở cuối:
    "{py}" "{app}" judge --files <thư mục/file> --criteria tasks/criteria.md [--refs "<mẫu đã duyệt>"] --brief "<yêu cầu>" --out work/J1 [--model <model>]
    Kết quả work/J1/vN/review.json. Fail -> sửa đúng lỗi nêu, chỉ chấm lại file đã sửa.
  - Lô lớn (từ 5 sản phẩm cùng loại): làm 1 mẫu, tự xem rồi mới chạy cả lô.
  - Giám khảo dùng model nhỏ (sonnet/haiku) trừ chế độ Pro.
  Bỏ qua khi hỏi đáp/tra cứu hoặc người dùng bảo làm nhanh/nháp.
- Lỗi: giao sửa đúng phần lỗi, tối đa 2 lần mỗi sub-task (số vòng theo chế độ bên dưới); quá thì ghi partial, báo người dùng.

## Ảnh có đánh dấu
- Người dùng gửi ảnh "_danhdau.png": tin nhắn có dòng [Đánh dấu trên ...] với toạ độ pixel thật của ảnh gốc.
  Đỏ = lỗi/cần xoá; cam = cần xem/sửa/thêm; số 1,2,3 khớp với ghi chú "1) ... 2) ..." trong tin nhắn. Sửa ĐÚNG vùng được đánh dấu.
- Khi BẠN cần đánh dấu ảnh (báo lỗi QA, so sánh trước/sau, chỉ chỗ sửa): dùng Pillow, đọc kích thước ảnh trước; khung bo góc
  (radius ~14, nét ~5px theo ảnh ~1400px, lề 18px quanh vùng); đường dẫn 25–35px cùng độ dày; nhãn 1–3 chữ, cùng màu, đặt sát;
  KHÔNG viền trắng quanh chữ; đỏ #E63946 chỉ cho lỗi/xoá, cam #FF9F1C cho phần còn lại; 2–6 chỗ/ảnh, nhiều hơn thì đánh số +
  chú thích hoặc tách ảnh; không resize ảnh khi đánh dấu; xem lại ảnh đã vẽ trước khi giao; liệt kê từng chỗ (số, màu, toạ độ, ý nghĩa).

## Google Drive / Sheets / Docs / Slides
Công cụ Google Drive của BẠN (mcp__claude_ai_Google_Drive, nạp bằng ToolSearch): tìm, đọc, tải, tạo file — KHÔNG sửa được ô/nội dung.
- Chỉ đọc/tóm tắt: tự đọc, không cần worker.
- SỬA TRỰC TIẾP trên file Google (giữ nguyên link): giao codex kèm --drive (ChatGPT dùng kết nối Google Drive của nó, có lệnh ghi
  Sheets/Docs/Slides). Ghi rõ link, tab/vùng cần sửa, phần phải giữ (tiêu đề, định dạng, checkbox...). Sửa xong giao codex đọc lại
  để kiểm tra. Codex báo không có kết nối Google Drive / bị chặn -> cách dự phòng bên dưới.
- Dự phòng: tải file về work/drive/<tên>/ (Sheets -> .xlsx, Docs -> .docx, Slides -> .pptx), giao worker --edit work/drive/<tên>,
  kiểm tra, rồi cập nhật bản trên Drive nếu công cụ cho phép; không thì lưu vào OUTPUT và chỉ cách thay (File > Import > Upload >
  Replace spreadsheet). Không đổi quyền chia sẻ file Drive.

## Storyboard
Storyboard viết dạng BẢNG CHỮ (cảnh, thời lượng, hình ảnh, chữ trên màn hình, âm thanh, CTA) trong câu trả lời hoặc tài liệu.
KHÔNG tự tạo ảnh/khung hình storyboard, trừ khi người dùng yêu cầu rõ (vẽ/minh hoạ/render storyboard).

## Worker chạy tách rời
Worker vẫn chạy tiếp khi phiên của bạn bị ngắt (app cập nhật/khởi động lại). Khi tiếp tục: đọc work/<ID>/vN/status.json —
"running" thì CHỜ bằng lệnh: "{py}" "{app}" wait work/<ID>   (không chạy lại worker đó).

## Trả kết quả theo từng chặng
Task nhiều phần (vd phân tích → idea → sản phẩm): xong phần nào có ích cho người dùng thì ghi ngay nội dung phần đó (markdown
ngắn gọn) vào ./stages/<số thứ tự>.md — giao diện hiện ngay cho người dùng đọc trong lúc bạn làm tiếp. Câu trả lời cuối vẫn phải
đủ ý (gộp kết quả các chặng); các chặng sẽ được ẩn khi task xong.

## Model
Bạn chạy Opus, mức suy nghĩ theo chế độ (Pro high, Balanced/Turbo medium, Eco low). App TỰ chọn model cho worker theo chế độ
(ChatGPT: GPT-6.2 Sol + mức suy nghĩ; Gemini: Neon medium ở Pro, Hydrogen medium ở Balanced (khó/giao lại: high), medium ở Turbo, low ở Eco;
gói Free/thiếu model thì tự lùi model có sẵn) và tự hạ khi sắp hết hạn mức —
KHÔNG truyền --model cho worker trừ khi có lý do rõ. {"Bước QUÁ khó (đã thử mà không ra): ghi xhigh vào tasks/effort.txt -> lượt sau bạn và worker chạy mức cao nhất (Gemini Neon high)." if t.get('mode') == 'pro' else ""}

## Tạo ảnh
Tạo theo mục Chia việc (Gemini trước, lỗi thì codex $imagegen). Chỉ khi không thể tạo được (lỗi, hết hạn mức): viết prompt vào
OUTPUT/prompts/, rồi hỏi người dùng (question.md): "Dán prompt trong <đường dẫn> vào ChatGPT, lưu ảnh vào OUTPUT/images/
rồi trả lời 'xong'". Khi được trả lời: check + chấm như trên.

{brain_md}{tpl_md}## Chế độ làm việc người dùng chọn cho task này: {mode_name}
{mode_rules}
- Worker status "limit": chuyển worker khác hoặc tự làm nếu đảm bảo chất lượng; nếu không, hỏi người dùng.

## Feedback và quyết định của người dùng (bắt buộc ghi lại)
Mỗi khi người dùng góp ý, yêu cầu sửa, hoặc duyệt/chốt điều gì: ghi vào tasks/decisions.json, GOM THEO TỪNG PHẦN VIỆC
(không ghi theo thứ tự thời gian tràn lan). Định dạng:
  [{{"group": "<tên phần việc, vd: Ply33 - nhân vật Messi>", "items": [{{"type": "feedback" | "decision", "text": "<1 câu ngắn>",
     "status": "open" | "done"}}]}}]
- "decision" = điều người dùng đã duyệt/chốt (style, tên, cấu trúc thư mục, phương án...). "feedback" = góp ý cần xử lý;
  xử lý xong đổi status thành "done". Sửa/gộp mục cũ thay vì thêm mục trùng. Phần việc mới -> group mới.
- Đầu mỗi lượt: đọc file này và tuân theo các quyết định đã chốt; không hỏi lại điều đã chốt.

## Tiến độ
Cập nhật tasks/plan.json sau mỗi bước: goal, subtasks[id, title, worker, model, status(todo|running|done|failed), attempt, outputs].
Liệt kê ĐỦ các bước dự kiến ngay từ đầu (cả bước bạn tự làm) — giao diện tính % tiến độ = số bước done / tổng bước.
Có thể bị ngắt giữa chừng (người dùng gửi tin gấp, hoặc máy tắt): khi chạy tiếp, đọc plan.json, bỏ qua bước đã xong.

## Khi cần người dùng quyết định
Chỉ hỏi khi thật sự quan trọng: thao tác không hoàn tác, tốn nhiều hạn mức, yêu cầu mâu thuẫn, thiếu dữ liệu không suy ra được.
Cách hỏi: ghi câu hỏi ngắn + các phương án vào ./question.md rồi DỪNG. Việc khác: tự quyết, ghi giả định.

## Kết thúc mỗi lượt
Báo cáo, đề xuất, kết quả nghiên cứu, concept, so sánh: viết thẳng trong câu trả lời, KHÔNG tạo file .md/.txt.
Chỉ tạo file khi sản phẩm vốn là file (ảnh, code, Excel/CSV, tài liệu người dùng cần mang đi dùng) hoặc người dùng yêu cầu.
Sản phẩm dạng file lưu vào OUTPUT. Câu trả lời cuối gửi người dùng: chỉ thông tin cần thiết —
kết quả đạt/chưa đạt, điểm cần người dùng biết, việc người dùng cần làm. Không liệt kê lại từng file
(giao diện có nút mở thư mục kết quả). Có thể dùng **đậm**, `code`, gạch đầu dòng.

## Cấm
Không dùng API key. Không ghi mật khẩu/token vào file.
"""
    wdir(t).mkdir(parents=True, exist_ok=True)
    if IS_WIN and wdir(t).name == ".work":
        subprocess.run(["attrib", "+h", str(wdir(t))], creationflags=NO_WINDOW, capture_output=True)
    rules = learn_rules()
    learn_md = (("## Bài học chung của team (đã kiểm chứng, hợp với hầu hết loại task — áp dụng)\n"
                 + "\n".join("- " + r for r in rules) + "\n\n") if rules else "") + LESSON_MD
    txt = txt.replace("\n## Cấm\n", "\n" + learn_md + "\n## Cấm\n", 1)
    if "codex" not in on:
        txt = "\n".join(x for x in txt.split("\n") if '" worker codex ' not in x)
    if t.get("kind") == "code":
        txt = code_md(txt, t)
    if pm == "codex":
        txt = pm_codex_md(txt)
    if free_setup():
        txt = txt.replace("\n## Cấm\n", "\n" + FREE_RULES + "\n## Cấm\n", 1)
    f = wdir(t) / ("AGENTS.md" if pm == "codex" else "CLAUDE.md")
    import hashlib
    h = hashlib.sha1(txt.encode()).hexdigest()
    if t.get("session_started") and t.get("md_hash") != h:
        t["md_changed"] = True          # lượt tới nhắc Claude đọc lại (giữ tới khi đã nhắc)
    t["md_hash"] = h
    f.write_text(txt, encoding="utf-8")


LESSON_MD = f"""## Bài học cho cả team (khi task xong)
Ghi ./lessons.json: {{"category": "<1 trong: {', '.join(CATEGORIES)}>", "lessons": ["<1 câu>", ...]}}
- Bài học = điều rút ra giúp MỌI task sau nhanh/đúng hơn: cách chia việc, worker/model hợp việc gì, lỗi hay gặp và cách tránh,
  bước thừa nên bỏ. Tối đa 3; không có gì thật sự mới -> "lessons": [] (vẫn ghi category).
- KHÔNG ghi tên dự án/game/khách hàng/người, link, đường dẫn, nội dung sản phẩm, số liệu riêng. App gửi ẩn danh về team.
"""


FREE_RULES = """## Chế độ Free (người dùng không có tài khoản AI trả phí — áp dụng THAY mọi luật chế độ ở trên khi mâu thuẫn)
- Hạn mức rất ít: làm tuần tự, 1 worker tại một thời điểm; không gọi giám khảo, không làm mẫu thử, tối đa 1 vòng sửa.
- Ưu tiên giao Gemini (Antigravity hoặc API key) cho research, ý tưởng, viết; tự làm phần code/file bằng lệnh/script.
- Worker báo "limit": chuyển ngay sang worker/cách khác còn dùng được, không chờ; hết cả thì trả phần đã xong + nói rõ phần còn thiếu.
- Câu trả lời ngắn, đủ ý; không làm thêm ngoài yêu cầu.
"""


def _section(txt, title, body):
    """Thay nội dung 1 mục '## <title>' (tới mục kế tiếp) trong hướng dẫn."""
    return re.sub(r"(?ms)^## " + re.escape(title) + r"\n.*?(?=^## )", lambda m: f"## {title}\n{body.strip()}\n\n", txt, count=1)


def pm_codex_md(txt):
    """Hướng dẫn khi ChatGPT (Codex) làm quản lý thay Claude: không có giám khảo Claude, không có công cụ Google Drive."""
    txt = re.sub(r"(?ms)^- Chấm chất lượng .*?^  Bỏ qua khi hỏi đáp[^\n]*\n",
                 "- Chấm chất lượng: không có giám khảo riêng — tự mở lại file thật, đối chiếu từng mục BẮT BUỘC trong criteria.md;\n"
                 "  văn bản dài có thể giao gemini đọc soát theo criteria.md. Lô lớn (từ 5 sản phẩm): làm 1 mẫu, tự xem rồi mới chạy cả lô.\n", txt)
    txt = _section(txt, "Google Drive / Sheets / Docs / Slides", """
Bạn KHÔNG có công cụ Google Drive. Link Google người dùng gửi:
- File chia sẻ "Bất kỳ ai có đường liên kết": tự tải bản xuất bằng lệnh (curl/python) — Sheets:
  https://docs.google.com/spreadsheets/d/<ID>/export?format=xlsx, Docs: .../document/d/<ID>/export?format=docx,
  Slides: .../presentation/d/<ID>/export/pptx — rồi đọc/sửa bản tải về, lưu kết quả vào OUTPUT.
- Không tải được (file riêng tư): hỏi người dùng (question.md) tải file về rồi gửi kèm. Sửa xong: chỉ cách thay trên Drive
  (File > Import > Upload > Replace). Không đổi quyền chia sẻ file.""")
    txt = _section(txt, "Model", """
Bạn là ChatGPT (Codex) làm quản lý thay Claude. App tự chọn model cho worker Gemini theo chế độ và tự hạ khi sắp hết hạn mức —
KHÔNG truyền --model cho worker trừ khi có lý do rõ. Không có sub-agent: việc code/file nhỏ thì tự làm bằng lệnh.""")
    txt = txt.replace("Tạo theo mục Chia việc (Gemini trước, lỗi thì codex $imagegen)", "Tạo bằng gemini, lỗi thì tự tạo ($imagegen)")
    if IS_WIN:      # Codex chạy lệnh bằng PowerShell: chương trình có đường dẫn trong ngoặc kép phải gọi bằng "&"
        cmd = f'"{py_console()}" "{APP}"'
        txt = txt.replace(cmd, "& " + cmd).replace("## Worker sẵn có trên máy\n",
                          "## Worker sẵn có trên máy\n(Lệnh chạy trong PowerShell: giữ dấu & ở đầu mỗi lệnh gọi app.)\n", 1)
    return txt


def md_name(t):
    return "AGENTS.md" if t.get("pm") == "codex" else "CLAUDE.md"


def build_prompt(t, nxt):
    kind = nxt.get("kind")
    files = nxt.get("files") or []
    fl = f"\nFile mới trong {tdir(t) / 'input'}: {', '.join(files)}." if files else ""
    text = nxt.get("text", "")
    if kind == "start" or not t.get("session_started"):
        base = f"Task mới. Yêu cầu của người dùng:\n\n{t['text']}\n"
        if kind != "start" and text:
            base += f"\nBổ sung của người dùng:\n{text}\n"
        return base + fl + f"\nLàm theo {md_name(t)}."
    if t.get("session_started") and t.get("md_changed"):   # hướng dẫn vừa đổi (app cập nhật / đổi chế độ...) -> đọc lại
        fl += f"\n{md_name(t)} vừa được cập nhật: đọc lại ./{md_name(t)} và áp dụng bản mới nhất (thay cho quy tắc cũ trong phiên)."
    if kind == "answer":
        return f"Người dùng trả lời:\n\n{text}\n{fl}\nTiếp tục task."
    if kind == "urgent":
        return (f"Người dùng vừa gửi tin nhắn trong lúc bạn đang làm (bạn đã bị ngắt):\n\n{text}\n{fl}\n"
                f"Đọc tasks/plan.json, điều chỉnh kế hoạch theo tin nhắn này rồi tiếp tục. Làm theo {md_name(t)}.")
    if kind == "followup":
        return f"Tin nhắn mới của người dùng:\n\n{text}\n{fl}\nLàm theo {md_name(t)}."
    return f"Task bị dừng giữa chừng. Đọc tasks/plan.json và chạy tiếp từ bước đang dở. Làm theo {md_name(t)}."


def gen_title(t):
    """Đặt tên task ngắn như Claude (model nhỏ, tốn rất ít hạn mức). Lỗi thì giữ tên tạm."""
    exe = find_bin("claude")
    try:
        if exe and claude_usable():          # không có Claude trả phí: giữ tên tạm (không tốn hạn mức ChatGPT)
            p = subprocess.run([exe, "-p", "Đặt tiêu đề 3-7 từ, cùng ngôn ngữ, cho yêu cầu dưới đây. "
                                "Chỉ trả về tiêu đề, không dấu ngoặc, không giải thích.\n\n" + t["text"][:2000],
                                "--model", "haiku"], cwd=str(tdir(t)), stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=40, creationflags=NO_WINDOW)
            title = (p.stdout or "").strip().splitlines()[0].strip(" \"'*#`.") if p.returncode == 0 and p.stdout.strip() else ""
            if 2 <= len(title) <= 80 and not t.get("renamed"):
                t["title"] = title
    except Exception:
        pass
    with _tlock:
        t["titling"] = False
        tsave(t)


def start_run(t):
    if not t.get("session_started"):
        t["pm"] = pick_pm() or "claude"         # chọn khi bắt đầu; phiên đã mở thì giữ AI quản lý cũ để nối tiếp được
    t.setdefault("pm", "claude")                # task của bản cũ: phiên Claude
    pm = t["pm"]
    exe = find_bin(pm)
    nxt = t.pop("next", None) or {"kind": "start"}
    d = tdir(t)
    if not exe or not CFG.get("output"):
        t["status"] = "failed"
        tmsg(t, "claude", ("Chưa cài Claude Code" if pm == "claude" else "Chưa cài ChatGPT (Codex)") if not exe
             else "Chưa chọn thư mục kết quả (Cài đặt → Set up)", kind="error")
        return tsave(t)
    if not t.get("folder"):                   # <dự án>/<ngày>/<HH_MM> <tên task> — dễ theo dõi theo dự án và thời gian
        pj = project_of(t)
        nw = datetime.datetime.now()
        base = str(Path(safe_name(pj["name"] if pj else "Chung", 60)) / nw.strftime("%Y-%m-%d") /
                   safe_name(nw.strftime("%H_%M ") + t["title"], 80))
        used = {x.get("folder") for x in TASKS.values() if x is not t}
        name, n = base, 2
        while name in used or (Path(CFG["output"]) / name).exists():
            name, n = f"{base} ({n})", n + 1
        t["folder"] = name
    out_dir(t).mkdir(parents=True, exist_ok=True)
    if t.get("kind") == "code" and not t.get("pdir"):    # Code mode: tự tạo thư mục dự án mới trong thư mục kết quả
        t["pdir"] = str(out_dir(t) / "code")
        Path(t["pdir"]).mkdir(parents=True, exist_ok=True)
    write_claude_md(t)
    d = wdir(t)
    q = d / "question.md"
    if q.exists():
        q.unlink()
    ef = (wdir(t) / "tasks" / "effort.txt")
    hard = t.get("mode") == "pro" and ef.exists() and ef.read_text("utf-8", "ignore").strip() == "xhigh"
    code = t.get("kind") == "code"
    cm, ce = pick_model(pm, "code" if code else t.get("mode", "balanced"), hard)   # Pro + bước quá khó: mức cao nhất
    if code:
        code_checkpoint(t)                      # điểm khôi phục trước khi sửa code (nút Khôi phục)
    t["_effort"], t["_hard"] = ce, hard
    if pm == "codex":
        # ChatGPT làm quản lý: không sandbox (như Claude: cần gọi worker, ghi OUTPUT, mạng); model mặc định của tài khoản
        # (gói Free/Plus tự đúng), chỉ chỉnh mức suy nghĩ. Câu trả lời cuối ghi ra .pm_last.md.
        last = d / ".pm_last.md"
        last.unlink(missing_ok=True)
        args = [exe, "exec"] + (["resume", t["session"]] if t.get("session_started") else []) + [
            "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-o", str(last),
            "-c", f'model_reasoning_effort="{ce}"'] + (DRIVE_APPROVE if GOOGLE_LINK.search(t.get("text", "") + nxt.get("text", "")) else []) + (
            [] if t.get("session_started") else ["-C", str(d)]) + [build_prompt(t, nxt)]
    else:
        args = [exe, "-p", build_prompt(t, nxt), "--output-format", "stream-json", "--verbose",
                "--permission-mode", "acceptEdits",
                "--allowedTools", "Bash,Read,Write,Edit,MultiEdit,Glob,Grep,LS,WebSearch,WebFetch,Task,Agent,TodoWrite,"
                                  "ToolSearch,mcp__claude_ai_Google_Drive", "--model", cm]
        if t.get("session_started"):
            args += ["--resume", t["session"]]
        else:
            t["session"] = str(uuid.uuid4())
            args += ["--session-id", t["session"]]
        for f in dict.fromkeys([str(out_dir(t))] + ([t["pdir"]] if code else []) + drives()):
            args += ["--add-dir", f]
    t.pop("md_changed", None)                   # đã nhắc trong prompt của lượt này
    t.update(status="running", started=now(), _pause=False, _cancel=False, _quit=False, _redirect=None)
    t["wi"] = tmsg(t, "work", "", status="running", started=now(), started_ts=time.time(), steps=[])
    tsave(t)
    try:
        proc = subprocess.Popen(args, cwd=str(d), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env=dict(os.environ, AIO_INPUT=str(tdir(t) / "input"), AIO_MODE="code" if code else t.get("mode", "balanced"),
                                         AIO_HARD="1" if t.pop("_hard", False) else "",
                                         CLAUDE_CODE_EFFORT_LEVEL=t.pop("_effort", "medium") or "medium"),
                                creationflags=NO_WINDOW | BELOW_NORMAL)
    except Exception as e:
        end_work(t, "failed")
        t["status"] = "failed"
        tmsg(t, "claude", str(e), kind="error")
        return tsave(t)
    PROCS[t["id"]] = proc
    t["pid"] = proc.pid
    tsave(t)
    threading.Thread(target=watch, args=(t, proc, nxt), daemon=True).start()


def summarize_tool(b):
    name = b.get("name", "")
    inp = b.get("input") or {}
    s = str(inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern")
            or inp.get("url") or inp.get("query") or inp.get("prompt") or "").replace("\n", " ")
    cmd_ = str(inp.get("command") or "").replace("\n", " ")
    m = re.search(r" worker (codex|gemini) .*?--out\s+\"?([^\s\"]+)", cmd_)
    if m:
        sid = re.split(r"[\\/]", m.group(2).rstrip("/\\"))[-1]
        return f"Giao {sid} cho {'ChatGPT' if m.group(1) == 'codex' else 'Gemini'}" + (" (sửa file trực tiếp)" if " --edit" in cmd_ else "") + (" (sửa file Google)" if " --drive" in cmd_ else "")
    if " check " in cmd_:
        return "Kiểm tra kết quả"
    label = {"Read": "Đọc", "Write": "Ghi", "Edit": "Sửa", "MultiEdit": "Sửa", "Glob": "Tìm file", "Grep": "Tìm",
             "LS": "Xem thư mục", "WebSearch": "Tìm trên web", "WebFetch": "Đọc web", "Task": "Sub-agent",
             "Agent": "Sub-agent", "TodoWrite": "Cập nhật kế hoạch", "Bash": "Chạy"}.get(name, name)
    if name in ("Read", "Write", "Edit", "MultiEdit") and s:
        s = re.split(r"[\\/]", s)[-1]
    return f"{label}: {s[:110]}" if s and name != "TodoWrite" else label


def _scan(od, since):
    if not od.is_dir():
        return []
    fs = [f for f in od.rglob("*") if f.is_file() and ".work" not in f.parts and STORE_NAME not in f.parts
          and f.stat().st_mtime >= since - 1]
    fs.sort(key=str)
    return [str(f.relative_to(od)).replace("\\", "/") for f in fs]


def too_wide(p):
    """Cả ổ đĩa, thư mục người dùng hoặc cha của nó (quá rộng để quét/cho AI sửa)."""
    home = Path.home().resolve()
    return len(p.parts) <= 1 or p.parent == Path("/Volumes") or p == home or p in home.parents


def path_in_text(text):
    """Thư mục người dùng/Claude nêu trong câu trả lời (vd export vào chỗ khác thư mục mặc định)."""
    root = r"(?:[A-Za-z]:[\\/]|/Users/|/Volumes/|/home/|~/)"          # Windows (C:\...) và Mac (/Users/..., ~/...)
    cands = re.findall(r"`(" + root + r"[^`\n]+)`", text or "") + re.findall(r"(" + root + r"[^\n`*\"<>|]+)", text or "")
    for c in cands:
        c = c.strip()
        for _ in range(200):                 # bớt dần ký tự cuối cho tới khi ra đường dẫn có thật
            if len(c) <= 3:
                break
            p = Path(os.path.expanduser(c.rstrip(" .,;:")))
            if p.exists():
                p = p if p.is_dir() else p.parent
                try:
                    rp = p.resolve()
                except OSError:
                    break
                if too_wide(rp):
                    break                    # quá rộng (ổ đĩa, thư mục người dùng): quét sẽ rất chậm -> bỏ
                return p
            c = c[:-1]
    return None


def out_file(tid, idx, name):
    """File kết quả gắn với 1 câu trả lời (chỉ trong thư mục của câu trả lời đó)."""
    t = TASKS.get(tid)
    try:
        m = t["messages"][int(idx)]
    except Exception:
        return None
    if name not in (m.get("files") or []):
        return None
    base = Path(m["dir"]) if m.get("dir") else out_dir(t)
    f = (base / name).resolve()
    try:
        f.relative_to(base.resolve())
    except ValueError:
        return None
    return f if f.is_file() else None


_thumbs = {}


def thumb(f, size=360):
    key = (str(f), f.stat().st_mtime)
    if key in _thumbs:
        return _thumbs[key]
    try:
        import io
        from PIL import Image
        im = Image.open(f)
        im.thumbnail((size, size))
        b = io.BytesIO()
        im.save(b, "PNG")
        data = b.getvalue()
    except Exception:
        return None
    if len(_thumbs) > 200:
        _thumbs.clear()
    _thumbs[key] = data
    return data


def ofile_act(d):
    f = out_file(d.get("t", ""), d.get("i", ""), d.get("f", ""))
    if not f:
        return {"error": "Không còn file"}
    op = d.get("op")
    if op == "open":
        open_path(f)
    elif op == "reveal":
        open_path(f.parent, str(f) if IS_WIN else "")
    elif op == "path":
        return {"ok": True, "path": str(f)}
    return {"ok": True}


def new_files(t, since, text=""):
    """File mới tạo trong lượt làm; trả (danh sách, thư mục để mở)."""
    fs = _scan(out_dir(t), since) if CFG.get("output") and t.get("folder") else []
    if fs:
        return fs[:30], ""
    d = path_in_text(text)
    if d:
        fs = _scan(d, since)
        if fs:
            return fs[:30], str(d)
    return [], ""


def codex_event(t, ev, cx):
    """1 dòng sự kiện JSON của `codex exec --json` (ChatGPT làm quản lý) -> bước hiển thị; trả kết quả khi lượt xong."""
    typ, it = ev.get("type"), ev.get("item") or {}
    if typ == "thread.started" and ev.get("thread_id") and not t.get("session_started"):
        t["session_started"], t["session"] = True, ev["thread_id"]
        tsave(t)
    elif typ == "item.started" and it.get("type") == "command_execution":
        step(t, summarize_tool({"name": "Bash", "input": {"command": str(it.get("command") or "")}}))
    elif typ == "item.completed" and it.get("type") == "agent_message" and str(it.get("text") or "").strip():
        cx["last"] = str(it["text"]).strip()
        step(t, cx["last"].split("\n")[0])
    elif typ == "item.completed" and it.get("type") == "file_change":
        names = [re.split(r"[\\/]", str(c.get("path", "")))[-1] for c in it.get("changes") or [] if isinstance(c, dict)]
        step(t, "Sửa: " + ", ".join(names[:5]) if names else "Sửa file")
    elif typ == "turn.completed":
        return {"result": cx["last"]}
    elif typ == "turn.failed":
        return {"is_error": True, "result": str((ev.get("error") or {}).get("message") or cx["err"] or "ChatGPT dừng giữa chừng")}
    elif typ == "error" and ev.get("message"):
        cx["err"] = str(ev["message"])
    return None


def watch(t, p, nxt):
    raw = open(wdir(t) / "run.log", "a", encoding="utf-8")
    raw.write(f"\n===== {now()} {nxt.get('kind')} =====\n")
    result = None
    w = work(t)
    since = (w or {}).get("started_ts", time.time())
    pm = t.get("pm") or "claude"
    cx = {"last": "", "err": ""}               # ChatGPT làm quản lý: câu trả lời/lỗi gần nhất
    for line in p.stdout:
        raw.write(line)
        raw.flush()
        try:
            ev = json.loads(line)
        except Exception:
            continue
        typ = ev.get("type")
        if pm == "codex":
            result = codex_event(t, ev, cx) or result
            continue
        if ev.get("session_id") and not t.get("session_started"):
            t["session_started"] = True
            t["session"] = ev["session_id"]
            tsave(t)
        if typ == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    step(t, b["text"].strip().split("\n")[0])
                    lo_ = b["text"].lower()
                    if "limit" in lo_ and any(k in lo_ for k in ("approaching", "% of your", "remaining")):
                        note_warn("claude", b["text"])
                elif b.get("type") == "tool_use":
                    step(t, summarize_tool(b))
        elif typ == "result":
            result = ev
    err = p.stderr.read()
    if err.strip():
        raw.write("--- stderr ---\n" + err)
    raw.close()
    p.wait()
    if pm == "codex" and result and not result.get("is_error"):
        f = wdir(t) / ".pm_last.md"
        result["result"] = (f.read_text("utf-8", "replace").strip() if f.is_file() else "") or cx["last"] or "Xong."
    elif pm == "codex" and not result and cx["err"]:
        result = {"is_error": True, "result": cx["err"]}
    PROCS.pop(t["id"], None)
    with _tlock:
        q = wdir(t) / "question.md"
        t.pop("pid", None)
        if t.get("_quit"):
            t["status"] = "running"        # app đóng: giữ running để lần mở sau tự chạy tiếp
        elif t.get("_redirect"):
            end_work(t, "interrupted")
            t["status"] = "queued"
            t["next"] = t["_redirect"]
        elif t.get("_cancel"):
            end_work(t, "cancelled")
            t["status"] = "cancelled"
        elif t.get("_pause"):
            end_work(t, "paused")
            t["status"] = "paused"
        elif t.get("kind") == "code" and read_install(t):
            end_work(t, "done")
            t["status"] = "waiting"
            t["install"] = read_install(t)
            (wdir(t) / "install_request.json").unlink(missing_ok=True)
            tmsg(t, "claude", t["install"]["why"] or "Cần cài thêm phần mềm để làm tiếp.", kind="install",
                 items=[i["name"] for i in t["install"]["items"]])
        elif q.exists() and q.read_text(encoding="utf-8").strip():
            end_work(t, "done")
            t["status"] = "waiting"
            qt = q.read_text(encoding="utf-8").strip()
            fs, fd = new_files(t, since, qt)
            tmsg(t, "claude", qt, kind="question", files=fs, dir=fd)
        elif p.returncode == 0 and result and not result.get("is_error"):
            end_work(t, "done")
            t["status"] = "done"
            t["finished"] = now()
            rt = (result.get("result") or "Xong.").strip()
            fs, fd = new_files(t, since, rt)
            tmsg(t, "claude", rt, files=fs, dir=fd)
            threading.Thread(target=finish_clean, args=(t,), daemon=True).start()
        else:
            msg = (result or {}).get("result") or err.strip()[-400:] or f"exit {p.returncode}"
            if is_limit(msg[-1500:]) or is_limit(err):          # AI quản lý hết hạn mức: chờ tới giờ reset rồi tự chạy tiếp
                lm = note_limit(pm, msg + "\n" + err[-2000:])
                end_work(t, "limit")
                t["status"] = "queued"
                t["hold_until"] = lm["until"]
                t["next"] = {"kind": "resume"}
                hh = datetime.datetime.fromtimestamp(lm["until"]).strftime("%H:%M")
                tmsg(t, "claude", f"{'Claude' if pm == 'claude' else 'ChatGPT'} hết hạn mức. Tự chạy tiếp lúc **{hh}**{'' if lm['known'] else ' (ước tính, thử lại mỗi 30 phút)'}.", kind="error")
            else:
                end_work(t, "failed")
                t["status"] = "failed"
                tmsg(t, "claude", msg, kind="error")
        t["_redirect"] = None
        tsave(t)
    try:
        registry(force=True)
    except Exception:
        pass


_ready = {"t": 0.0}


def pm_ready(t):
    """Có AI làm quản lý chưa. Chưa: tự mở đăng nhập Claude 1 lần cho task này, task chờ rồi tự chạy."""
    if time.time() - _ready["t"] > 60 or (t.get("need_login") and time.time() - _ready["t"] > 10):
        _ready["t"] = time.time()          # kiểm tra đăng nhập ở nền (lệnh chạy vài giây, không giữ khóa task)

        def chk():
            with _status_lock:
                for ai in ("claude", "codex"):
                    if not CFG["ais"].get(ai, {}).get("pending"):
                        ai_status(ai)
                save_cfg()
        threading.Thread(target=chk, daemon=True).start()
    pm = (t.get("pm") or "claude") if t.get("session_started") else pick_pm()
    if pm and (pm != "codex" or codex_usable()) and (pm != "claude" or claude_usable()):
        if t.pop("need_login", None):
            tsave(t)
        return True
    if not t.get("need_login"):
        t["need_login"] = True
        tsave(t)
        if not CFG["ais"].get("claude", {}).get("ok") and not CFG["ais"].get("codex", {}).get("ok"):
            ai_login("claude")
    return False


SCHED = {"beat": 0.0}


def scheduler():
    while True:
        SCHED["beat"] = time.time()
        try:
            sched_tick()
        except Exception:
            import traceback
            dlog("scheduler error:\n" + traceback.format_exc())
        time.sleep(1)


def sched_tick():
    if not _tlock.acquire(timeout=60):           # bị kẹt khóa: ghi lại các luồng để tìm lỗi
        import traceback
        dlog("scheduler: _tlock busy >60s\n" + "\n".join(
            "".join(traceback.format_stack(f)) for f in sys._current_frames().values())[-6000:])
        return
    try:
        running = sum(1 for t in TASKS.values() if t["status"] == "running")
        queued = sorted((t for t in TASKS.values() if t["status"] == "queued"), key=lambda x: x["created"])
        lm = limits()
        for t in queued:
            if running >= MAX_RUNNING:
                break
            if t.get("hold_until", 0) > time.time() or lm.get(t.get("pm") or pick_pm() or "claude"):
                continue          # AI quản lý hết hạn mức: chờ tới giờ reset (mọi task cùng quản lý cùng chờ)
            if t.pop("hold_until", None):
                tsave(t)
            if not pm_ready(t):
                continue          # chờ đăng nhập (đã tự mở trang đăng nhập Claude)
            if t.get("titling") and time.time() - t.get("created_ts", 0) < 30:
                continue          # chờ đặt tên xong để thư mục kết quả có tên đẹp
            try:
                start_run(t)
            except Exception as e:
                import traceback
                dlog(f"start_run {t['id']}:\n" + traceback.format_exc())
                t["status"] = "failed"
                tmsg(t, "claude", f"Không khởi động được task: {e}", kind="error")
                tsave(t)
                continue
            running += 1
    finally:
        _tlock.release()


def act(tid, action, text="", files=None, template=""):
    t = TASKS.get(tid)
    if not t:
        return {"error": "Không tìm thấy task"}
    s = t["status"]
    files = [f for f in (files or []) if (tdir(t) / "input" / f).exists()]
    text = (text or "").strip()
    with _tlock:
        if action == "pause" and s == "running":
            t["_pause"] = True
            kill_tree(PROCS.get(tid))
        elif action == "pause" and s == "queued":
            t["status"] = "paused"
        elif action == "cancel" and s in ("running", "queued", "paused", "waiting", "failed"):
            if s == "running":
                t["_cancel"] = True
                kill_tree(PROCS.get(tid))
            else:
                t["status"] = "cancelled"
        elif action == "resume" and s in ("paused", "failed"):
            t["status"] = "queued"
            t.setdefault("next", {"kind": "resume"})
        elif action == "send" and (text or files or template):
            tp = next((x for x in templates() if x["id"] == template), None)
            if tp:                                     # chọn template trong task cũ: áp quy trình mẫu cho lượt này
                t["template"] = tp
                text = (f"[Dùng template: {tp['name']}] " + text).strip()
                if wdir(t).exists():
                    write_claude_md(t)
            tmsg(t, "user", text, files=files)
            if s == "running":                         # tin gấp: ngắt, Claude điều chỉnh rồi làm tiếp
                t["_redirect"] = {"kind": "urgent", "text": text, "files": files}
                kill_tree(PROCS.get(tid))
            elif s == "queued" and t.get("next"):      # chưa chạy: gộp vào yêu cầu đang chờ
                n = t["next"]
                n["text"] = (n.get("text", "") + "\n\n" + text).strip()
                n["files"] = (n.get("files") or []) + files
                if n.get("kind") == "resume":
                    n["kind"] = "urgent"
            else:
                t["status"] = "queued"
                t["next"] = {"kind": "answer" if s == "waiting" else "followup", "text": text, "files": files}
        elif action == "mode" and (text == "code") != (t.get("kind") == "code"):
            return {"error": "Code mode cần thư mục dự án: tạo task mới ở chế độ Code" if text == "code"
                    else "Task Code chỉ chạy ở chế độ Code"}
        elif action == "mode" and MODE_ALIAS.get(text, text) in MODES:
            t["mode"] = norm_mode(text)                           # áp dụng từ lượt chạy kế tiếp
            write_claude_md(t)
        elif action == "install" and s == "waiting" and t.get("install") and not t.get("installing"):
            t["installing"] = True
            threading.Thread(target=run_install, args=(t,), daemon=True).start()
        elif action == "restore" and t.get("kind") == "code" and s not in ("running", "queued"):
            r = code_restore(t)
            tsave(t)
            return r
        elif action == "pin":
            t["pinned"] = not t.get("pinned")
        elif action == "rename" and text:
            t["title"] = text[:80]
            t["renamed"] = True
        elif action == "open" and text and Path(text).is_dir() and any(
                m.get("dir") == text for m in t.get("messages", [])):
            open_path(Path(text))
            return {"ok": True}
        elif action == "open":
            open_path(out_dir(t) if CFG.get("output") and out_dir(t).exists() else tdir(t))
            return {"ok": True}
        elif action == "delete" and s not in ("running", "queued"):
            TASKS.pop(tid, None)
            shutil.rmtree(tdir(t), ignore_errors=True)
            return {"ok": True}
        else:
            return {"error": "Không thực hiện được ở trạng thái này"}
        tsave(t)
    return {"ok": True}


def open_task_file(tid, rel):
    t = TASKS.get(tid)
    if not t or not CFG.get("output"):
        return {"error": "Không tìm thấy"}
    base = out_dir(t).resolve()
    f = (base / rel).resolve()
    if base not in f.parents or not f.exists():
        return {"error": "Không tìm thấy file"}
    open_path(f)
    return {"ok": True}


# ---------------------------------------------------------------- Learning: bài học chung của mọi người dùng
# Mỗi task xong: Claude ghi bài học chung vào lessons.json -> app lọc dữ liệu riêng, ghi hàng đợi learn_queue.jsonl ->
# gửi về Google Form (Sheet của team). Hàng tuần tác vụ hẹn giờ lọc, gộp vào src/learnings.md -> phát hành bản mới.
def learn_src(name, embedded):
    if embedded or VERSION != "dev":
        return embedded
    f = APP.parent / name                       # chạy từ mã nguồn (dev): đọc thẳng file trong src/
    return f.read_text("utf-8") if f.is_file() else ""


def learn_rules():
    """Quy tắc đang áp dụng (mục '## Đang áp dụng' trong learnings.md: mỗi dòng '- ...')."""
    txt = learn_src("learnings.md", LEARN_MD)
    m = re.search(r"(?ms)^## Đang áp dụng\s*\n(.*?)(?=^## |\Z)", txt)
    return [x.strip()[2:].strip() for x in (m.group(1) if m else "").splitlines() if x.strip().startswith("- ")]


PRIVATE_RE = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"https?://\S+|www\.\S+"), "<link>"),
    (re.compile(r"\\\\[^\s\\]+(?:\\[^\s\\]+)*|[A-Za-z]:[\\/][^\s,;\"']*|(?:/Users|/Volumes|/home|~)/[^\s,;\"']*"), "<đường dẫn>"),
    (re.compile(r"\b(?:AIza[\w-]{20,}|sk-[\w-]{20,}|gh[pousr]_[\w]{20,})\b"), "<key>"),
    (re.compile(r"\b\d{6,}\b"), "<số>"),
]


def scrub(text, n=300):
    """Bỏ dữ liệu riêng khỏi bài học trước khi gửi: email, link, đường dẫn, key, dãy số dài."""
    text = str(text or "")
    for rx, rep in PRIVATE_RE:
        text = rx.sub(rep, text)
    return re.sub(r"\s+", " ", text).strip()[:n]


def learn_report(t):
    """Báo cáo ẩn danh của 1 task: loại việc, bài học (đã lọc), AI nào làm/bao lâu/đạt không. Không có nội dung task."""
    w = wdir(t)
    les = rjson(w / "lessons.json", {}) or {}
    les = les if isinstance(les, dict) else {"lessons": les if isinstance(les, list) else []}
    cat = str(les.get("category") or "khác").strip().lower()[:40]
    works = [m for m in t.get("messages", []) if m.get("role") == "work"]
    subs = [{"worker": s_.get("worker", ""), "model": s_.get("model", ""), "status": s_.get("status", ""),
             "seconds": s_.get("seconds"), "attempt": s_.get("attempt")} for s_ in subtasks(t)][:30]
    return {"v": 1, "release": RELEASE, "os": "mac" if IS_MAC else "win" if IS_WIN else "other", "at": int(time.time()),
            "kind": t.get("kind") or "task", "mode": t.get("mode", ""), "pm": t.get("pm", "claude"), "category": cat,
            "lessons": [scrub(x) for x in (les.get("lessons") or []) if str(x).strip()][:5],
            "turns": len(works), "seconds": round(sum((m.get("ended_ts") or m.get("started_ts", 0)) - m.get("started_ts", 0) for m in works)),
            "status": t.get("status"), "subtasks": subs,
            "plans": {k: CFG["ais"].get(k, {}).get("plan", "") for k in ("claude", "codex")},
            "models": {"gemini": (CFG.get("gemini_list") or [])[:30], "gemini_api": (CFG.get("gemini_api_models") or [])[:30]}}


def learn_queue(t):
    try:
        if t.get("learned"):
            return
        ends = [m.get("ended_ts") or 0 for m in t.get("messages", []) if m.get("role") == "work"]
        if not (wdir(t) / "lessons.json").exists() and time.time() - max(ends or [0]) > 3600:
            t["learned"] = True                # task cũ (trước khi có learning): không gửi lại
            return
        rep = learn_report(t)
        with open(DATA / "learn_queue.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        t["learned"] = True
    except Exception as e:
        dlog(f"learn_queue {t.get('id')}: {e}")


def learn_form():
    raw = learn_src("learn_form.json", LEARN_FORM)
    try:
        d = json.loads(raw) if raw else {}
    except Exception:
        d = {}
    return (d.get("url") or "").strip(), (d.get("entry") or "").strip()


def learn_send():
    """Gửi hàng đợi bài học về Google Form của team (mỗi báo cáo 1 lần gửi). Chưa có Form -> giữ lại gửi sau."""
    import urllib.parse
    import urllib.request
    url, entry = learn_form()
    f = DATA / "learn_queue.jsonl"
    if not url or not entry or not f.is_file():
        return 0
    lines = [x for x in f.read_text("utf-8", "replace").splitlines() if x.strip()][-500:]
    left, sent = [], 0
    for i, line in enumerate(lines):
        try:
            body = urllib.parse.urlencode({entry: line}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"User-Agent": "maestro"}), timeout=20).read(200)
            sent += 1
        except Exception as e:
            dlog(f"learn_send: {e}")
            left = lines[i:]
            break
    f.write_text("".join(x + "\n" for x in left), encoding="utf-8")
    if sent:
        CFG["learn_sent"] = int(CFG.get("learn_sent") or 0) + sent
        save_cfg()
    return sent


def learn_loop():
    while True:
        time.sleep(600)
        try:
            learn_send()
        except Exception as e:
            dlog(f"learn_loop: {e}")


def learn_state():
    f = DATA / "learn_queue.jsonl"
    pending = sum(1 for x in f.read_text("utf-8", "replace").splitlines() if x.strip()) if f.is_file() else 0
    return {"rules": learn_rules(), "pending": pending, "sent": int(CFG.get("learn_sent") or 0),
            "on": bool(learn_form()[0])}


# ---------------------------------------------------------------- Project Brain + Workflow Templates
_plock = threading.Lock()


def projects():
    return rjson(STORE / "projects.json", []) or []


def save_projects(ps):
    wjson(STORE / "projects.json", ps)


def project_of(t):
    return next((p for p in projects() if p["id"] == t.get("project")), None)


def project_api(d):
    a = d.get("action", "")
    with _plock:
        ps = projects()
        p = next((x for x in ps if x["id"] == d.get("id")), None)
        if a == "get":
            return p or {"error": "Không tìm thấy dự án"}
        if a == "create":
            name = (d.get("name") or "").strip()[:60]
            if not name:
                return {"error": "Nhập tên dự án"}
            p = next((x for x in ps if x["name"].lower() == name.lower()), None)
            if not p:
                p = {"id": "p" + secrets.token_hex(4), "name": name, "brain": "", "created": now()}
                ps.append(p)
                save_projects(ps)
            if d.get("task") in TASKS:
                TASKS[d["task"]]["project"] = p["id"]
                tsave(TASKS[d["task"]])
            return {"ok": True, "id": p["id"]}
        if not p and a != "assign":
            return {"error": "Không tìm thấy dự án"}
        if a == "save":
            p["brain"] = (d.get("brain") or "")[:20000]
            if (d.get("name") or "").strip():
                p["name"] = d["name"].strip()[:60]
            save_projects(ps)
            for t in TASKS.values():
                if t.get("project") == p["id"] and t["status"] not in ("running",) and wdir(t).exists():
                    write_claude_md(t)
            return {"ok": True}
        if a == "delete":
            save_projects([x for x in ps if x is not p])
            for t in TASKS.values():
                if t.get("project") == p["id"]:
                    t.pop("project", None)
                    tsave(t)
            return {"ok": True}
        if a == "assign":
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            t["project"] = p["id"] if p else ""
            tsave(t)
            if wdir(t).exists() and t["status"] != "running":
                write_claude_md(t)
            return {"ok": True}
        if a in ("accept", "reject"):              # đề xuất của Claude cho bộ nhớ dự án: người dùng duyệt
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            sug = brain_suggest(t)
            i = int(d.get("idx", -1))
            if not 0 <= i < len(sug):
                return {"error": "Đề xuất không còn"}
            item = sug.pop(i)
            if a == "accept":
                p["brain"] = (p.get("brain", "").rstrip() + "\n- " + item).strip()
                save_projects(ps)
            t["brain_suggest"] = sug
            f = wdir(t) / "brain_suggest.json"
            if f.exists():
                wjson(f, sug)
            tsave(t)
            return {"ok": True}
    return {"error": "bad request"}


def brain_suggest(t):
    f = wdir(t) / "brain_suggest.json"
    v = rjson(f, None) if f.exists() else None
    if isinstance(v, list):
        return [str(x.get("text") if isinstance(x, dict) else x)[:400] for x in v if x][:20]
    return list(t.get("brain_suggest") or [])


def templates():
    return rjson(STORE / "templates.json", []) or []


def template_api(d):
    a = d.get("action", "")
    with _plock:
        ts = templates()
        if a == "save":
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            subs = [s for m in task_detail(t)["messages"] if m["role"] == "work" for s in m.get("subtasks", [])]
            asks = [m["text"] for m in t.get("messages", []) if m["role"] == "user" and m.get("text")]
            r = t.get("recipe") or {}
            tp = {"id": "w" + datetime.datetime.now().strftime("%y%m%d%H%M%S"),
                  "name": (d.get("name") or t["title"]).strip()[:80], "created": now(), "from": t["id"],
                  "mode": t.get("mode", "balanced"), "project": t.get("project", ""),
                  "asks": asks[:20], "criteria": r.get("criteria", "")[:8000],
                  "steps": [{"title": s.get("title", ""), "worker": s.get("worker", ""), "model": s.get("model", "")} for s in subs][:40]}
            ts.append(tp)
            wjson(STORE / "templates.json", ts)
            t["tpl_saved"] = tp["id"]
            tsave(t)
            return {"ok": True, "id": tp["id"]}
        if a == "delete":
            wjson(STORE / "templates.json", [x for x in ts if x["id"] != d.get("id")])
            return {"ok": True}
    return {"error": "bad request"}


def new_task(text, mode="balanced", project="", template="", kind="", pdir=""):
    if not text.strip():
        return {"error": "Chưa nhập yêu cầu"}
    kind = "code" if norm_mode(mode) == "code" or kind == "code" else ""
    if kind == "code" and pdir:                # thư mục dự án có sẵn (tuỳ chọn); không có -> tự tạo mới khi chạy
        p = Path(pdir).expanduser()
        bad = edit_refused(p.resolve()) if p.is_dir() else f"Không thấy thư mục dự án: {pdir}"
        if bad:
            return {"error": bad}
    tid = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    while tid in TASKS:
        tid += "b"
    t = {"id": tid, "title": re.sub(r"\s+", " ", text.strip())[:50], "text": text.strip(),
         "status": "draft", "created": now(), "created_ts": time.time(), "messages": [],
         "mode": norm_mode(mode), "w2": True}
    if kind == "code":
        t.update(kind="code", pdir=str(Path(pdir).expanduser()) if pdir else "")
    if project and any(p["id"] == project for p in projects()):
        t["project"] = project
    tp = next((x for x in templates() if x["id"] == template), None)
    if tp:
        t["template"] = tp
    (tdir(t) / "input").mkdir(parents=True, exist_ok=True)
    with _tlock:
        TASKS[tid] = t
        tsave(t)
    return {"ok": True, "id": tid}


def submit_task(tid, files):
    t = TASKS.get(tid)
    if not t or t["status"] != "draft":
        return {"error": "Task không hợp lệ"}
    with _tlock:
        files = [f for f in files if (tdir(t) / "input" / f).exists()]
        tmsg(t, "user", t["text"], files=files)
        t["status"] = "queued"
        t["next"] = {"kind": "start", "files": files}
        t["titling"] = True
        t["created_ts"] = time.time()
        tsave(t)
    threading.Thread(target=gen_title, args=(t,), daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- AI accounts
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _jwt(tok):
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)) or {}
    except Exception:
        return {}


def _jwt_email(tok):
    return _jwt(tok).get("email", "")


PAID_CLAUDE = {"pro", "max", "team", "enterprise"}


def claude_plan(j):
    """Gói Claude từ `claude auth status --json` -> (tên gói, trả phí?). None = không rõ (bản CLI cũ) -> coi như trước đây."""
    if not j.get("loggedIn"):
        return "", None
    plan = j.get("subscriptionType") or ""
    if j.get("authMethod") not in (None, "claude.ai") or "storedClaudeAiLogin" not in j and "subscriptionType" not in j:
        return plan, None                     # đăng nhập kiểu khác / CLI không báo gói
    return plan or "free", plan in PAID_CLAUDE


def codex_plan():
    """Gói ChatGPT ghi trong file đăng nhập Codex (free, plus, pro, team...). '' = không rõ."""
    f = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    toks = (rjson(f) or {}).get("tokens") or {}
    return str((_jwt(toks.get("id_token", "")).get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or "").lower()


def ai_email(ai):
    """Email tài khoản đang đăng nhập. Chỉ đọc email, không đọc/ghi token ra ngoài."""
    home = Path.home()
    try:
        if ai == "claude":
            for f in (home / ".claude.json", home / ".claude" / ".claude.json"):
                acc = (rjson(f) or {}).get("oauthAccount") or {}
                if acc.get("emailAddress"):
                    return acc["emailAddress"]
        elif ai == "codex":
            f = Path(os.environ.get("CODEX_HOME") or home / ".codex") / "auth.json"
            toks = (rjson(f) or {}).get("tokens") or {}
            e = _jwt_email(toks.get("id_token", ""))
            if e:
                return e
        elif ai == "gemini":
            for f in (home / ".gemini" / "google_accounts.json", home / ".gemini" / "antigravity-cli" / "google_accounts.json"):
                act = (rjson(f) or {}).get("active")
                if act:
                    return act
            exe = find_bin("agy")
            if exe:
                p = subprocess.run([exe, "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WINDOW)
                m = EMAIL_RE.search((p.stdout or "") + (p.stderr or ""))
                if m:
                    return m.group(0)
    except Exception:
        pass
    return ""


def claude_usable():
    c = CFG["ais"].get("claude", {})
    return bool(find_bin("claude")) and bool(c.get("ok")) and c.get("paid") is not False


def codex_usable():
    c = CFG["ais"].get("codex", {})
    return bool(find_bin("codex")) and bool(c.get("ok"))


def pick_pm():
    """Ai làm quản lý (PM): Claude gói trả phí như cũ; không có thì ChatGPT (Codex, kể cả gói Free); không có ai -> ''."""
    return "claude" if claude_usable() else "codex" if codex_usable() else ""


def free_setup():
    """Không có tài khoản trả phí nào (Claude/ChatGPT) -> chế độ Free: tiết kiệm tối đa + mời thêm Gemini API key."""
    a = CFG["ais"]
    return not claude_usable() and a.get("codex", {}).get("paid") is not True


def setup_state():
    pm = pick_pm()
    return {"pm": pm, "free": free_setup(), "gemini_key": bool(CFG.get("gemini_key")),
            "gemini_api_models": len(CFG.get("gemini_api_models") or [])}


def set_gemini_key(key):
    """Lưu Gemini API key (chỉ trên máy này, data/config.json). Kiểm tra key bằng cách lấy danh sách model."""
    key = re.sub(r"\s+", "", key)
    if not key:
        CFG.pop("gemini_key", None)
        CFG.pop("gemini_api_models", None)
        save_cfg()
        return {"ok": True, "msg": "Đã xoá Gemini API key"}
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,80}", key):
        return {"error": "Không giống API key của Google (thường bắt đầu bằng AIza…)"}
    try:
        models = gemini_api_models(key)
    except Exception as e:
        return {"error": f"Key chưa dùng được: {e}"[:200]}
    if not models:
        return {"error": "Key hợp lệ nhưng không có model Gemini nào dùng được"}
    CFG["gemini_key"], CFG["gemini_api_models"] = key, models
    save_cfg()
    return {"ok": True, "msg": f"Đã lưu key · {len(models)} model Gemini dùng được"}


def ai_state():
    lm = limits()
    return {k: {"installed": bool(find_bin(v["bin"])), **CFG["ais"].get(k, {}),
                **({"limit_until": lm[k]["until"], "limit_known": lm[k].get("known", True)} if k in lm else {})}
            for k, v in AIS.items()}


LOGINS = {}          # ai -> Popen của tiến trình đăng nhập đang chạy
_status_lock = threading.Lock()


def _run_hidden(args, timeout, cwd=None):
    return subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def ai_status(ai):
    """Trạng thái đăng nhập. Claude/ChatGPT: lệnh trạng thái (tức thì, không tốn hạn mức).
    Gemini không có lệnh trạng thái -> giữ kết quả lần đăng nhập gần nhất."""
    exe = find_bin(AIS[ai]["bin"])
    cur = CFG["ais"].setdefault(ai, {})
    if not exe:
        cur.update(ok=False, msg="Chưa cài")
        return cur
    try:
        if ai == "claude":
            p = _run_hidden([exe, "auth", "status", "--json"], 20)
            j = json.loads(p.stdout[p.stdout.index("{"):]) if "{" in (p.stdout or "") else {}
            ok = bool(j.get("loggedIn"))
            plan, paid = claude_plan(j)
            cur.update(ok=ok, msg="", email=(j.get("email") or ai_email(ai)) if ok else "", plan=plan, paid=paid)
        elif ai == "codex":
            p = _run_hidden([exe, "login", "status"], 45)
            out = ((p.stdout or "") + (p.stderr or "")).lower()
            if "node" in out and ("not recognized" in out or "not found" in out):   # PATH thiếu node -> tìm lại rồi thử 1 lần
                refresh_path()
                dlog(f"codex: node missing; which(node)={shutil.which('node')}; PATH={os.environ.get('PATH', '')[:1500]}")
                if shutil.which("node"):
                    p = _run_hidden([exe, "login", "status"], 45)
                    out = ((p.stdout or "") + (p.stderr or "")).lower()
            ok = p.returncode == 0 and "not logged" not in out
            msg = ""
            if not ok and "not logged" not in out:   # lệnh lỗi (không phải chưa đăng nhập) -> không coi là bị văng
                dlog(f"codex login status rc={p.returncode}: {out[-600:]}")
                if "node" in out and ("not recognized" in out or "not found" in out):
                    msg = ("Không tìm thấy Node.js — chạy lại Maestro_setup.bat (sẽ cài Node), rồi Export log nếu vẫn lỗi"
                           if not shutil.which("node") else "Codex lỗi khi chạy — bấm Export log gửi lại")
                elif (Path.home() / ".codex" / "auth.json").exists():
                    ok = True
            plan = codex_plan() if ok else ""
            cur.update(ok=ok, msg=msg, email=ai_email(ai) if ok else "", plan=plan,
                       paid=(plan != "free") if plan else None)
    except Exception as e:
        cur.setdefault("ok", False)
        cur["msg"] = str(e)[:150]
    return cur


_status_at = {"t": 0.0}


def refresh_status():
    """Kiểm tra đăng nhập Claude/ChatGPT theo yêu cầu (khi đang soạn prompt hoặc mở Cài đặt); tối đa 1 lần/phút."""
    if time.time() - _status_at["t"] < 60:
        return {"ok": True}
    _status_at["t"] = time.time()

    def run():
        for ai in ("claude", "codex"):
            if not CFG["ais"].get(ai, {}).get("pending"):
                with _status_lock:
                    before = dict(CFG["ais"].get(ai, {}))
                    ai_status(ai)
                    if before != CFG["ais"].get(ai):
                        save_cfg()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\\\)|\x1b[=>()][0-9A-B]?")


def dlog(msg):
    """Nhật ký chẩn đoán (đi kèm Export log)."""
    try:
        with open(DATA / "debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{now()}] v{RELEASE} py{sys.version.split()[0]} {msg}\n")
    except Exception:
        pass


class HiddenTerm:
    """Chạy chương trình dạng cửa sổ dòng lệnh ở chế độ ẩn (terminal ảo), đọc màn hình và gõ phím thay người dùng."""

    def __init__(self, args, cwd, env=None):
        self.buf = ""
        self.pid = None
        if IS_WIN:
            LIBS_READY.wait(300)     # thư viện terminal ảo do bộ cài (hoặc ensure_libs lúc mở app) cài
            from winpty import PtyProcess
            self.p = PtyProcess.spawn([str(x) for x in args], cwd=cwd, env=env, dimensions=(50, 500))
            self.pid = self.p.pid
            self._read = lambda: self.p.read(4096)
            self._write = self.p.write
            self.alive = self.p.isalive
        else:
            import pty
            m, s = pty.openpty()
            self.sp = subprocess.Popen(args, cwd=cwd, env=env, stdin=s, stdout=s, stderr=s, close_fds=True, start_new_session=True)
            os.close(s)
            self.pid = self.sp.pid
            self._read = lambda: os.read(m, 4096).decode("utf-8", "replace")
            self._write = lambda t: os.write(m, t.encode())
            self.alive = lambda: self.sp.poll() is None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            while True:
                d = self._read()
                if not d:
                    break
                self.buf = (self.buf + ANSI_RE.sub("", d))[-20000:]
        except Exception:
            pass

    def send(self, text):
        self._write(text)

    def wait_for(self, pattern, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = re.search(pattern, self.buf, re.I)
            if m:
                return m
            if not self.alive():
                return None
            time.sleep(0.3)
        return None

    def poll(self):
        return None if self.alive() else 0

    def kill(self):
        try:
            if IS_WIN:
                self.p.terminate(force=True)
            else:
                self.sp.kill()
        except Exception:
            pass


def gemini_forget():
    """Xóa phiên Antigravity đã lưu (để đổi tài khoản)."""
    try:
        if IS_WIN:
            _run_hidden(["cmdkey", "/delete:gemini:antigravity"], 15)
        f = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        if f.exists():
            f.unlink()
        if os.environ.get("FAKE_STATE"):
            Path(os.environ["FAKE_STATE"], "agy").unlink(missing_ok=True)
    except Exception:
        pass


def gemini_term_login(exe, d, cur):
    """Đăng nhập Antigravity không cần cửa sổ: tool tự chọn Google OAuth, mã xác nhận dán trên trang web của tool."""
    term = HiddenTerm([exe], str(d))
    LOGINS["gemini"] = term
    m = term.wait_for(r"Select login method|not signed in|@[\w-]+\.[\w.-]+", 40)
    if not m:
        return False
    if "@" in m.group(0) and "signed in" not in term.buf.lower():
        return True                       # đã đăng nhập sẵn
    term.wait_for(r"Select login method", 10)
    term.send("\r")                       # chọn 1. Google OAuth -> agy mở trình duyệt
    time.sleep(1.5)
    u = re.search(r"https://accounts\.google\.com/\S+", re.sub(r"[\r\n]+", "", term.buf))
    cur["url"] = u.group(0) if u else ""
    cur["code"] = True                    # trang web hiện ô dán mã
    t0 = time.time()
    while time.time() - t0 < 600 and term.alive():
        if gemini_has_cred():
            time.sleep(2)
            return True
        time.sleep(1.5)
    return gemini_has_cred()


def gemini_has_cred():
    """Máy đã có phiên đăng nhập Antigravity chưa (Windows Credential Manager hoặc file token)."""
    if os.environ.get("FAKE_STATE"):
        return Path(os.environ["FAKE_STATE"], "agy").exists()
    if (Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists():
        return True
    if not IS_WIN:
        return True
    try:
        return "gemini:antigravity" in _run_hidden(["cmdkey", "/list"], 15).stdout.lower()
    except Exception:
        return False


def gemini_ping(exe, timeout):
    """Gemini: chạy 1 câu ngắn. Đã đăng nhập -> trả lời ngay; chưa -> agy tự mở trình duyệt đăng nhập."""
    d = DATA / ".login"
    d.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([exe, "-p", PING], cwd=str(d), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    LOGINS["gemini"] = p
    t0 = time.time()
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        return False, "timeout", time.time() - t0
    return "PONG" in (out or "").upper(), (out or "").strip()[-200:], time.time() - t0


def ai_login(ai, switch=False):
    """Đăng nhập trên trình duyệt, không mở cửa sổ dòng lệnh (trừ khi Gemini bắt buộc)."""
    exe = find_bin(AIS[ai]["bin"])
    if not exe:
        return {"error": "Chưa cài. Chạy lại file cài Maestro (.bat)."}
    cur = CFG["ais"].setdefault(ai, {})
    if cur.get("pending"):
        return {"ok": True}
    cur["pending"] = "login"
    d = DATA / ".login"
    d.mkdir(parents=True, exist_ok=True)

    def run():
        try:
            if ai in ("claude", "codex"):
                if switch:
                    try:
                        _run_hidden([exe, "auth", "logout"] if ai == "claude" else [exe, "logout"], 30)
                    except Exception:
                        pass
                elif ai_status(ai).get("ok"):
                    return
                args = [exe, "auth", "login", "--claudeai"] if ai == "claude" else [exe, "login"]
                old = LOGINS.pop(ai, None)
                if old:
                    kill_tree(old)
                if ai == "codex" and IS_WIN:          # phiên đăng nhập cũ còn giữ cổng 1455 -> codex login thoát ngay
                    _run_hidden(["powershell", "-NoProfile", "-Command",
                                 "Get-NetTCPConnection -LocalPort 1455 -State Listen -EA 0 | % { Stop-Process -Id $_.OwningProcess -Force -EA 0 }"], 20)
                if ai == "claude":                   # Claude tự mở trình duyệt, đăng nhập xong tự nhận (không cần dán mã)
                    env = dict(os.environ)
                    if env.get("BROWSER") == "none":
                        env.pop("BROWSER")
                    return claude_term_login(exe, d, cur, env)
                env = dict(os.environ)                  # ChatGPT: để Codex tự mở trình duyệt (đặt BROWSER làm hỏng đăng nhập)
                logf = d / f"{ai}.out"
                fh = open(logf, "w", encoding="utf-8", errors="replace")
                p = subprocess.Popen(args, cwd=str(d), stdin=subprocess.PIPE, stdout=fh, stderr=subprocess.STDOUT,
                                     env=env, creationflags=NO_WINDOW)
                LOGINS[ai] = p

                def grab():                         # lấy link đăng nhập để web mở trong cửa sổ nhỏ
                    for _ in range(40):
                        time.sleep(0.5)
                        try:
                            m = re.search(r"https://\S+", logf.read_text("utf-8", "replace"))
                        except Exception:
                            m = None
                        if m:
                            cur["url"] = m.group(0).rstrip(").,'\"")
                            return
                        if p.poll() is not None:
                            return
                threading.Thread(target=grab, daemon=True).start()
                t0 = time.time()
                while time.time() - t0 < 600:
                    time.sleep(2)
                    if p.poll() is not None or (time.time() - t0 > 6 and ai_status(ai).get("ok")):
                        break
                time.sleep(1)
                kill_tree(p)
                fh.close()
                ok = ai_status(ai).get("ok")
                if not ok:
                    dlog(f"{ai} login failed rc={p.poll()}; output:\n" + logf.read_text("utf-8", "replace")[-1500:])
                    lo = logf.read_text("utf-8", "replace").lower()
                    cur["msg"] = ("Thiếu Node.js — chạy lại Maestro_setup.bat" if "node" in lo and "not recognized" in lo
                                  else "Đăng nhập chưa xong. Bấm Đăng nhập lại; nếu vẫn lỗi, Export log gửi tôi.")
            else:
                # agy -p không tự hoàn tất đăng nhập được (không có chỗ dán mã) -> chỉ chạy ẩn khi máy đã có phiên lưu
                ok = (not switch) and gemini_has_cred() and gemini_ping(exe, 120)[0]
                if not ok:
                    if switch:
                        gemini_forget()
                    fail = ""
                    try:
                        done = gemini_term_login(exe, d, cur)
                    except Exception:                # không dựng được terminal ảo (thiếu thư viện)
                        import traceback
                        done = False
                        fail = "Không mở được đăng nhập Gemini — chạy lại file cài Maestro, rồi bấm Đăng nhập lại"
                        dlog("gemini hidden login failed:\n" + traceback.format_exc())
                    else:
                        lg0 = LOGINS.get("gemini")
                        dlog(f"gemini hidden login done={done}; screen tail:\n" + (lg0.buf[-1500:] if lg0 else ""))
                    lg = LOGINS.pop("gemini", None)
                    if lg:
                        lg.kill()
                    cur.pop("code", None)
                    cur.pop("url", None)
                    ok = bool(done) and gemini_has_cred() and gemini_ping(exe, 120)[0]
                cur.update(ok=ok, msg="" if ok else (fail or "Chưa đăng nhập được"), email="")
        except Exception as e:
            cur.update(ok=False, msg=str(e)[:150])
        finally:
            LOGINS.pop(ai, None)
            cur.pop("pending", None)
            cur.pop("code", None)
            cur.pop("url", None)
            cur.pop("auto", None)
            cur["at"] = now()
            save_cfg()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def claude_term_login(exe, d, cur, env):
    """Claude: chạy 'claude auth login' trong terminal ẩn. Claude tự mở trình duyệt với trang đăng nhập trả kết quả thẳng về
    máy (localhost) -> bấm Authorize là xong. Dự phòng (trình duyệt không mở): link trên màn hình + dán mã vào web."""
    p = HiddenTerm([exe, "auth", "login", "--claudeai"], str(d), env)
    LOGINS["claude"] = p
    t0 = time.time()
    while time.time() - t0 < 900 and p.alive():
        time.sleep(1)
        if not cur.get("url"):
            m = re.search(r"https://claude\.ai/oauth/authorize\S+|https://\S*oauth\S+", p.buf)
            if m:
                cur["url"] = m.group(0)
                cur["code"] = True
                cur["auto"] = "Opening browser" in p.buf
        if time.time() - t0 > 8 and int(time.time() - t0) % 4 == 0 and ai_status("claude").get("ok"):
            break
    time.sleep(1)
    dlog("claude login screen tail:\n" + p.buf[-1500:])
    p.kill()
    if not ai_status("claude").get("ok"):
        cur["msg"] = "Đăng nhập chưa xong. Bấm Đăng nhập lại; nếu vẫn lỗi, Export log gửi tôi."


def ai_cancel(ai):
    p = LOGINS.get(ai)
    if isinstance(p, HiddenTerm):
        p.kill()
    elif p:
        kill_tree(p)
    return {"ok": True}


def ai_code(ai, code):
    p = LOGINS.get(ai)
    code = re.sub(r"\s+", "", code or "")
    if not isinstance(p, HiddenTerm) or not p.alive() or not code:
        return {"error": "Phiên đăng nhập đã hết, bấm Đăng nhập lại"}
    p.send(code)
    time.sleep(0.3)
    p.send("\r")
    return {"ok": True}


def check_startup():
    """Mở app: chỉ kiểm tra AI nào chưa từng kiểm tra (lần đầu); còn lại dùng kết quả đã lưu."""
    threading.Thread(target=gemini_models, daemon=True).start()
    for ai in ("claude", "codex"):
        if "ok" not in CFG["ais"].get(ai, {}):
            ai_status(ai)
    save_cfg()


# ---------------------------------------------------------------- HTTP
def ui_html():
    if UI_B64:
        return base64.b64decode(UI_B64)
    return (APP.parent / "ui.html").read_bytes()


_prog = {}
_exp = {"t": 0, "v": {}}


def expected_secs(mode):
    """Thời gian 1 lượt làm thường mất (trung vị các lượt đã xong cùng chế độ) để ước % tiến độ."""
    if time.time() - _exp["t"] > 120:
        by = {}
        for x in TASKS.values():
            for m in x.get("messages", []):
                if m.get("role") == "work" and m.get("status") == "done" and m.get("ended_ts") and m.get("started_ts"):
                    d = m["ended_ts"] - m["started_ts"]
                    if 20 < d < 4 * 3600:
                        by.setdefault(x.get("mode", "balanced"), []).append(d)
        _exp.update(t=time.time(), v={k: sorted(v)[len(v) // 2] for k, v in by.items() if len(v) >= 2})
    return max(60, _exp["v"].get(mode) or {"turbo": 240, "balanced": 420, "pro": 600, "eco": 600, "code": 600}.get(mode, 420))


def pm_limit(t):
    """Hạn mức của AI đang làm quản lý cho task ({} = còn dùng được)."""
    return limits().get(t.get("pm") or pick_pm() or "claude") or {}


def task_detail(t):
    d = {k: t.get(k) for k in ("id", "title", "status", "created", "finished", "mode", "kind", "pdir", "installing")}
    d["can_restore"] = bool(t.get("checkpoints"))
    if t.get("need_login") and t["status"] == "queued":
        d["status"] = "login"
    if t["status"] == "queued" and (t.get("hold_until", 0) > time.time() or pm_limit(t)):
        d["status"] = "limit"
        d["hold_until"] = max(t.get("hold_until", 0), pm_limit(t).get("until", 0))
    msgs = [dict(m) for m in t.get("messages", [])]
    subs = subtasks(t)
    works = [m for m in msgs if m["role"] == "work"]
    for m in works:
        m["subtasks"] = list(m.pop("subs", None) or [])
    for s in [x for x in subs if not any(x["id"] == y["id"] for m in works for y in m["subtasks"])]:   # gắn sub-task vào lượt làm việc chứa thời điểm của nó
        for m in reversed(works):
            if s["ts"] >= m.get("started_ts", 0) - 1:
                m["subtasks"].append(s)
                break
    if t["status"] == "running" and works and works[-1].get("status") == "running":
        cur = works[-1]
        pl = rjson(wdir(t) / "tasks" / "plan.json", {}) or {}
        pl = pl if isinstance(pl, dict) else {}
        if pl and wdir(t).joinpath("tasks", "plan.json").stat().st_mtime < cur.get("started_ts", 0) - 5:
            pl = {}                                          # kế hoạch của lượt trước -> bỏ qua
        st = [str(x.get("status", "")).lower().strip() for x in (pl.get("subtasks") or []) if isinstance(x, dict)]
        tot = len(st)
        if tot:                                              # có kế hoạch: % = số bước xong / tổng bước
            est = sum(x in ("done", "completed", "failed", "skipped", "partial") for x in st) / tot
        else:                                                # chưa có kế hoạch: ước theo thời gian các lượt trước
            est = min(0.95, (time.time() - cur.get("started_ts", time.time())) / expected_secs(t.get("mode", "balanced")))
        key = (t["id"], cur.get("started_ts"))
        est = max(est, _prog.get(key, 0))                     # không lùi
        _prog[key] = est
        cur["progress"] = max(1, min(99, round(100 * est)))
        cur["nsteps"] = tot
        sd = wdir(t) / "stages"
        if sd.is_dir():
            cur["stages"] = [f.read_text("utf-8", "replace")[:6000] for f in sorted(sd.glob("*.md"),
                             key=lambda f: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", f.stem)])][:20]
    for m in works:
        if m.get("status") != "running":
            for s in m["subtasks"]:
                if s["status"] == "running":
                    s["status"] = "stopped"
    if t["status"] == "running" and works:
        works[-1]["now_ts"] = time.time()
    d["messages"] = msgs
    d["output"] = str(out_dir(t)) if CFG.get("output") else ""
    dec = rjson(wdir(t) / "tasks" / "decisions.json", None)
    if not isinstance(dec, list):
        dec = t.get("decisions") or []
    if t["status"] == "queued":
        nrun = sum(1 for x in TASKS.values() if x["status"] == "running")
        d["wait"] = f"Chờ {nrun} task đang chạy (máy này chạy tối đa {MAX_RUNNING} task cùng lúc)" if nrun >= MAX_RUNNING else ""
    d["project"] = t.get("project", "")
    d["tpl_saved"] = t.get("tpl_saved", "")
    d["brain_suggest"] = brain_suggest(t) if t.get("project") else []
    d["decisions"] = [{"group": str(g.get("group", ""))[:120],
                       "items": [{"type": i.get("type", "feedback"), "text": str(i.get("text", ""))[:400], "status": i.get("status", "")}
                                 for i in g.get("items", []) if isinstance(i, dict)]}
                      for g in dec if isinstance(g, dict)] if isinstance(dec, list) else []
    return d


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if not ctype.startswith("image/") else ""))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def ok_host(self):
        h = self.headers.get("Host", "").lower()
        names = (HOSTNAME, "127.0.0.1", "localhost")
        return h in names + tuple(f"{n}:{PORT}" for n in names) if PORT == 80 else h in tuple(f"{n}:{PORT}" for n in names)

    def authed(self):
        # chỉ nhận từ máy này (server nghe 127.0.0.1), đúng Host (chặn DNS rebinding),
        # và header riêng (trang web lạ không gửi được nếu không qua CORS)
        return self.ok_host() and self.headers.get("X-AIO") == "1"

    def do_GET(self):
        if not self.ok_host():
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/favicon.ico", "/icon.svg"):
            return self.send(200, ICON_SVG, "image/svg+xml")
        if u.path == "/ping":                       # bộ khởi động kiểm tra đúng tiến trình vừa chạy đã lên
            return self.send(200, {"pid": os.getpid(), "release": RELEASE})
        if u.path == "/":
            if q.get("t"):
                return self.send(302, b"", "text/plain", {"Location": "/"})
            return self.send(200, ui_html(), "text/html")
        if u.path == "/api/ofile":        # ảnh xem trước của file kết quả (thu nhỏ)
            f = out_file(q.get("t", [""])[0], q.get("i", [""])[0], q.get("f", [""])[0])
            if not f or f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                return self.send(404, {"error": "not found"})
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(f.suffix.lower()[1:], "image/" + f.suffix.lower()[1:])
            data = None if q.get("full", [""])[0] == "1" else thumb(f)
            return self.send(200, data or f.read_bytes(), "image/png" if data else mime,
                             {"X-Content-Type-Options": "nosniff", "Cache-Control": "max-age=60"})
        if u.path == "/api/input":        # ảnh người dùng đính kèm (thẻ <img> không gửi được header riêng)
            t = TASKS.get(q.get("t", [""])[0])
            name = Path(q.get("name", [""])[0]).name
            ext = name.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(ext)
            f = tdir(t) / "input" / name if t else None
            if not mime or not f or not f.is_file():
                return self.send(404, {"error": "not found"})
            return self.send(200, f.read_bytes(), mime, {"X-Content-Type-Options": "nosniff"})
        if not self.authed():
            return self.send(403, {"error": "forbidden"})
        if u.path == "/api/state":
            if SCHED["beat"] and time.time() - SCHED["beat"] > 90:     # bộ xếp lịch bị dừng -> chạy lại
                dlog("scheduler stalled, restarting")
                SCHED["beat"] = time.time()
                threading.Thread(target=scheduler, daemon=True).start()
            with _tlock:
                ts = [{**{k: t.get(k) for k in ("id", "title", "status", "created", "pinned")},
           **({"status": "limit"} if t["status"] == "queued" and (t.get("hold_until", 0) > time.time() or pm_limit(t)) else {})}
                      for t in TASKS.values() if t["status"] != "draft"]
                for x in ts:
                    if TASKS[x["id"]].get("need_login") and x["status"] == "queued":
                        x["status"] = "login"
            ts.sort(key=lambda x: x["created"], reverse=True)
            upd = CFG.pop("updated_to", "")
            if upd:
                save_cfg()
            fail = ""
            bj = DATA / "boot.json"
            if bj.exists():
                b = rjson(bj, {}) or {}
                upd = upd or b.pop("updated_to", "")
                fail = b.pop("update_fail", "")
                if upd or fail:
                    wjson(bj, b)
            return self.send(200, {"ais": ai_state(), "output": CFG["output"],
                                   "projects": [{"id": p["id"], "name": p["name"]} for p in projects()],
                                   "templates": [{"id": x["id"], "name": x["name"], "steps": len(x.get("steps", []))} for x in templates()], "instructions": CFG["instructions"],
                                   "tasks": ts, "version": VERSION, "release": RELEASE, "autostart": autostart_on(), "os": "mac" if IS_MAC else "win" if IS_WIN else "other", "updated": upd, "update_fail": fail,
                                   "machine": {"cpu": CPU, "ram": RAM_GB, "max": MAX_RUNNING}, "setup": setup_state(),
                                   "learn": learn_state()})
        if u.path == "/api/registry":
            return self.send(200, registry(force=q.get("force", [""])[0] == "1"))
        if u.path == "/api/task":
            t = TASKS.get(q.get("id", [""])[0])
            return self.send(200, task_detail(t) if t else {"error": "Không tìm thấy task"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authed():
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if u.path == "/api/upload":
            return self.upload(parse_qs(u.query), n)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            data = {}
        try:
            r = self.route(u.path, data)
        except Exception as e:
            r = {"error": str(e)}
        self.send(200, r)

    def upload(self, qs, n):
        name = Path(qs.get("name", [""])[0]).name
        t = TASKS.get(qs.get("t", [""])[0])
        if not t or not name:
            return self.send(400, {"error": "bad upload"})
        dst_dir = tdir(t) / "input"
        dst_dir.mkdir(parents=True, exist_ok=True)
        with open(dst_dir / name, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        return self.send(200, {"ok": True, "name": name})

    def route(self, path, d):
        if path == "/api/ai/login" and d.get("ai") in AIS:
            return ai_login(d["ai"], switch=bool(d.get("switch")))
        if path == "/api/ai/code" and d.get("ai") in AIS:
            return ai_code(d["ai"], str(d.get("code", "")))
        if path == "/api/ai/cancel" and d.get("ai") in AIS:
            return ai_cancel(d["ai"])
        if path == "/api/clear-cache":
            if busy_tasks():
                return {"error": "Đang có task chạy. Dọn cache sau khi task xong."}
            return clear_cache()
        if path == "/api/autostart":
            return set_autostart(bool(d.get("on")))
        if path == "/api/quit":
            threading.Thread(target=lambda: (time.sleep(0.4), quit_app()), daemon=True).start()
            return {"ok": True}
        if path == "/api/export-log":
            return export_log()
        if path == "/api/ai/refresh":
            return refresh_status()
        if path == "/api/memory":
            CFG["instructions"] = str(d.get("instructions") or "")[:20000]
            save_cfg()
            return {"ok": True, "msg": "Đã lưu ghi nhớ"}
        if path == "/api/gemini-key":
            return set_gemini_key(str(d.get("key") or ""))
        if path == "/api/update":
            return update_now()
        if path == "/api/pick":
            with _pick_lock:
                return {"path": pick_folder(str(d.get("title") or "Chọn thư mục nhận kết quả"))}
        if path == "/api/setup":
            o = (d.get("output") or "").strip()
            if not o or not Path(o).is_dir():
                return {"error": "Chọn thư mục nhận kết quả"}
            if o != CFG.get("output") and busy_tasks():
                return {"error": "Đang có task chạy. Đổi thư mục sau khi task xong hoặc tạm dừng."}
            CFG["output"] = o
            save_cfg()
            with _tlock:
                set_store()
                TASKS.clear()
                load_tasks()
            return {"ok": True}
        if path == "/api/open-output":
            if CFG.get("output"):
                open_path(CFG["output"])
            return {"ok": True}
        if path == "/api/task/new":
            return new_task(d.get("text", ""), d.get("mode", "balanced"), d.get("project", ""), d.get("template", ""),
                            d.get("kind", ""), d.get("pdir", ""))
        if path == "/api/ofile":
            return ofile_act(d)
        if path == "/api/project":
            return project_api(d)
        if path == "/api/template":
            return template_api(d)
        if path == "/api/task/submit":
            return submit_task(d.get("id", ""), d.get("files", []))
        if path == "/api/task/act":
            return act(d.get("id", ""), d.get("action", ""), d.get("text", ""), d.get("files"), d.get("template", ""))
        if path == "/api/task/file":
            return open_task_file(d.get("id", ""), d.get("path", ""))
        return {"error": "bad request"}


UPDATED = {"pending": None}
VERS = HOME / "versions"


_reg = {"t": 0.0, "data": None}


def registry(force=False):
    """Lịch sử task + danh mục file kết quả của mọi task (quét thư mục kết quả, nhớ đệm 30 giây)."""
    if not force and _reg["data"] and time.time() - _reg["t"] < 30:
        return _reg["data"]
    tasks, arts = [], []
    for t in sorted(TASKS.values(), key=lambda x: x["created"], reverse=True):
        if t["status"] == "draft":
            continue
        works = [m for m in t.get("messages", []) if m["role"] == "work"]
        dur = sum((m.get("ended_ts") or m.get("started_ts", 0)) - m.get("started_ts", 0) for m in works)
        n = 0
        od = out_dir(t) if CFG.get("output") and t.get("folder") else None
        if od and od.is_dir():
            for f in od.rglob("*"):
                if f.is_file() and ".work" not in f.parts:
                    n += 1
                    if len(arts) < 3000:
                        st = f.stat()
                        arts.append({"task": t["id"], "title": t["title"], "path": f.relative_to(od).as_posix(),
                                     "name": f.name, "ext": f.suffix.lower().lstrip("."), "size": st.st_size,
                                     "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
        tasks.append({"id": t["id"], "title": t["title"], "status": t["status"], "created": t["created"][:16],
                      "turns": len(works), "seconds": round(dur), "files": n, "folder": t.get("folder", "")})
    arts.sort(key=lambda x: x["mtime"], reverse=True)
    _reg.update(t=time.time(), data={"tasks": tasks, "artifacts": arts})
    try:      # bản cho Claude tra cứu file của các task trước
        wjson(STORE / "registry.json", {"output_root": CFG.get("output", ""), "tasks": [
            {"title": x["title"], "folder": x["folder"], "created": x["created"], "status": x["status"],
             "files": [a["path"] for a in arts if a["task"] == x["id"]][:200]} for x in tasks]})
    except Exception:
        pass
    return _reg["data"]


KEEP_EXT = {".md", ".json", ".txt", ".log", ".out", ".py", ".js", ".ps1", ".bat", ".csv", ".yaml", ".yml", ".html"}


def _fhash(f):
    import hashlib
    h = hashlib.sha1()
    with open(f, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def slim_task(t):
    """Giảm dung lượng thư mục làm việc của 1 task (giữ lịch sử: tin nhắn, kế hoạch, tiêu chí, quyết định, log chữ).
    - Luôn: xóa file trong work/ trùng hệt file đã có ở thư mục kết quả (bản thật nằm ở đó).
    Task còn dở: giữ bản nháp để làm tiếp (task xong được dọn hết bằng finish_clean)."""
    if t.get("status") in ("running", "queued"):
        return 0
    w = wdir(t) / "work"
    if not w.exists():
        return 0
    od = out_dir(t) if CFG.get("output") else None
    idx = {}
    if od and od.exists():
        for f in od.rglob("*"):
            if f.is_file() and STORE_NAME not in f.parts and ".work" not in f.parts:
                idx.setdefault(f.stat().st_size, []).append(f)
    freed = 0
    for f in list(w.rglob("*")):
        if not f.is_file() or f.name == "status.json":
            continue
        try:
            sz = f.stat().st_size
            if f.suffix.lower() in KEEP_EXT and sz < 2_000_000:
                continue
            if sz in idx and any(_fhash(o) == _fhash(f) for o in idx[sz]):
                f.unlink()
                freed += sz
        except Exception as e:
            dlog(f"slim {f}: {e}")
    for d in sorted((x for x in w.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()                                  # chỉ xóa được thư mục rỗng
        except OSError:
            pass
    return freed


def clear_cache():
    freed = 0
    for t in list(TASKS.values()):
        if t.get("status") in ("done", "cancelled"):     # task xong: chỉ giữ lịch sử tin nhắn
            w = wdir(t)
            before = sum(f.stat().st_size for f in w.rglob("*") if f.is_file()) if w.exists() else 0
            finish_clean(t)
            freed += before - (sum(f.stat().st_size for f in w.rglob("*") if f.is_file()) if w.exists() else 0)
        else:
            freed += slim_task(t)
    for p in [DATA / ".login", APP.parent / "__pycache__"]:
        if p.exists():
            freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            shutil.rmtree(p, ignore_errors=True)
    for p in [HOME / "app.py.old", DATA / "debug.log.1"]:
        if p.is_file():
            freed += p.stat().st_size
            p.unlink()
    lg = DATA / "debug.log"
    if lg.is_file() and lg.stat().st_size > 1_000_000:
        b = lg.read_bytes()
        freed += len(b) - 500_000
        lg.write_bytes(b[-500_000:])
    try:
        registry(force=True)
    except Exception:
        pass
    mb = freed / 1048576
    return {"ok": True, "msg": f"Đã dọn {mb:.0f} MB" if mb >= 1 else "Không có gì cần dọn"}


MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.maestro.app.plist"


def autostart_on():
    if IS_MAC:
        return MAC_AGENT.exists()
    if IS_WIN:
        return (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Maestro.lnk").exists()
    return False


def set_autostart(on):
    """Bật/tắt: tự chạy nền khi đăng nhập máy (task dở tự chạy tiếp)."""
    boot_py = HOME / "app.py"
    if IS_MAC:
        if on:
            MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
            MAC_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.maestro.app</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{boot_py}</string><string>--background</string></array>
<key>RunAtLoad</key><true/>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get("PATH", "")}</string></dict>
<key>StandardOutPath</key><string>{DATA / "launchd.log"}</string><key>StandardErrorPath</key><string>{DATA / "launchd.log"}</string>
</dict></plist>
""", encoding="utf-8")
        else:
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.maestro.app"], capture_output=True)
            MAC_AGENT.unlink(missing_ok=True)
        return {"ok": True}
    if IS_WIN:
        lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Maestro.lnk"
        if on:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{pyw if pyw.exists() else sys.executable}';"
                  f"$s.Arguments='\"{boot_py}\" --background';$s.WorkingDirectory='{HOME}';$s.IconLocation='{HOME / 'icon.ico'}';$s.Save()")
            _run_hidden(["powershell", "-NoProfile", "-Command", ps], 30)
        else:
            lnk.unlink(missing_ok=True)
        return {"ok": True}
    return {"error": "Chưa hỗ trợ trên hệ điều hành này"}


def quit_app():
    """Thoát Maestro: task đang chạy giữ trạng thái để lần mở sau tự chạy tiếp."""
    for t in [t for t in TASKS.values() if t["status"] == "running"]:
        t["_quit"] = True
        kill_tree(PROCS.get(t["id"]))
    time.sleep(1.5)
    os._exit(0)


def export_log():
    """Gom log của mọi task vào 1 file zip trong thư mục kết quả (không kèm file đầu vào/đầu ra)."""
    import zipfile
    if not CFG.get("output") or not Path(CFG["output"]).is_dir():
        return {"error": "Chưa chọn thư mục nhận kết quả"}
    name = "Maestro-log-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    dst = Path(CFG["output"]) / name
    fmt = lambda s: f"{int(s) // 60}m{int(s) % 60:02d}s"
    lines = [f"# AI Orchestrator log — v{RELEASE} ({VERSION}) — {now()}", "",
             "AI: " + ", ".join(f"{k}={'ok' if v.get('ok') else 'chưa'}" for k, v in CFG["ais"].items()), ""]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for t in sorted(TASKS.values(), key=lambda x: x["created"], reverse=True):
            d = wdir(t)
            lines += [f"## {t['title']}  [{t['id']}]  — {t['status']}", f"Tạo: {t['created']}  Thư mục: {t.get('folder', '')}", ""]
            det = task_detail(t)
            for m in det["messages"]:
                if m["role"] == "user":
                    lines.append(f"- {m.get('time', '')}  BẠN: {m['text'][:200].replace(chr(10), ' ')}")
                elif m["role"] == "work":
                    dur = (m.get("ended_ts") or time.time()) - m.get("started_ts", time.time())
                    lines.append(f"- {m.get('started', '')}  LƯỢT LÀM: {fmt(dur)} — {m.get('status')} — {len(m.get('steps', []))} bước")
                    for s in m.get("subtasks", []):
                        lines.append(f"    - sub-task {s['id']}: {s['worker'] or '?'} {s['model']}  lần {s['attempt']}  "
                                     f"{fmt(s['seconds']) if s.get('seconds') is not None else '?'}  {s['status']}  {s['error']}")
                else:
                    lines.append(f"- {m.get('time', '')}  CLAUDE ({m.get('kind') or 'trả lời'}): {m['text'][:200].replace(chr(10), ' ')}")
            lines.append("")
            for f in [tdir(t) / "task.json", d / "timeline.log", d / "run.log", d / "tasks" / "plan.json", d / "CLAUDE.md"]:
                if f.is_file():
                    z.write(f, f"{t['id']}/{f.name}")
            for f in list((d / "tasks").glob("*.md")) + [x for x in (d / "work").glob("*/v*/*")
                                                           if x.name in ("status.json", "worker.log", "TASK.md", "result.md")]:
                if f.is_file():
                    z.write(f, f"{t['id']}/{f.relative_to(d).as_posix()}")
        if (DATA / "debug.log").is_file():
            z.write(DATA / "debug.log", "debug.log")
        if (HOME / "install.log").is_file():
            z.write(HOME / "install.log", "install.log")
        z.writestr("summary.md", "\n".join(lines))
    open_path(CFG["output"])
    return {"ok": True, "msg": "Đã lưu " + name + " trong thư mục kết quả"}


def busy_tasks():
    """Có task đang làm/chờ -> chưa tự cập nhật."""
    return any(t["status"] in ("running", "queued") or t.get("_redirect") for t in TASKS.values())


def _http(url, timeout, accept=None):
    import gzip
    import urllib.request
    h = {"User-Agent": "maestro", "Accept-Encoding": "gzip"}
    if accept:
        h["Accept"] = accept
    r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)
    b = r.read()
    return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b


def _fetch_any(urls, timeout):
    """Tải cùng lúc từ nhiều nguồn (GitHub API, jsDelivr, raw); nguồn nào xong trước dùng nguồn đó."""
    import queue
    q = queue.Queue()
    for u, acc in urls:
        threading.Thread(target=lambda u=u, acc=acc: q.put(_try(lambda: _http(u, timeout, acc))), daemon=True).start()
    err = None
    for _ in urls:
        ok, v = q.get()
        if ok:
            return v
        err = v
    raise err or OSError("download failed")


def _try(f):
    try:
        return True, f()
    except Exception as e:
        return False, e


def fetch_update():
    """Tải bản mới từ GitHub (chỉ code, không có dữ liệu). Trả về số phiên bản mới, '' nếu đang mới nhất."""
    import hashlib
    if not UPDATE_BASE or VERSION == "dev":
        return ""
    if UPDATED["pending"] and (VERS / UPDATED["pending"] / "app.py").exists():
        return UPDATED.get("release") or UPDATED["pending"]          # đã tải sẵn ở nền
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/", UPDATE_BASE)
    repo, ref = m.group(1), m.group(2)
    ok, sha = _try(lambda: _http(f"https://api.github.com/repos/{repo}/commits/{ref}", 8,
                                 "application/vnd.github.sha").decode().strip())
    ref2 = sha if ok and re.fullmatch(r"[0-9a-f]{40}", sha) else ref      # theo đúng commit -> không dính bộ đệm

    def srcs(name):
        u = [(f"https://api.github.com/repos/{repo}/contents/{name}?ref={ref2}", "application/vnd.github.raw"),
             (f"https://raw.githubusercontent.com/{repo}/{ref2}/{name}", None)]
        if ref2 != ref:
            u.append((f"https://cdn.jsdelivr.net/gh/{repo}@{ref2}/{name}", None))
        return u
    meta = json.loads(_fetch_any(srcs("latest.json"), 10))
    if meta.get("version", "") > VERSION:
        if meta["version"] in ((rjson(DATA / "boot.json", {}) or {}).get("bad_versions") or []):
            return ""                                  # bản này đã lỗi khi khởi động trên máy này -> bỏ qua
        body = _fetch_any(srcs("app.py"), 40)
        if hashlib.sha256(body).hexdigest() == meta.get("sha256"):
            vd = VERS / meta["version"]
            vd.mkdir(parents=True, exist_ok=True)
            (vd / "app.py.part").write_bytes(body)
            os.replace(vd / "app.py.part", vd / "app.py")
            UPDATED["pending"] = meta["version"]
            m = re.search(rb'^RELEASE = "([^"]+)"', body, re.M)
            UPDATED["release"] = m.group(1).decode() if m else ""
            return UPDATED["release"] or meta["version"]
    return ""


def update_now():
    try:
        new = fetch_update()
    except Exception:
        return {"error": "Không kết nối được GitHub, thử lại sau"}
    if not new and not UPDATED["pending"]:
        return {"ok": True, "msg": f"Đang dùng bản mới nhất (v{RELEASE})"}
    threading.Thread(target=lambda: (time.sleep(0.5), apply_update()), daemon=True).start()   # task đang chạy: dừng, cập nhật xong tự chạy tiếp
    return {"ok": True, "restart": True, "msg": "Đang cập nhật, trang sẽ tự tải lại…"}


def check_update():
    if not UPDATE_BASE or VERSION == "dev":
        return
    while True:
        try:
            fetch_update()
        except Exception:
            pass
        for _ in range(15 * 60 // 10):            # kiểm tra lại mỗi 15 phút; áp dụng khi rảnh
            if UPDATED["pending"] and not busy_tasks():
                apply_update()
            time.sleep(10)


def apply_update():
    """Giao cho bộ khởi động (HOME/app.py): chạy thử bản mới, lên được mới dùng, không thì quay về bản cũ."""
    v = UPDATED["pending"]
    if not v or not (VERS / v / "app.py").exists():
        return
    (HOME / "next.txt").write_text(v, encoding="ascii")
    live = [t for t in TASKS.values() if t["status"] == "running"]
    for t in live:                       # giữ trạng thái "running" -> bản mới mở lên sẽ tự chạy tiếp (Claude nối lại phiên)
        t["_quit"] = True
        kill_tree(PROCS.get(t["id"]))
    for _ in range(20):
        if all(t.get("pid") is None for t in live):
            break
        time.sleep(0.25)
    boot = HOME / "app.py"
    env = {k: x for k, x in os.environ.items() if k not in ("AIO_CHILD",)}
    env["AIO_RESTART"] = "1"
    subprocess.Popen([sys.executable, str(boot), "--background"], cwd=str(HOME), env=env, creationflags=NO_WINDOW)
    os._exit(0)


def _healthy(timeout, proc=None):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            return False
        for p in PORTS:
            try:
                ok = b"<" in urllib.request.urlopen(f"http://127.0.0.1:{p}/", timeout=3).read()[:50]
                if proc is not None:
                    ok = ok and json.loads(urllib.request.urlopen(f"http://127.0.0.1:{p}/ping", timeout=2).read()).get("pid") == proc.pid
                if ok:
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def boot(argv):
    """Bộ khởi động: luôn là HOME/app.py (chỉ đổi khi cài lại bằng file .bat).
    Chạy bản trong versions/ (current.txt), thử bản mới (next.txt) trước; bản nào không lên trong 40 giây thì bỏ."""
    cfgf = DATA / "boot.json"            # file riêng: bản đang chạy không ghi đè lên
    def cfg_set(**kw):
        c = rjson(cfgf, {}) or {}
        for k, v in kw.items():
            if k == "bad":
                c["bad_versions"] = (c.get("bad_versions") or [])[-9:] + [v]
            else:
                c[k] = v
        wjson(cfgf, c)
    if os.environ.get("AIO_RESTART"):          # chờ bản cũ tắt hẳn
        for _ in range(20):
            if running_port() is None:
                break
            time.sleep(0.5)
    elif running_port():                        # đã chạy rồi -> để bản đang chạy mở trình duyệt
        return False
    nxt = (HOME / "next.txt").read_text("ascii").strip() if (HOME / "next.txt").exists() else ""
    cur = (HOME / "current.txt").read_text("ascii").strip() if (HOME / "current.txt").exists() else ""
    (HOME / "next.txt").unlink(missing_ok=True)
    for v in [x for x in dict.fromkeys([nxt, cur]) if x and x > VERSION]:   # bản cài lại bằng .bat mới hơn -> dùng nó
        f = VERS / v / "app.py"
        if not f.exists():
            continue
        env = dict(os.environ, AIO_CHILD="1", AIO_HOME=str(HOME), AIO_RESTART="1")
        p = subprocess.Popen([sys.executable, str(f)] + argv, cwd=str(HOME), env=env, creationflags=NO_WINDOW)
        if _healthy(90, p) and p.poll() is None:
            if v != cur:
                (HOME / "current.txt").write_text(v, encoding="ascii")
                cfg_set(updated_to=v)
                keep = {v, cur}
                for d in VERS.iterdir():                 # chỉ giữ bản đang dùng + bản trước đó
                    if d.is_dir() and d.name not in keep:
                        shutil.rmtree(d, ignore_errors=True)
            return True
        kill_tree(p)
        if v != cur:
            shutil.rmtree(VERS / v, ignore_errors=True)
        cfg_set(bad=v, update_fail=f"Bản mới ({v}) không khởi động được nên đã quay về bản trước.")
        if v == cur:
            (HOME / "current.txt").unlink(missing_ok=True)
    return None                                  # không có bản nào trong versions/ chạy được -> chạy chính file này


def running_port():
    """Cổng của bản đang chạy (nếu có)."""
    import urllib.request
    for p in PORTS:
        try:
            if b"<svg" in urllib.request.urlopen(f"http://127.0.0.1:{p}/icon.svg", timeout=1.5).read()[:200]:
                return p
        except Exception:
            pass
    return None


def serve(background=False):
    global CFG, PORT
    refresh_path()
    CFG = load_cfg()
    save_cfg()
    srv = None
    restart = bool(os.environ.pop("AIO_RESTART", None))
    if not restart:
        p = running_port()
        if p:                             # đã chạy rồi -> chỉ mở trình duyệt
            if not background:
                webbrowser.open(app_url(p))
            return
    order = ([CFG["port"]] if CFG.get("port") in PORTS else []) + PORTS
    for p in dict.fromkeys(order):
        for _ in range(20 if restart and p == order[0] else 1):   # vừa tự cập nhật: chờ bản cũ nhả cổng
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", p), H)
                break
            except OSError:
                time.sleep(0.5)
        if srv:
            PORT = p
            break
    if srv is None:
        return
    CFG["port"] = PORT
    save_cfg()
    url = app_url()
    try:
        set_store()
    except Exception as e:
        dlog(f"set_store: {e}")
    if ICO_B64:                                  # icon shortcut Start Menu đổi theo bản cập nhật
        try:
            ico = base64.b64decode(ICO_B64)
            if not (HOME / "icon.ico").exists() or (HOME / "icon.ico").read_bytes() != ico:
                (HOME / "icon.ico").write_bytes(ico)
        except Exception:
            pass
    if IS_WIN:                                   # đổi tên shortcut cũ sang Maestro (bản cập nhật qua nút Cập nhật)
        for base in (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                     Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"):
            try:
                if (base / "AI Orchestrator.lnk").exists() and not (base / "Maestro.lnk").exists():
                    os.replace(base / "AI Orchestrator.lnk", base / "Maestro.lnk")
            except Exception:
                pass
    load_tasks()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=check_startup, daemon=True).start()
    threading.Thread(target=ensure_libs, daemon=True).start()
    threading.Thread(target=learn_loop, daemon=True).start()
    threading.Thread(target=check_update, daemon=True).start()
    if not background and not os.environ.get("AIO_NO_BROWSER"):
        webbrowser.open(url)
    print("Maestro:", url, flush=True)
    srv.serve_forever()


# ---------------------------------------------------------------- worker (Claude gọi)
def run_limited(cmd, cwd, timeout):
    p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    try:
        so, se = p.communicate(timeout=timeout)
        return p.returncode, so, se, False
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            so, se = p.communicate(timeout=10)
        except Exception:
            so, se = "", ""
        return -1, so, se, True


GEMINI_API = os.environ.get("AIO_GEMINI_BASE") or "https://generativelanguage.googleapis.com"
API_SKIP = ("embedding", "tts", "image", "live", "audio", "aqa", "robotics", "computer-use", "native", "learnlm", "gemma")


def gemini_api_call(key, path, body=None, timeout=30):
    """Gọi Gemini API bằng key của người dùng (key gửi trong header, không nằm trong đường dẫn) -> (mã HTTP, JSON)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(GEMINI_API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json", "User-Agent": "maestro"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def api_err(j):
    return str(((j or {}).get("error") or {}).get("message") or "")[:300]


def gemini_api_models(key):
    """Danh sách model Gemini mà key dùng được (mới nhất trước). Key sai -> ValueError."""
    code, j = gemini_api_call(key, "/v1beta/models?pageSize=1000", timeout=20)
    if code != 200:
        raise ValueError(api_err(j) or f"HTTP {code}")
    names = []
    for m in j.get("models") or []:
        n = str(m.get("name", "")).split("/")[-1]
        if n.startswith("gemini") and "generateContent" in (m.get("supportedGenerationMethods") or []) and not any(x in n for x in API_SKIP):
            names.append(n)

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0
    return sorted(dict.fromkeys(names), key=lambda n: (-ver(n), "preview" in n or "exp" in n, n))


def api_order(models, hard):
    """Thứ tự thử model: thường Flash -> Flash-Lite -> Pro; việc khó Pro trước. Mỗi nhóm: bản mới nhất trước."""
    flash = [m for m in models if "flash" in m and "lite" not in m]
    lite = [m for m in models if "lite" in m]
    pro = [m for m in models if "pro" in m]
    rest = [m for m in models if m not in flash + lite + pro]
    return (pro + flash + lite if hard else flash + lite + pro) + rest


API_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
            ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain", ".csv": "text/csv", ".json": "application/json",
            ".html": "text/html"}


def api_worker(st, v, key, text, a):
    """Worker Gemini qua API key: chỉ trả lời bằng chữ (đọc được ảnh/PDF/file chữ trong input mà task nhắc tên)."""
    inp = Path(os.environ.get("AIO_INPUT") or Path.cwd() / "input")
    parts, total = [{"text": text + "\n\n---\nTrả lời đầy đủ bằng chữ (markdown). Bạn không tạo/sửa được file."}], 0
    if inp.is_dir():
        for f in sorted(inp.iterdir()):
            mime = API_MIME.get(f.suffix.lower())
            if f.is_file() and mime and f.name in text and total + f.stat().st_size < 15 * 2 ** 20:
                total += f.stat().st_size
                parts.append({"text": f"[File đính kèm: {f.name}]"})
                parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(f.read_bytes()).decode()}})
    models = (rjson(CFG_FILE, {}) or {}).get("gemini_api_models") or []
    t0, log, limited, err, m = time.time(), [], False, "", ""
    try:
        models = models or gemini_api_models(key)
        hard = a.hard or os.environ.get("AIO_HARD") == "1" or v.name != "v1"
        for m in api_order([x for x in models if x not in bad_models("gemini_api")], hard)[:5]:
            body = {"contents": [{"role": "user", "parts": parts}], "tools": [{"google_search": {}}]}
            code, j = gemini_api_call(key, f"/v1beta/models/{m}:generateContent", body, a.timeout)
            if code == 400 and "tool" in api_err(j).lower() or code == 400 and "search" in api_err(j).lower():
                body.pop("tools")                  # model không hỗ trợ tìm kiếm Google -> gọi không kèm
                code, j = gemini_api_call(key, f"/v1beta/models/{m}:generateContent", body, a.timeout)
            log.append(f"{m}: HTTP {code} {api_err(j)}")
            if code == 200:
                out = "".join(p_.get("text", "") for c in j.get("candidates") or [] for p_ in (c.get("content") or {}).get("parts") or [])
                if out.strip():
                    (v / "result.md").write_text(out, encoding="utf-8")
                    st.update(status="completed", model="api:" + m, via="api", seconds=round(time.time() - t0), error="", exit_code=0)
                    st.pop("reset", None)
                    break
                err = "Gemini API không trả nội dung (có thể bị bộ lọc an toàn chặn)"
            elif code == 429:
                limited, err = True, api_err(j) or "Gemini API hết lượt (429)"
            elif code in (401, 403) or code == 400 and "api key" in api_err(j).lower():
                err = "Gemini API key không dùng được: " + api_err(j)
                break
            elif code == 404:
                mark_bad_model("gemini_api", m)
            else:
                err = api_err(j) or f"HTTP {code}"
    except Exception as e:
        err = f"Không gọi được Gemini API: {e}"[:300]
    if st.get("via") != "api" or st.get("status") != "completed":
        if limited:
            lm = note_limit("gemini_api", err)
            st.update(status="limit", reset=lm["until"], via="api", model="api:" + (m or "?"),
                      error="Gemini API hết lượt, dùng lại lúc " + datetime.datetime.fromtimestamp(lm["until"]).strftime("%H:%M"))
        else:
            st.update(status="failed", via="api", model="api:" + (m or "?"), error=err or "Gemini API lỗi")
    with open(v / "worker.log", "a", encoding="utf-8") as fh:
        fh.write("--- gemini api ---\n" + "\n".join(log) + "\n")


# Codex: lệnh ghi của kết nối (Google Drive...) cần người duyệt; worker chạy nền không ai duyệt -> bị chặn.
# Chỉ bật tự duyệt khi task cần sửa file Google (cờ --drive / tin nhắn có link Google).
DRIVE_APPROVE = ["-c", 'apps._default.default_tools_approval_mode="approve"']
GOOGLE_LINK = re.compile(r"https?://(?:docs|drive|sheets)\.google\.com/", re.I)


def edit_refused(p):
    """Thư mục quá rộng/nguy hiểm để cho worker sửa thẳng -> lý do từ chối ('' = được phép)."""
    if not p.is_dir():
        return f"Không thấy thư mục cần sửa: {p}"
    sysd = [Path(os.environ.get(k, "")) for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData") if os.environ.get(k)]
    sysd += [Path(x) for x in ("/System", "/Library", "/Applications", "/usr", "/bin", "/etc", "/private")] if not IS_WIN else []
    sysd += [HOME, Path.home() / "AppData", Path.home() / "Library"]
    if too_wide(p):
        return f"Thư mục {p} quá rộng (cả ổ đĩa/thư mục người dùng): chọn đúng thư mục con chứa file cần sửa"
    for d in sysd:
        try:
            d = d.resolve()
        except OSError:
            continue
        if p == d or d in p.parents:
            return f"Không cho sửa thư mục hệ thống/ứng dụng: {p}"
    return ""


SKIP_PARTS = (".work", STORE_NAME, ".git", "node_modules", "__pycache__")


def _files_in(d):
    for f in d.rglob("*"):
        if f.is_file() and not any(x in SKIP_PARTS for x in f.relative_to(d).parts):
            yield f


def backup_dir(src, dst, limit=300 * 2 ** 20):
    """Chép lại thư mục trước khi worker sửa thẳng (để hoàn tác). Quá 300 MB / 3000 file thì bỏ qua."""
    try:
        fs, tot = [], 0
        for f in _files_in(src):
            fs.append(f)
            tot += f.stat().st_size
            if tot > limit or len(fs) > 3000:
                return "bỏ qua (thư mục quá lớn để sao lưu)"
        for f in fs:
            o = dst / f.relative_to(src)
            o.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, o)
        return str(dst)
    except Exception as e:
        return f"lỗi sao lưu: {e}"[:200]


def changed_files(d, backup, since):
    """File worker đã tạo/sửa trong thư mục được sửa (so với bản sao lưu hoặc theo giờ sửa)."""
    out, has_b = [], backup.is_dir()
    try:
        for f in _files_in(d):
            rel = f.relative_to(d)
            b = backup / rel
            if has_b:
                changed = not b.exists() or b.stat().st_size != f.stat().st_size or abs(b.stat().st_mtime - f.stat().st_mtime) > 1
            else:
                changed = f.stat().st_mtime >= since - 1
            if changed:
                out.append(rel.as_posix())
                if len(out) >= 200:
                    break
    except Exception:
        pass
    return sorted(out)


# ---------------------------------------------------------------- Code mode: điểm khôi phục + cài phần mềm thiếu
def _git(p, *a, timeout=60):
    r = subprocess.run(["git", "-C", str(p), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def code_checkpoint(t):
    """Trước mỗi lượt Code mode: lưu trạng thái thư mục dự án. Dự án git: 'git stash create' (không đổi file nào);
    không có git: chép file (bỏ .git, node_modules; tối đa 300 MB) vào lịch sử task. Giữ 5 điểm gần nhất."""
    p, cps = Path(t.get("pdir", "")), t.setdefault("checkpoints", [])
    if not p.is_dir():
        return
    n = (cps[-1]["n"] + 1) if cps else 1
    cp = {"n": n, "ts": time.time(), "time": now()}
    try:
        if shutil.which("git") and (p / ".git").exists() and _git(p, "rev-parse", "HEAD")[0] == 0:
            head = _git(p, "rev-parse", "HEAD")[1]
            sha = _git(p, "stash", "create")[1] or head
            _git(p, "update-ref", f"refs/maestro/{t['id']}-{n}", sha)       # giữ lại, không bị git dọn mất
            cp.update(git=sha, head=head)
        else:
            dst = tdir(t) / "checkpoints" / str(n)
            r = backup_dir(p, dst)
            cp.update(dir=r if r == str(dst) else "", note="" if r == str(dst) else r)
    except Exception as e:
        cp["note"] = f"lỗi: {e}"[:200]
    cps.append(cp)
    for old in cps[:-5]:
        if old.get("dir"):
            shutil.rmtree(old["dir"], ignore_errors=True)
        elif old.get("git"):
            _git(p, "update-ref", "-d", f"refs/maestro/{t['id']}-{old['n']}")
    t["checkpoints"] = cps[-5:]


def code_restore(t):
    """Đưa thư mục dự án về điểm khôi phục gần nhất (trước lượt làm cuối). File mới tạo sau đó vẫn giữ."""
    cps, p = t.get("checkpoints") or [], Path(t.get("pdir", ""))
    if not cps or not p.is_dir():
        return {"error": "Chưa có điểm khôi phục"}
    cp = cps[-1]
    if cp.get("git"):
        cur = _git(p, "stash", "create")[1]
        if cur:
            _git(p, "update-ref", f"refs/maestro/{t['id']}-undo", cur)     # bản trước khi khôi phục (phòng khi đổi ý)
        rc, _, err = _git(p, "reset", "--hard", cp["head"])
        if rc == 0 and cp["git"] != cp["head"]:
            rc, _, err = _git(p, "stash", "apply", cp["git"])
        if rc != 0:
            return {"error": "Khôi phục chưa xong: " + err[-200:]}
    elif cp.get("dir") and Path(cp["dir"]).is_dir():
        src = Path(cp["dir"])
        for f in src.rglob("*"):
            if f.is_file():
                o = p / f.relative_to(src)
                o.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, o)
    else:
        return {"error": "Điểm khôi phục không dùng được: " + (cp.get("note") or "thư mục quá lớn để sao lưu")}
    tmsg(t, "claude", f"Đã khôi phục thư mục dự án về lúc **{cp['time']}** (trước lượt làm cuối). File mới tạo sau đó vẫn giữ.")
    return {"ok": True, "msg": "Đã khôi phục"}


INSTALL_ID = {"winget": r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", "brew": r"[a-z0-9][a-z0-9@._+/-]{0,80}",
              "pip": r"[A-Za-z0-9][A-Za-z0-9._,=\[\]-]{0,80}", "npm": r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+(?:@[A-Za-z0-9._-]+)?"}


def read_install(t):
    """Yêu cầu cài phần mềm do Claude ghi (install_request.json) -> danh sách đã kiểm tra (chỉ tên gói hợp lệ, không lệnh tự do)."""
    d = rjson(wdir(t) / "install_request.json", None)
    if not isinstance(d, dict):
        return None
    items = []
    for it in (d.get("items") or [])[:10]:
        if not isinstance(it, dict):
            continue
        ok = {k: str(it[k]).strip() for k in INSTALL_ID if it.get(k) and re.fullmatch(INSTALL_ID[k], str(it[k]).strip())}
        if ok:
            items.append({"name": str(it.get("name") or next(iter(ok.values())))[:60], **ok})
    return {"why": str(d.get("why") or "")[:300], "items": items} if items else None


def install_script(items, log, done):
    """File cài đặt (.bat Windows / .command Mac) chạy lần lượt winget/brew, pip, npm cho các gói đã duyệt."""
    py = py_console()
    if IS_WIN:
        L = ["@echo off", "chcp 65001 >nul", "title Maestro - cai dat phan mem", "echo Maestro dang cai phan mem. Khong tat cua so nay.", ""]
        for it in items:
            if it.get("winget"):
                L += [f'echo === {it["winget"]}', f'winget install -e --id {it["winget"]} --silent --accept-source-agreements --accept-package-agreements',
                      f'echo [%ERRORLEVEL%] winget {it["winget"]}>> "{log}"']
        L.append('set "PATH=%PATH%;%ProgramFiles%\\nodejs;%APPDATA%\\npm"')
        for it in items:
            if it.get("pip"):
                L += [f'echo === {it["pip"]}', f'"{py}" -m pip install --user --disable-pip-version-check {it["pip"]}', f'echo [%ERRORLEVEL%] pip {it["pip"]}>> "{log}"']
            if it.get("npm"):
                L += [f'echo === {it["npm"]}', f'call npm install -g {it["npm"]}', f'echo [%ERRORLEVEL%] npm {it["npm"]}>> "{log}"']
        L += [f'echo done> "{done}"', "echo XONG. Cua so tu dong sau 5 giay.", "timeout /t 5 >nul"]
        return "\r\n".join(L) + "\r\n"
    L = ["#!/bin/bash", 'echo "Maestro đang cài phần mềm. Không tắt cửa sổ này."',
         'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"']
    for it in items:
        if it.get("brew"):
            L += [f'echo "=== {it["brew"]}"', f'if command -v brew >/dev/null; then brew install {it["brew"]}; echo "[$?] brew {it["brew"]}" >> "{log}"; '
                  f'else echo "[1] brew {it["brew"]}: chưa có Homebrew" >> "{log}"; fi']
        if it.get("pip"):
            L += [f'"{py}" -m pip install --user {it["pip"]} || "{py}" -m pip install --user --break-system-packages {it["pip"]}',
                  f'echo "[$?] pip {it["pip"]}" >> "{log}"']
        if it.get("npm"):
            L += [f'npm install -g {it["npm"]}', f'echo "[$?] npm {it["npm"]}" >> "{log}"']
    L += [f'echo done > "{done}"', 'echo "XONG. Có thể đóng cửa sổ này."', "sleep 3", "exit 0"]
    return "\n".join(L) + "\n"


def run_install(t):
    """Người dùng bấm Cài: chạy file cài trong cửa sổ hiện tiến trình, xong thì task tự chạy tiếp và báo kết quả."""
    req = t.get("install") or {}
    items = req.get("items") or []
    d = tdir(t) / "install"
    d.mkdir(parents=True, exist_ok=True)
    log, done = d / "install.log", d / "done.txt"
    log.write_text("", encoding="utf-8")
    done.unlink(missing_ok=True)
    f = d / ("cai_dat.bat" if IS_WIN else "cai_dat.command")
    f.write_text(install_script(items, log, done), encoding="utf-8")
    try:
        if IS_WIN:
            subprocess.run(["cmd.exe", "/c", "start", "/wait", "Maestro - cài đặt", "cmd.exe", "/c", str(f)], creationflags=NO_WINDOW, timeout=3600)
        else:
            os.chmod(f, 0o755)
            subprocess.Popen(["open", "-a", "Terminal", str(f)] if IS_MAC else ["bash", str(f)])
            t0 = time.time()
            while time.time() - t0 < 3600 and not done.exists():
                time.sleep(3)
    except Exception as e:
        dlog(f"run_install: {e}")
    refresh_path()                       # phần mềm vừa cài thấy được ngay
    res = log.read_text("utf-8", "replace").strip() if log.exists() else ""
    names = ", ".join(i["name"] for i in items)
    with _tlock:
        t.pop("installing", None)
        t.pop("install", None)
        tmsg(t, "user", f"Đã chạy cài đặt: {names}")
        t["status"] = "queued"
        t["next"] = {"kind": "answer", "text": f"[Maestro] Người dùng đã bấm Cài. Kết quả (mã [0] = thành công):\n{res or '(không có log)'}\n"
                                               "Kiểm tra lại phần mềm vừa cài rồi làm tiếp."}
        tsave(t)


CODE_MD = """## Vai trò (Code mode)
Bạn là kỹ sư phần mềm chính, làm như Claude Code: tự làm là chính, viết/sửa code TRỰC TIẾP trong THƯ MỤC DỰ ÁN: {pdir}
(Maestro tự tạo thư mục này cho task; dự án mới thì tạo code ở đây.) Người dùng nêu đường dẫn một dự án CÓ SẴN -> làm trong
thư mục đó; trước khi sửa, chép bản sao các file sẽ sửa vào work/backup/ (app chỉ tự lưu điểm khôi phục cho thư mục ở trên).
Dự án có sẵn: đọc README / CLAUDE.md / AGENTS.md / package.json / pyproject... trước để biết cách chạy, quy ước code.
Task này là một cuộc trò chuyện nhiều lượt.

## Quy trình code
1. Hiểu: tìm đúng file liên quan (Grep/Glob), đọc phần cần đọc; không đọc tràn lan.
2. Kế hoạch ngắn trong tasks/plan.json (việc nhỏ thì làm luôn).
3. Sửa: thay đổi nhỏ nhất đúng yêu cầu, giữ phong cách code sẵn có, không "cải thiện" phần không được nhờ.
4. CHẠY THỬ: chạy build/test/lint có sẵn của dự án; không có test thì chạy chương trình hoặc đoạn kiểm tra ngắn.
   Giao diện web: chạy server local, chụp màn hình bằng trình duyệt có sẵn
   (Edge/Chrome: --headless=new --screenshot=<file.png> --window-size=1280,800 <url>) rồi tự xem ảnh, sửa chỗ lệch.
5. Lỗi -> sửa -> chạy lại tới khi qua (tối đa 3 vòng cho một lỗi; vẫn lỗi thì báo rõ lỗi gì, đã thử gì).
6. Báo cáo bằng lời dễ hiểu: đã đổi gì (file + ý chính), đã chạy thử gì và kết quả, còn gì chưa xong. Không dán code dài.
- Git: KHÔNG commit/push/đổi nhánh trừ khi người dùng yêu cầu. App tự lưu điểm khôi phục trước mỗi lượt (nút Khôi phục).
- Không xoá file/thư mục của dự án ngoài yêu cầu. Không ghi mật khẩu/key vào code.
- Lệnh chạy lâu (server): chạy nền, nhớ tắt khi xong.

## GPT/Gemini hỗ trợ (chỉ khi có lợi rõ; việc chính bạn tự làm)
- codex: ý kiến thứ hai — soát lỗi/thiết kế (giao danh sách file + điều cần soát, dặn không sửa), hoặc viết 1 phần độc lập
  với --edit <thư mục con của dự án>.
- gemini: tra tài liệu, thư viện, thông báo lỗi lạ trên web.

## Thiếu phần mềm
Cần cài thêm (Node.js, Git, thư viện Python/npm...) mà máy chưa có: KHÔNG tự cài. Ghi ./install_request.json rồi DỪNG lượt:
  {{"why": "<1 câu: cần để làm gì>", "items": [{{"name": "Node.js", "winget": "OpenJS.NodeJS.LTS", "brew": "node"}},
   {{"name": "requests", "pip": "requests"}}, {{"name": "typescript", "npm": "typescript"}}]}}
(winget = gói Windows, brew = gói Mac, pip = thư viện Python, npm = gói Node; chỉ tên gói, không lệnh khác.)
App hiện thông báo + nút Cài cho người dùng; cài xong app tự chạy tiếp lượt sau kèm kết quả cài.
"""


def code_md(txt, t):
    """Hướng dẫn Code mode: thay mục Vai trò/Chia việc/Nghiệm thu/chế độ bằng quy trình code + chạy thử."""
    code = CODE_MD.format(pdir=t.get("pdir", ""))
    txt = re.sub(r"(?ms)^## Vai trò\n.*?(?=^## )", lambda m: code + "\n", txt, count=1)
    for sec in ("Chia việc (Gemini nhanh, GPT chính xác hơn)", "Nghiệm thu", "Ảnh có đánh dấu", "Storyboard", "Tạo ảnh",
                "Trả kết quả theo từng chặng"):
        txt = re.sub(r"(?ms)^## " + re.escape(sec) + r"\n.*?(?=^## )", "", txt, count=1)
    txt = re.sub(r"(?ms)^## Chế độ làm việc người dùng chọn.*?(?=^## )",
                 "## Chế độ: Code (Opus, suy nghĩ medium)\n- Ưu tiên chạy được và đúng; chạy thử thật trước khi báo xong.\n\n", txt, count=1)
    return txt


def cmd_worker(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="worker")
    ap.add_argument("worker", choices=["codex", "gemini"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--vdir")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--hard", action="store_true")
    ap.add_argument("--drive", action="store_true", help="ChatGPT được tự duyệt lệnh ghi của kết nối Google Drive (sửa thẳng Sheets/Docs/Slides)")
    ap.add_argument("--edit", help="thư mục worker được SỬA TRỰC TIẾP (file của người dùng, thư mục OUTPUT, file Drive đã tải về)")
    a = ap.parse_args(argv)
    proj = Path.cwd()
    edit = Path(a.edit).expanduser().resolve() if a.edit else None
    task = Path(a.task).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.vdir:
        v = Path(a.vdir)
    else:
        n = 1
        while (out / f"v{n}").exists():
            n += 1
        v = out / f"v{n}"
        v.mkdir()
    st = {"worker": a.worker, "model": a.model, "out_dir": str(v)}
    wjson(v / "status.json", {**st, "status": "running"})
    if not os.environ.get("AIO_WCHILD"):
        # chạy worker tách khỏi phiên Claude (qua 1 tiến trình trung gian tự thoát): app cập nhật/khởi động lại
        # hay phiên Claude bị ngắt thì worker vẫn làm tiếp; tiến trình này chỉ chờ kết quả rồi in ra.
        # GIỮ tiến trình trung gian: nếu worker là con trực tiếp, "taskkill /T" (Tạm dừng/tin gấp) sẽ giết luôn worker.
        flags = (0x00000008 | 0x00000200 | 0x08000000) if IS_WIN else 0   # DETACHED | NEW_GROUP | NO_WINDOW
        child = [sys.executable, str(APP), "worker", *argv, "--vdir", str(v)]
        spawn = [sys.executable, "-c", "import subprocess,sys,os;subprocess.Popen(sys.argv[2:],creationflags=int(sys.argv[1]),"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=os.name!='nt')",
                 str(flags), *child]
        subprocess.run(spawn, env=dict(os.environ, AIO_WCHILD="1"), creationflags=NO_WINDOW, cwd=str(proj))
        return wait_status(v, a.timeout + 180)
    exe = find_bin("codex" if a.worker == "codex" else "agy")
    bad_edit = edit_refused(edit) if edit else ""
    cfgd = rjson(CFG_FILE, {}) or {}
    key = cfgd.get("gemini_key", "") if a.worker == "gemini" else ""
    # Gemini: không có Antigravity / chưa đăng nhập / đang hết lượt -> dùng Gemini API key (nếu người dùng đã thêm)
    use_api = bool(key) and not edit and (not exe or ((cfgd.get("ais") or {}).get("gemini") or {}).get("ok") is False
                                          or bool(limits().get("gemini")))
    if key and edit and not exe:
        bad_edit = bad_edit or "Gemini qua API key không sửa file được: giao codex hoặc tự sửa"
    if edit and not bad_edit:
        st["edit_dir"] = str(edit)
        st["backup"] = backup_dir(edit, v / "_backup")
    if bad_edit:
        st.update(status="failed", error=bad_edit)
    elif not task.exists():
        st.update(status="failed", error=f"Không thấy {task}")
    elif not exe and not use_api:
        st.update(status="failed", error="Worker chưa cài")
    elif use_api:
        text = task.read_text(encoding="utf-8")
        (v / "TASK.md").write_text(text, encoding="utf-8")
        api_worker(st, v, key, text, a)
    else:
        text = task.read_text(encoding="utf-8")
        (v / "TASK.md").write_text(text, encoding="utf-8")
        inp_ = os.environ.get("AIO_INPUT") or str(proj / "input")
        edit_note = (f"Được SỬA TRỰC TIẾP file trong thư mục {edit} (giữ nguyên định dạng, công thức, bố cục; chỉ sửa đúng phần "
                     "yêu cầu; file mới cũng lưu trong thư mục này). KHÔNG sửa/xóa file ở nơi khác. "
                     "Excel/Word/PowerPoint: dùng Python (openpyxl, python-docx, python-pptx); không chuyển đổi định dạng file. "
                     "Câu trả lời cuối: liệt kê từng file đã sửa và đã sửa gì. ") if edit else ""
        if a.worker == "codex":
            drive_note = ("Được sửa TRỰC TIẾP file Google (Sheets/Docs/Slides) bằng kết nối Google Drive của bạn: chỉ sửa đúng file/link "
                          "trong yêu cầu, không xoá/di chuyển/chia sẻ file, không dùng kết nối khác (Gmail, Calendar...). Đọc lại sau khi sửa "
                          "để kiểm tra. Không có kết nối Google Drive thì nói rõ, không làm cách khác. ") if a.drive else ""
            prompt = (drive_note + f"Thực hiện đúng yêu cầu trong {v / 'TASK.md'}. "
                      f"File đầu vào (chỉ đọc): {inp_}. " + (edit_note or "Ghi mọi file kết quả vào thư mục hiện tại. ") +
                      "Câu trả lời cuối: nếu sản phẩm là văn bản thì ghi ĐẦY ĐỦ nội dung sản phẩm (không chỉ tóm tắt); "
                      "nếu tạo file thì liệt kê file đã tạo và cách đã kiểm tra.")
            cmd = [exe, "exec", "--skip-git-repo-check", *CODEX_SANDBOX, "-C", str(edit or v),
                   "--output-last-message", str(v / "result.md")]
            mods, eff = pick_model("codex", os.environ.get("AIO_MODE", "balanced"), os.environ.get("AIO_HARD") == "1")
            mods = [a.model] if a.model else list(mods)
            base_cmd = cmd + ["-c", f'model_reasoning_effort="{eff}"'] + (DRIVE_APPROVE if a.drive else [])
            cands = [base_cmd + ["-m", m_, prompt] for m_ in mods] + [base_cmd + [prompt]]   # model không dùng được -> thử model kế, cuối cùng mặc định
            cmd = cands[0]
            cwd = edit or v                 # Codex chỉ được ghi trong thư mục làm việc của nó (sandbox)
        else:
            md_ = os.environ.get("AIO_MODE", "balanced")
            retry_ = v.name != "v1"                     # giao lại (sửa lỗi / thử lại) -> nâng mức
            hard_ = os.environ.get("AIO_HARD") == "1" or (md_ == "balanced" and (a.hard or retry_))
            gm, _ = pick_model("gemini", md_, hard_)
            inp = inp_
            gtext = (f"{text}\n\n---\nFile đầu vào (chỉ đọc): {inp}. " + (edit_note or
                     f"Lưu mọi file kết quả (ảnh, tài liệu) vào thư mục hiện tại: {v}. ") +
                     "Câu trả lời cuối: nếu sản phẩm là văn bản thì ghi ĐẦY ĐỦ nội dung; nếu tạo file thì liệt kê tên file.")
            cmd = [exe, "-p", gtext, "--output-format", "json", "--add-dir", str(proj)]
            if os.path.isdir(inp):
                cmd += ["--add-dir", inp]
            if edit:
                cmd += ["--add-dir", str(edit)]
            write_ = a.write or bool(edit)  # cho Gemini tự ghi file không cần hỏi (chỉ khi task cần tạo/sửa file)
            if write_:
                cmd += ["--dangerously-skip-permissions"]
            plain = [exe, "-p", gtext, "--output-format", "json"] + (["--dangerously-skip-permissions"] if write_ else [])
            cands = ([cmd + ["--model", a.model or gm]] if (a.model or gm) else []) + [cmd, plain]   # cờ không hỗ trợ -> bản đơn giản
            cmd = cands[0]
            cwd = v
        t0 = time.time()
        st["t0"] = t0
        for i_, cmd in enumerate(cands):
            code, so, se, to = run_limited(cmd, cwd, a.timeout)
            low_ = (so + se).lower()
            bad_flag = code != 0 and ("flag provided but not defined" in low_ or "unknown flag" in low_ or "unknown option" in low_)
            bad_model = code != 0 and not bad_flag and "model" in low_ and any(k in low_ for k in (
                "not found", "not supported", "does not exist", "invalid", "unknown", "not available", "no access"))
            if bad_flag and i_ < len(cands) - 1:
                continue
            if bad_model:                       # nhớ model không dùng được để lần sau không thử lại (đỡ chậm)
                um = next((cmd[j + 1] for j in range(len(cmd) - 1) if cmd[j] in ("-m", "--model")), None)
                if um:
                    mark_bad_model(a.worker, um)
            if not bad_model or i_ == len(cands) - 1:
                break
        st["model"] = next((cmd[j + 1] for j in range(len(cmd) - 1) if cmd[j] in ("-m", "--model")), "mặc định")
        if any(k in (so + se).lower() for k in ("approaching", "less than half", "remaining", "% of your")) and "limit" in (so + se).lower():
            note_warn(a.worker, se + so)
        st.update(seconds=round(time.time() - t0), exit_code=code)
        (v / "worker.log").write_text(f"--- stdout ---\n{so}\n--- stderr ---\n{se}\n", encoding="utf-8")
        if a.worker == "gemini" and so.strip():
            try:
                resp = json.loads(so).get("response") or so
            except Exception:
                resp = so
            (v / "result.md").write_text(resp, encoding="utf-8")
        res = v / "result.md"
        if to:
            st.update(status="timeout", error=f"Quá {a.timeout}s")
        elif code != 0 and is_limit(se[-1500:] + "\n" + so[-1500:]):
            lm = note_limit(a.worker, se + "\n" + so)
            st.update(status="limit", reset=lm["until"], error="Worker hết hạn mức, dùng lại lúc " +
                      datetime.datetime.fromtimestamp(lm["until"]).strftime("%H:%M") + ("" if lm["known"] else " (ước tính)")
                      + ": " + (se or so).strip()[-300:])
        elif code != 0:
            st.update(status="failed", error=(se or so).strip()[-500:])
        elif not res.exists() or not res.read_text(encoding="utf-8").strip():
            st.update(status="failed", error="Worker không trả kết quả")
        else:
            st["status"] = "completed"
    if key and not use_api and not edit and "exit_code" in st and st.get("status") in ("limit", "failed"):   # Antigravity hết lượt/lỗi -> thử API
        st["agy_error"] = st.pop("error", "")[:300]
        api_worker(st, v, key, (v / "TASK.md").read_text(encoding="utf-8") if (v / "TASK.md").exists() else "", a)
    st["files"] = sorted(str(f.relative_to(v)).replace("\\", "/") for f in v.rglob("*")
                         if f.is_file() and f.name not in ("TASK.md", "worker.log", "status.json", "result.md")
                         and "_backup" not in f.relative_to(v).parts)
    if edit and st.get("edit_dir"):
        st["edited"] = changed_files(edit, v / "_backup", st.get("t0", 0))
    st.pop("t0", None)
    wjson(v / "status.json", st)
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st["status"] == "completed" else 1)


# Chỉ cụm từ mà Claude/Codex/Gemini thật sự in ra khi hết hạn mức (chữ lẻ như "quota", "429" dễ trùng nội dung bài làm)
LIMIT_RE = re.compile(r"usage limit|limit reached|reached your (?:usage |rate )?limit|hit your (?:usage )?limit|rate limit(?:ed| exceeded)?"
                      r"|quota (?:exceeded|exhausted)|exceeded (?:your |the )?(?:current )?quota|resource_exhausted|resource exhausted"
                      r"|too many requests|\b429\b[^\n]{0,40}(?:limit|quota|too many)|limit will reset|out of (?:usage|credits)")


def is_limit(text):
    """Thông báo lỗi có phải do hết hạn mức không. Chỉ xét ~2000 ký tự cuối (phần lỗi), không xét nội dung bài làm."""
    return bool(LIMIT_RE.search((text or "")[-2000:].lower()))


def parse_reset(text, now_ts=None):
    """Đọc giờ được dùng lại từ thông báo hết hạn mức (Claude/Codex/Gemini). Không đọc được -> None."""
    now_ts = now_ts or time.time()
    s = (text or "").lower()
    m = re.search(r"\|(\d{10})\b", s) or re.search(r"reset[^0-9]{0,20}(\d{10})\b", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:in|after)\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,and]*)+)", s)
    if m:
        sec = 0
        for n, u in re.findall(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", m.group(1)):
            sec += int(n) * (86400 if u.startswith("d") else 3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
        if sec:
            return int(now_ts + sec)
    m = re.search(r"(?:resets?|reset at|try again at|available at|retry at)\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            d = datetime.datetime.fromtimestamp(now_ts).replace(hour=h, minute=mi, second=0, microsecond=0)
            if d.timestamp() <= now_ts:
                d += datetime.timedelta(days=1)
            return int(d.timestamp())
    return None


def note_limit(ai, text):
    """Ghi hạn mức của 1 AI (worker chạy ở tiến trình riêng -> dùng file chung)."""
    f = DATA / "limits.json"
    d = rjson(f, {}) or {}
    until = parse_reset(text)
    d[ai] = {"until": until or int(time.time() + 1800), "known": bool(until), "msg": (text or "").strip()[-300:], "at": int(time.time())}
    try:
        wjson(f, d)
    except Exception:
        pass
    return d[ai]


def limits():
    d = rjson(DATA / "limits.json", {}) or {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("until", 0) > time.time()}


# ---------------------------------------------------------------- model theo chế độ (người dùng chốt; app tự hạ khi sắp/đã hết hạn mức)
EFFORTS = ["low", "medium", "high", "xhigh"]
# ChatGPT: thử lần lượt (gói Plus có GPT-6.2; gói Free chỉ có dòng Luna) -> cuối cùng là model mặc định của tài khoản
CODEX_MODELS = ["gpt-6.2-sol", "gpt-6-sol", "gpt-6-luna"]
# Gemini: Neon cho Pro (bước quá khó: high), Hydrogen cho các chế độ khác; chỉ dùng model mà `agy models` liệt kê
MODEL_PLAN = {   # (model, mức suy nghĩ) theo chế độ
    "claude": {"pro": ("opus", "high"), "balanced": ("opus", "medium"), "turbo": ("opus", "medium"), "eco": ("opus", "low"),
               "code": ("opus", "medium")},       # Code mode: Opus mới nhất (bí danh "opus" tự theo bản mới), mức medium
    "codex": {"pro": (CODEX_MODELS, "high"), "balanced": (CODEX_MODELS, "medium"), "turbo": (CODEX_MODELS, "low"),
              "eco": (CODEX_MODELS, "low"), "code": (CODEX_MODELS, "medium")},
    "gemini": {"pro": ("neon", "medium"), "balanced": ("hydrogen", "medium"), "turbo": ("hydrogen", "medium"),
               "eco": ("hydrogen", "low"), "code": ("hydrogen", "medium")},
}
GEM_FALLBACK = {"neon": ["hydrogen", "flash"], "hydrogen": ["flash", "neon"]}


def pressure(ai):
    """Sắp hết hạn mức (cảnh báo) hoặc vừa hết trong 6 giờ qua -> hạ 1 bậc để còn dùng được lâu hơn."""
    d = (rjson(DATA / "limits.json", {}) or {}).get(ai) or {}
    return bool(d) and ((d.get("warn") and time.time() - d.get("warn_at", 0) < 6 * 3600)
                        or time.time() - d.get("until", 0) < 6 * 3600)


BAD_MODEL_TTL = 24 * 3600      # model báo lỗi chỉ bị bỏ qua 24 giờ (lỗi có thể chỉ là tạm thời)


def bad_models(ai):
    v = (rjson(DATA / "models_bad.json", {}) or {}).get(ai) or {}
    if not isinstance(v, dict):                     # bản cũ lưu danh sách vĩnh viễn -> bỏ
        return set()
    return {m for m, ts in v.items() if time.time() - float(ts or 0) < BAD_MODEL_TTL}


def mark_bad_model(ai, m):
    f = DATA / "models_bad.json"
    d = rjson(f, {}) or {}
    cur = d.get(ai) if isinstance(d.get(ai), dict) else {}
    cur = {k: ts for k, ts in cur.items() if time.time() - float(ts or 0) < BAD_MODEL_TTL}
    cur[m] = int(time.time())
    d[ai] = cur
    try:
        wjson(f, d)
    except Exception:
        pass


def gem_pick(key, eff):
    """Chọn đúng tên model Gemini có trong `agy models` (theo dòng + mức suy nghĩ); không có thì lùi dòng khác."""
    ls = [x for x in ((rjson(DATA / "config.json", {}) or {}).get("gemini_list") or []) if x not in bad_models("gemini")]
    for k in [key] + GEM_FALLBACK.get(key, []):
        hit = [x for x in ls if k in x.lower()]
        if hit:
            same = [x for x in hit if eff and eff in x.lower()]
            return (same or hit)[0]
    return None                                     # không có danh sách -> để agy tự chọn model mặc định


def pick_model(ai, mode, hard=False):
    mode = norm_mode(mode)
    m, eff = MODEL_PLAN[ai][mode]
    if hard:
        eff = "high" if ai == "gemini" else "xhigh"
    elif pressure(ai) and eff != "low":
        eff = EFFORTS[max(0, EFFORTS.index(eff) - 1)]
    if ai == "gemini":
        return gem_pick(m, eff), eff
    if ai == "codex":
        m = [x for x in m if x not in bad_models("codex")]
    return m, eff


def note_warn(ai, text):
    f = DATA / "limits.json"
    d = rjson(f, {}) or {}
    cur = d.get(ai) or {"until": 0}
    cur.update(warn=True, warn_at=int(time.time()), msg=(text or "")[-200:])
    d[ai] = cur
    try:
        wjson(f, d)
    except Exception:
        pass


def gemini_models():
    """Lấy tên model Gemini thật của tài khoản (agy models) để chọn Neon/Hydrogen."""
    exe = find_bin("agy")
    if not exe:
        return
    try:
        out = _run_hidden([exe, "models"], 40).stdout or ""
    except Exception:
        return
    found = []
    for line in out.splitlines():
        l2 = line.strip().strip("-*•> ").strip()
        if not l2 or not re.search(r"[a-z]", l2, re.I) or l2.lower().startswith(("available", "usage", "model", "name ")):
            continue
        q = re.findall(r'"([^"]+)"', l2)
        ids = re.findall(r"\b[a-z0-9]+(?:[.\-][a-z0-9]+)+\b", l2.lower())
        found.append(q[0] if q else (ids[0] if ids and any(c.isalpha() for c in ids[0]) else re.split(r"\s{2,}|\t", l2)[0]))
    if found:
        CFG["gemini_list"] = found
        save_cfg()
    dlog(f"agy models -> {found}; raw: {out[:400]}")


def wait_status(v, limit=1200):
    """Chờ worker (đang chạy tách rời) xong, in status và trả mã thoát như khi chạy trực tiếp."""
    v = Path(v)
    t0 = time.time()
    st = {}
    while time.time() - t0 < limit:
        st = rjson(v / "status.json", {}) or {}
        if st.get("status") not in (None, "running"):
            break
        time.sleep(2)
    else:
        st = {**st, "status": "timeout", "error": "Chờ worker quá lâu"}
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st.get("status") == "completed" else 1)


def cmd_wait(argv):
    """wait <thư mục vN hoặc thư mục --out>: chờ worker đang chạy (vd sau khi phiên bị ngắt) thay vì chạy lại."""
    p = Path(argv[0]).resolve()
    if not (p / "status.json").exists():
        vs = sorted((x for x in p.glob("v*") if x.is_dir() and x.name[1:].isdigit()), key=lambda x: int(x.name[1:]))
        p = vs[-1] if vs else p
    wait_status(p, int(argv[1]) if len(argv) > 1 else 1200)


# ---------------------------------------------------------------- check (Claude gọi)
def cmd_check(argv):
    import argparse
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": str(detail)})

    ap = argparse.ArgumentParser(prog="check")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("files"); p.add_argument("--dir", required=True); p.add_argument("--glob", default="*"); p.add_argument("--count", type=int)
    p = sp.add_parser("png"); p.add_argument("--dir", required=True); p.add_argument("--count", type=int); p.add_argument("--alpha", action="store_true"); p.add_argument("--size")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "files":
            fs = sorted(f for f in Path(a.dir).glob(a.glob) if f.is_file())
            add(f"số file {a.glob}", a.count is None or len(fs) == a.count, f"{len(fs)} (cần {a.count})")
            for f in fs:
                add(f"{f.name} không rỗng", f.stat().st_size > 0, f"{f.stat().st_size} bytes")
        elif a.cmd == "png":
            from PIL import Image
            fs = sorted(Path(a.dir).glob("*.png"))
            add("số file PNG", a.count is None or len(fs) == a.count, f"{len(fs)} (cần {a.count})")
            for f in fs:
                try:
                    im = Image.open(f)
                    im.load()
                    add(f"{f.name} PNG hợp lệ", im.format == "PNG", f"{im.size[0]}x{im.size[1]} {im.mode}")
                    if a.size:
                        add(f"{f.name} kích thước", f"{im.size[0]}x{im.size[1]}" == a.size.lower(), f"{im.size[0]}x{im.size[1]}")
                    if a.alpha:
                        if im.mode not in ("RGBA", "LA"):
                            add(f"{f.name} nền trong suốt", False, f"mode {im.mode}, không có alpha")
                        else:
                            lo, hi = im.getchannel("A").getextrema()
                            add(f"{f.name} nền trong suốt", lo == 0 and hi > 0, f"alpha {lo}..{hi}")
                except Exception as e:
                    add(f"{f.name} đọc được", False, e)
    except ImportError as e:
        add("thư viện", False, f"{e} — chạy lại file cài đặt")
    except Exception as e:
        add("lỗi", False, e)
    ok = bool(checks) and all(c["pass"] for c in checks)
    print(json.dumps({"pass": ok, "checks": checks}, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- judge (Claude gọi): giám khảo độc lập cho ảnh
JUDGE_PROMPT = """Bạn là GIÁM KHẢO độc lập nghiệm thu sản phẩm. Bạn KHÔNG tham gia làm và không biết quá trình làm.
Nhiệm vụ: kiểm tra khắt khe từng file theo TIÊU CHÍ dưới đây. Người làm thường tự chấm dễ dãi; việc của bạn là tìm lỗi.
Khi phân vân giữa đạt và chưa đạt: chấm CHƯA ĐẠT. Không khen xã giao.

Cách làm:
1. Mở và kiểm tra THẬT từng file cần chấm{ref_note}: ảnh/văn bản/code dùng Read; bảng tính, dữ liệu, số liệu thì chạy lệnh
   để đọc và tính lại (chỉ ĐỌC — tuyệt đối không sửa, xóa, tạo file sản phẩm).
2. Với mỗi file, chấm từng tiêu chí 1-5 (5 = đạt hoàn toàn) và ghi lỗi cụ thể, kiểm chứng được (ở đâu, sai gì).
3. File "fail" nếu vi phạm bất kỳ tiêu chí BẮT BUỘC nào, hoặc có tiêu chí nào <= 2.

TIÊU CHÍ:
{criteria}

YÊU CẦU RIÊNG CỦA LẦN NÀY:
{brief}

FILE CẦN CHẤM:
{images}
{refs}
Chỉ trả về MỘT khối JSON, không thêm chữ nào khác:
{{"overall": "pass" | "fail", "images": [{{"file": "<tên file>", "verdict": "pass" | "fail",
"scores": {{"<tiêu chí>": 1-5}}, "issues": ["<lỗi cụ thể>"], "fix": "<chỉ dẫn sửa ngắn>"}}], "summary": "<1-2 câu>"}}"""


def cmd_judge(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="judge", description="Giám khảo độc lập (phiên Claude mới) nghiệm thu file theo tiêu chí")
    ap.add_argument("--files", "--images", dest="images", required=True, nargs="+",
                    help="thư mục hoặc file sản phẩm cần chấm (tối đa 12 file/lần)")
    ap.add_argument("--criteria", required=True, help="file .md tiêu chí nghiệm thu của task")
    ap.add_argument("--refs", help="thư mục mẫu đã được người dùng duyệt (tùy chọn)")
    ap.add_argument("--brief", default="(không)", help="yêu cầu riêng của lần này: pose, số áo, kích thước...")
    ap.add_argument("--out", required=True, help="vd work/J1")
    ap.add_argument("--model")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    imgs = []
    for p in a.images:
        p = Path(p).resolve()
        imgs += sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
    imgs = [f for f in imgs if f.is_file()][:12]
    refs = sorted(f for f in Path(a.refs).resolve().iterdir() if f.is_file())[:5] if a.refs and Path(a.refs).is_dir() else []
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out / f"v{n}").exists():
        n += 1
    v = out / f"v{n}"
    v.mkdir()
    (v / "TASK.md").write_text(f"Chấm độc lập {len(imgs)} file\n\n{a.brief}", encoding="utf-8")
    st = {"worker": "judge", "model": a.model, "out_dir": str(v)}
    wjson(v / "status.json", {**st, "status": "running"})
    exe = find_bin("claude")
    crit = Path(a.criteria)
    if not exe or not crit.is_file() or not imgs:
        st.update(status="failed", error="Chưa cài Claude" if not exe else f"Không thấy tiêu chí {crit}" if not crit.is_file() else "Không có file để chấm")
    else:
        prompt = JUDGE_PROMPT.format(
            criteria=crit.read_text(encoding="utf-8")[:12000], brief=a.brief,
            images="\n".join(f"- {f}" for f in imgs),
            ref_note=" và các MẪU ĐÃ DUYỆT (chuẩn để so sánh)" if refs else "",
            refs=("\nMẪU ĐÃ DUYỆT (chỉ để so sánh, không chấm):\n" + "\n".join(f"- {f}" for f in refs) + "\n") if refs else "")
        cmd = [exe, "-p", prompt, "--output-format", "json", "--allowedTools", "Read,Glob,Grep,Bash"]
        for d in dict.fromkeys([str(f.parent) for f in imgs + refs]):
            cmd += ["--add-dir", d]
        if a.model:
            cmd += ["--model", a.model]
        t0 = time.time()
        code, so, se, to = run_limited(cmd, v, a.timeout)
        st.update(seconds=round(time.time() - t0), exit_code=code)
        (v / "worker.log").write_text(f"--- stdout ---\n{so}\n--- stderr ---\n{se}\n", encoding="utf-8")
        verdict = None
        try:
            text = json.loads(so).get("result", "")
            verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception:
            pass
        if to:
            st.update(status="timeout", error=f"Quá {a.timeout}s")
        elif not isinstance(verdict, dict) or "images" not in verdict:
            st.update(status="failed", error="Giám khảo không trả kết quả đọc được: " + (se or so).strip()[-300:])
        else:
            bad = [i for i in verdict["images"] if i.get("verdict") != "pass"]
            verdict["overall"] = "fail" if bad or verdict.get("overall") != "pass" else "pass"
            wjson(v / "review.json", verdict)
            st.update(status="completed" if verdict["overall"] == "pass" else "rejected",
                      error="" if not bad else f"{len(bad)}/{len(verdict['images'])} file chưa đạt", review=verdict)
    wjson(v / "status.json", {k: x for k, x in st.items() if k != "review"})
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st["status"] == "completed" else 1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "wait":
        cmd_wait(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        cmd_worker(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "check":
        cmd_check(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "judge":
        cmd_judge(sys.argv[2:])
    else:
        if not os.environ.get("AIO_CHILD") and APP.parent == HOME and VERSION != "dev":
            r = boot(sys.argv[1:])
            if r is not None:
                if r is False and "--background" not in sys.argv:
                    webbrowser.open(app_url(running_port()))
                sys.exit(0)
            os.environ["AIO_RESTART"] = "1"
        serve(background="--background" in sys.argv)
