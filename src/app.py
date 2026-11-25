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
        tl = w / "timeline.log"                 # nhật ký giờ từng bước: giữ lại trong lịch sử để đo chỗ chậm (Export log)
        if w != tdir(t) and tl.is_file():
            with open(tdir(t) / "timeline.log", "a", encoding="utf-8") as fo:
                fo.write(tl.read_text("utf-8", "replace"))
        if w != tdir(t):
            shutil.rmtree(w, ignore_errors=True)
        else:                        # task cũ: chỉ giữ task.json, file đính kèm, CLAUDE.md
            for x in ("work", "tasks"):
                shutil.rmtree(w / x, ignore_errors=True)
            for x in ("run.log", "question.md"):
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


def tl_mark(t, text):
    """Mốc giờ chỉ ghi vào nhật ký (không hiện ở giao diện): bước vừa có kết quả / hết lượt — để đo thời gian từng phần."""
    try:
        with open(wdir(t) / "timeline.log", "a", encoding="utf-8") as f:
            f.write(f"{now()}\t{text}\n")
    except Exception:
        pass


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
    "pro": """- Ưu tiên chất lượng và thời gian. Song song tối đa {n} worker cho phần độc lập.
- Giám khảo (khi Giám sát bật) được dùng model mạnh.""",
    "balanced": """- Cân bằng chất lượng, thời gian, hạn mức. Song song tối đa {n} worker cho phần độc lập; tuần tự khi phụ thuộc.
- Model nhỏ/nhanh cho việc đơn giản, mạnh cho việc khó.""",
    "turbo": """- TỐC ĐỘ TỐI ĐA: bắt tay làm ngay, kế hoạch vài dòng, không hỏi lại trừ khi thiếu dữ kiện bắt buộc. Song song tối đa {n} worker.
- Không mẫu thử trước lô; Giám sát tắt thì không vòng sửa: chỉ check kỹ thuật, sai thì sửa đúng chỗ; tự xem sản phẩm tối đa 1 lần.
- Không làm thêm ngoài yêu cầu (không preview/ghép so sánh). Trả kết quả ngay khi đủ dùng; ghi rõ phần chưa kiểm tra kỹ.""",
    "eco": """- TIẾT KIỆM HẠN MỨC: tuần tự, 1 worker một lúc; việc tự làm được bằng lệnh/script thì không gọi AI.
- Giám sát tắt: tối đa 1 vòng sửa. Không làm thêm ngoài yêu cầu.""",
}


# ---------------------------------------------------------------- hướng dẫn theo nhu cầu (mô-đun)
IMG_RE = re.compile(r"ảnh|hình|image|png|jpe?g|webp|keyframe|storyboard|asset|banner|icon|thumbnail|poster|sprite|"
                    r"art\b|artwork|vẽ|render|minh hoạ|minh họa|background|\bbg\b|logo|gif", re.I)
EDIT_RE = re.compile(r"sửa|chỉnh|edit|cập nhật|thay|đổi tên|xóa|xoá|clear|\.xlsx|\.docx|\.pptx|\.csv|[A-Za-z]:\\|\\\\|/Users/", re.I)


def judge_on(t):
    """Nút Giám sát của task; chưa bấm thì theo chế độ (Pro, Balanced bật)."""
    v = t.get("judge")
    return bool(v) if v is not None else norm_mode(t.get("mode")) in ("pro", "balanced", "code")


def md_needs(t):
    """Task cần mô-đun hướng dẫn nào (theo mọi tin nhắn + file đính kèm của task)."""
    text = " ".join([t.get("text", "")] + [m.get("text", "") for m in t.get("messages", []) if m.get("role") == "user"])
    inp = tdir(t) / "input"
    names = [f.name.lower() for f in inp.iterdir()] if inp.is_dir() else []
    need = set()
    if IMG_RE.search(text) or any(n.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) for n in names):
        need.add("image")
    if "_danhdau" in text or "đánh dấu" in text.lower() or any("_danhdau" in n for n in names):
        need.add("markup")
    if GOOGLE_LINK.search(text) or re.search(r"google (drive|sheet|doc|slide)", text, re.I):
        need.add("drive")
    if EDIT_RE.search(text) or "drive" in need:
        need.add("edit")
    return need


def md_image(py, app, pm):
    return f"""## Ảnh & storyboard
- Storyboard: mặc định viết BẢNG CHỮ (cảnh, thời lượng, hình ảnh, chữ trên màn hình, âm thanh, CTA). Chỉ vẽ khung hình khi người
  dùng yêu cầu rõ (keyframe/vẽ/render storyboard).
- Tạo ảnh: {"Gemini (--write) trước, lỗi thì codex ($imagegen)" if pm != "codex" else "gemini (--write), lỗi thì tự tạo ($imagegen)"}.
  Không tạo được (lỗi, hết hạn mức): viết prompt vào OUTPUT/prompts/, hỏi người dùng (question.md) dán vào ChatGPT, lưu ảnh
  vào OUTPUT/images/ rồi trả lời "xong"; sau đó check + chấm như trên.
- Ảnh PNG: kiểm tra size/nền trong suốt bằng lệnh check png trước khi xem bằng mắt.

"""


def md_markup(py, app, pm):
    return """## Ảnh có đánh dấu
- Ảnh "_danhdau.png" người dùng gửi: tin nhắn có dòng [Đánh dấu trên ...] với toạ độ pixel thật của ảnh gốc. Đỏ = lỗi/cần xoá;
  cam = cần xem/sửa/thêm; số 1,2,3 khớp ghi chú "1) ... 2) ...". Sửa ĐÚNG vùng được đánh dấu.
- BẠN đánh dấu ảnh (báo lỗi QA, trước/sau): Pillow, đọc kích thước trước; khung bo góc (radius ~14, nét ~5px với ảnh ~1400px,
  lề 18px); đường dẫn 25–35px cùng độ dày; nhãn 1–3 chữ cùng màu, đặt sát, KHÔNG viền trắng; đỏ #E63946 chỉ cho lỗi/xoá, cam
  #FF9F1C cho phần còn lại; 2–6 chỗ/ảnh (nhiều hơn: đánh số + chú thích); không resize; xem lại ảnh trước khi giao; liệt kê
  từng chỗ (số, màu, toạ độ, ý nghĩa).

"""


def md_drive(py, app, pm):
    if pm == "codex":
        return """## Google Drive / Sheets / Docs / Slides
- Sửa TRỰC TIẾP file Google (giữ link): tự dùng kết nối Google Drive của bạn nếu có.
- Không có: file chia sẻ "bất kỳ ai có link" thì tải bản xuất (Sheets .../export?format=xlsx, Docs .../export?format=docx,
  Slides .../export/pptx), sửa, lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace). File riêng tư: hỏi người
  dùng tải về gửi kèm. Không đổi quyền chia sẻ file.

"""
    return """## Google Drive / Sheets / Docs / Slides
- Công cụ Google Drive của BẠN (mcp__claude_ai_Google_Drive, nạp bằng ToolSearch): tìm, đọc, tải, tạo file — KHÔNG sửa được ô.
  Chỉ đọc/tóm tắt: tự đọc.
- SỬA TRỰC TIẾP file Google (giữ link): giao codex kèm --drive (ChatGPT dùng kết nối Google Drive của nó). Ghi rõ link, tab/vùng
  cần sửa, phần phải giữ (tiêu đề, định dạng, checkbox...); sửa xong giao codex đọc lại để kiểm tra.
- Codex không có kết nối / bị chặn: tải file về work/drive/<tên>/ (.xlsx/.docx/.pptx), giao --edit work/drive/<tên>, kiểm tra,
  cập nhật lại trên Drive nếu công cụ cho phép; không thì lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace).
  Không đổi quyền chia sẻ file.

"""


def md_edit(py, app, pm):
    return """## Sửa file (--edit)
- --edit <thư mục>: worker SỬA TRỰC TIẾP file trong thư mục đó (file người dùng yêu cầu sửa, OUTPUT, file Drive đã tải về). Dùng
  khi sửa nhiều/lặp lại (nhiều file, bảng tính lớn) để khỏi tự chép/sửa. Chọn thư mục NHỎ NHẤT chứa file cần sửa (cả ổ/thư mục
  người dùng sẽ bị từ chối). App tự sao lưu vào work/<ID>/vN/_backup; status.json có "edited" = file đã đổi. Mở lại file đã sửa
  để kiểm tra.

"""


MD_MODULES = {"image": md_image, "markup": md_markup, "drive": md_drive, "edit": md_edit}
MD_TITLES = {"image": "tạo ảnh, storyboard", "markup": "ảnh có đánh dấu (đọc/vẽ khung lỗi)",
             "drive": "Google Drive/Sheets/Docs/Slides (đọc, sửa trực tiếp)", "edit": "worker sửa file trực tiếp (--edit)"}


def write_claude_md(t):
    py, app = py_console(), str(APP)
    mode = norm_mode(t.get("mode"))
    mode_name = MODES[mode]
    mode_rules = MODE_RULES[mode].format(n="1 (máy cấu hình thấp)" if LOW_SPEC else str(MAX_WORKERS))
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
    watch_on = judge_on(t)                    # nút Giám sát: giám khảo chấm, sửa tới khi đạt
    judge = watch_on and pm == "claude"
    need = md_needs(t)
    mods = {k: f(py, app, pm) for k, f in MD_MODULES.items()}
    inline = "".join(mods[k] for k in mods if k in need)
    extra = [k for k in mods if k not in need]
    rd = wdir(t) / "rules"
    rd.mkdir(parents=True, exist_ok=True)
    for k in mods:                              # mô-đun không cần lúc này: để sẵn file, cần thì Claude tự đọc
        (rd / f"{k}.md").write_text(mods[k], encoding="utf-8")
    extra_md = ("## Hướng dẫn thêm — CHỈ đọc khi task cần tới\n" + "\n".join(
        f"- {MD_TITLES[k]}: ./rules/{k}.md" for k in extra) + "\n\n") if extra else ""
    txt = f"""# Task {t['id']}

## Hướng dẫn của người dùng (ưu tiên áp dụng)
{CFG.get('instructions', '').strip() or '(không)'}

## Vai trò
Bạn là PM: lên kế hoạch, giao việc, kiểm tra, tổng hợp; tự viết/biên tập văn bản (ý tưởng, kịch bản, báo cáo, câu chữ).
Phần quan trọng hoặc cần tư duy (phân tích, quyết định hướng làm, chốt nội dung chính, đánh giá chất lượng): TỰ xử lý để
đảm bảo kết quả. Việc làm thuần thao tác/khối lượng — vẽ/xử lý ảnh, xử lý file/bảng tính hàng loạt, code/script, research web
dài — ưu tiên giao GPT/Gemini. Nghiệm thu trên file thật, sửa đúng phần lỗi. Task là cuộc trò chuyện nhiều lượt.

## Đường dẫn
- Thư mục hiện tại: nháp/brief/log, bị XÓA khi task xong -> không để sản phẩm ở đây. File người dùng gửi: {tdir(t) / "input"}
- OUTPUT (sản phẩm giao người dùng): {out_dir(t)} — người dùng chỉ định nơi khác thì lưu đúng nơi đó, ghi đường dẫn đầy đủ.
- Được ĐỌC mọi ổ: {', '.join(drives())}. Chỉ sửa file có sẵn khi người dùng yêu cầu sửa chính file đó; không xóa/di chuyển
  file ngoài thư mục hiện tại và OUTPUT. Sản phẩm task trước (dùng lại, tránh làm lại): {STORE / "registry.json"}

## Worker
{chr(10).join('- ' + desc[w] for w in on) or '- (không có — tự làm)'}
Worker lỗi/không chạy: coi như không có. Giao việc: viết yêu cầu đủ ngữ cảnh vào
tasks/<ID>.md (dòng đầu = tên ngắn; worker không thấy cuộc trò chuyện), rồi chạy:
  "{py}" "{app}" worker codex  --task tasks/S1.md --out work/S1 [--edit <thư mục>] [--drive] [--timeout 900]
  "{py}" "{app}" worker gemini --task tasks/S2.md --out work/S2 [--write] [--edit <thư mục>] [--hard]
  --write: Gemini được tạo file/ảnh. --hard: việc khó (Balanced: Gemini lên mức high; giao lại vì lỗi cũng tự lên high).
  --edit / --drive: xem mục sửa file / Google Drive.
NHIỀU VIỆC: giao hết cả lô liền một lượt bằng --bg (trả về ngay, worker chạy nền), rồi chờ MỘT lần cho cả lô:
  "{py}" "{app}" wait work/S1 work/S2 work/S3      (in kết quả từng việc; không chờ/kiểm tra từng cái một)
Worker lỗi / hết lượt / không vẽ ra ảnh: app TỰ giao bên kia làm lại ngay trong cùng lệnh (Gemini→GPT mọi việc; GPT→Gemini việc
vẽ; status via=gpt-fallback/gemini-fallback) — KHÔNG tự kiểm tra rồi giao lại. Bên nào không vẽ được: 30 phút tới app giao thẳng
bên kia. Kết quả: work/<ID>/vN/ (result.md, status.json, file worker tạo; bên làm thay ở vN/gpt/ hoặc vN/gemini/).
Sửa lỗi: giao lại cùng --out. App tự chọn model — không truyền --model.

## Chia việc (Gemini nhanh, GPT chính xác hơn)
- Research web, đối thủ, xu hướng: Gemini. Code, script, số liệu, Excel/Word/PowerPoint: GPT.
- Pro: GPT làm trước mọi việc còn lại. Balanced/Turbo/Eco: Gemini trước (idea, kịch bản, brief, ảnh), lỗi mới giao lại
  ĐÚNG PHẦN lỗi cho GPT; Balanced: idea/câu chữ chia Gemini phần cần nhanh/rộng, GPT phần cần sắc (idea chính, hook).
- Việc phụ thuộc: làm trước; việc độc lập: chạy song song (mỗi worker --out riêng; không 2 worker --edit cùng thư mục).

## Tốc độ (quan trọng)
- VIỆC ĐƠN GIẢN (hỏi đáp, 1 thao tác, sửa nhỏ): làm NGAY — không criteria/plan/decisions, không giám khảo, không bước "kiểm tra" riêng.
- Văn bản do worker viết: dặn ghi TOÀN BỘ nội dung vào câu trả lời cuối. Không chép/viết lại phần đã đạt; viết nội dung cuối 1 lần.
- Gộp nhiều lệnh đọc/kiểm tra vào một lần chạy.

## Nghiệm thu
- Worker "completed" chưa phải là xong: kiểm tra file thật. Đo được: "{py}" "{app}" check files|png ... (xem --help).
{"- Giám sát bật + task có sản phẩm: trước khi làm, viết tasks/criteria.md ngắn, kiểm chứng được (đánh dấu BẮT BUỘC); đổi yêu cầu -> cập nhật." + chr(10) if watch_on else "- Giám sát tắt: không viết criteria.md; tự đối chiếu sản phẩm với đúng yêu cầu gốc." + chr(10)}- Chấm chất lượng: {("GIÁM SÁT BẬT — giám khảo độc lập chấm CẢ LÔ ở cuối (sau check kỹ thuật); fail -> sửa đúng lỗi nêu, chấm lại "
  "file đã sửa, lặp tới khi ĐẠT (tối đa 4 vòng; vẫn fail thì báo rõ còn lỗi gì):") if judge else
  ("GIÁM SÁT BẬT — không có giám khảo Claude: giao gemini soát theo criteria.md, sửa và soát lại tới khi đạt (tối đa 4 vòng).")
  if watch_on else "Giám sát TẮT — không gọi giám khảo; tự xem lại theo yêu cầu 1 lần, lỗi nhỏ thẩm mỹ thì ghi chú cho người dùng."}
{f"""    "{py}" "{app}" judge --files <thư mục/file> --criteria tasks/criteria.md [--refs "<mẫu đã duyệt>"] --brief "<yêu cầu>" --out work/J1 [--model haiku|sonnet]
  Văn bản/task nhẹ: --model haiku. Giám khảo model nhỏ trừ Pro. Code: chạy test trước, giám khảo soát phần đã sửa.
""" if judge else ""}  Lô lớn (từ 5 sản phẩm cùng loại): làm 1 mẫu, tự xem rồi mới chạy cả lô. Lỗi: sửa tối đa 2 lần/sub-task, quá thì báo partial.

{inline}{extra_md}{brain_md}{tpl_md}## Chế độ: {mode_name}
{mode_rules}
- Worker báo "limit": chuyển worker khác hoặc tự làm; không đảm bảo chất lượng thì hỏi người dùng.{" Bước QUÁ khó (đã thử không ra): ghi xhigh vào tasks/effort.txt -> lượt sau chạy mức cao nhất." if mode == 'pro' else ""}

## Ghi lại (để giao diện hiển thị)
- Tiến độ: app tự tính từ các bước và worker — KHÔNG cần viết plan.json.
- Người dùng góp ý hoặc chốt điều gì (chỉ khi đó): cập nhật tasks/decisions.json, gom theo phần việc, sửa/gộp mục cũ:
  [{{"group": "<phần việc>", "items": [{{"type": "feedback"|"decision", "text": "<1 câu>", "status": "open"|"done"}}]}}]
  Có file này thì đọc đầu mỗi lượt, theo đúng các quyết định đã chốt, không hỏi lại.
- Task nhiều phần: xong phần nào hữu ích thì ghi ngay vào ./stages/<số>.md (giao diện hiện cho người dùng đọc trong lúc làm).

## Khi cần người dùng quyết định
Chỉ hỏi khi thật sự quan trọng (không hoàn tác được, tốn nhiều hạn mức, mâu thuẫn, thiếu dữ liệu): ghi câu hỏi ngắn + phương án
vào ./question.md rồi DỪNG. Còn lại tự quyết, ghi giả định.

## Kết thúc mỗi lượt
Báo cáo, research, concept, so sánh: viết thẳng trong câu trả lời, KHÔNG tạo file .md/.txt. Chỉ tạo file khi sản phẩm vốn là file
hoặc người dùng yêu cầu; lưu vào OUTPUT. Câu trả lời cuối ngắn: đạt/chưa đạt, điểm cần biết, việc người dùng cần làm.
Không liệt kê lại từng file (giao diện có nút mở thư mục). KHÔNG kể quá trình/worker (ai làm, hết hạn mức, chuyển worker,
số vòng) trừ khi ảnh hưởng tới kết quả người dùng nhận. Tập trung vào sản phẩm của task. Dùng được **đậm**, `code`, gạch đầu dòng.

## Cấm
Không dùng API key. Không ghi mật khẩu/token vào file.
"""
    wdir(t).mkdir(parents=True, exist_ok=True)
    if IS_WIN and wdir(t).name == ".work":
        subprocess.run(["attrib", "+h", str(wdir(t))], creationflags=NO_WINDOW, capture_output=True)
    rules = learn_rules()
    learn_md = (("## Bài học chung của team (đã kiểm chứng, hợp với hầu hết loại task — áp dụng)\n"
                 + "\n".join("- " + r for r in rules) + "\n\n") if rules else "")
    txt = txt.replace("\n## Cấm\n", "\n" + learn_md + "\n## Cấm\n", 1)
    if "codex" not in on:
        txt = "\n".join(x for x in txt.split("\n") if '" worker codex ' not in x)
    if t.get("kind") == "code":
        txt = code_md(txt, t, py, app)
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


FREE_RULES = """## Chế độ Free (người dùng không có tài khoản AI trả phí — áp dụng THAY mọi luật chế độ ở trên khi mâu thuẫn)
- Hạn mức rất ít: làm tuần tự, 1 worker tại một thời điểm; không làm mẫu thử; Giám sát tắt thì tối đa 1 vòng sửa.
- Ưu tiên giao Gemini (Antigravity hoặc API key) cho research, ý tưởng, viết; tự làm phần code/file bằng lệnh/script.
- Worker báo "limit": chuyển ngay sang worker/cách khác còn dùng được, không chờ; hết cả thì trả phần đã xong + nói rõ phần còn thiếu.
- Câu trả lời ngắn, đủ ý; không làm thêm ngoài yêu cầu.
"""


def _section(txt, title, body):
    """Thay nội dung 1 mục '## <title>' (tới mục kế tiếp) trong hướng dẫn."""
    return re.sub(r"(?ms)^## " + re.escape(title) + r"\n.*?(?=^## )", lambda m: f"## {title}\n{body.strip()}\n\n", txt, count=1)


def pm_codex_md(txt):
    """Hướng dẫn khi ChatGPT (Codex) làm quản lý thay Claude: không có giám khảo Claude, không có công cụ Drive của Claude."""
    txt = txt.replace("## Vai trò\n", "## Vai trò\nBạn là ChatGPT (Codex) làm quản lý thay Claude; không có sub-agent — việc code/file nhỏ tự làm bằng lệnh.\n", 1)
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
    wait = (f'\nWorker vẫn chạy tiếp khi bạn bị ngắt: work/<ID>/vN/status.json còn "running" thì CHỜ bằng lệnh '
            f'"{py_console()}" "{APP}" wait work/<ID> (không giao lại việc đó).')
    if kind == "urgent":
        return (f"Người dùng vừa gửi tin nhắn trong lúc bạn đang làm (bạn đã bị ngắt):\n\n{text}\n{fl}\n"
                f"Xem lại phần đã làm (work/*/status.json, file đã tạo), điều chỉnh theo tin nhắn này rồi tiếp tục. Làm theo {md_name(t)}.{wait}")
    if kind == "followup":
        return f"Tin nhắn mới của người dùng:\n\n{text}\n{fl}\nLàm theo {md_name(t)}."
    return f"Task bị dừng giữa chừng. Xem lại phần đã làm (work/*/status.json, file đã tạo) và làm tiếp phần còn dở. Làm theo {md_name(t)}.{wait}"


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
    if t.get("u0") is None:                     # đo mức dùng 1 lần khi task bắt đầu (không đo từng lượt cho đỡ tốn)
        t["u0"] = {}
        threading.Thread(target=usage_task_start, args=(t,), daemon=True).start()
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
        sid = "nhiều việc" if "$" in sid or "%" in sid else sid
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
    elif typ == "item.completed" and it.get("type") == "command_execution":
        tl_mark(t, "(Claude suy nghĩ)")
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
        elif typ == "user":                 # kết quả công cụ đã về -> từ đây là Claude suy nghĩ/viết
            tl_mark(t, "(Claude suy nghĩ)")
        elif typ == "result":
            result = ev
    err = p.stderr.read()
    tl_mark(t, "(xong lượt)")
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
    if t.get("status") == "done" and "u0" in t:
        try:
            usage_task_done(t)              # task hoàn thành: đo lại -> mức task tốn (≈, task khác chạy cùng lúc cũng tính vào)
            tsave(t)
        except Exception as e:
            dlog(f"usage_task_done: {e}")


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
            pm_ = t.get("pm") or pick_pm() or "claude"
            if t.get("hold_until", 0) > time.time() or lm.get(pm_):
                continue          # AI quản lý hết hạn mức: chờ tới giờ reset (mọi task cùng quản lý cùng chờ)
            ub = usage_block(pm_)
            if ub:                # gói đã dùng gần hết + "chỉ dùng trong gói": không chạy để không tràn sang credit
                note_limit(pm_, f"in-plan limit, resets at |{ub}")
                continue
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
        elif action == "judge":
            t["judge"] = text == "on"                  # Giám sát: áp dụng từ lượt chạy kế tiếp
            if wdir(t).exists():
                write_claude_md(t)
        elif action == "mode" and MODE_ALIAS.get(text, text) in MODES:
            t["judge"] = norm_mode(text) in ("pro", "balanced", "code")   # đổi chế độ: Pro/Balanced/Code tự bật Giám sát
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


def learn_extract(t):
    """Rút bài học chung của 1 task xong bằng Claude Haiku (rẻ, nhanh, chạy ngầm) — Claude quản lý không phải tự ghi."""
    exe = find_bin("claude")
    if not exe or not claude_usable():
        return {}
    asks = [m.get("text", "")[:1200] for m in t.get("messages", []) if m.get("role") == "user"][:3]
    ans = next((m.get("text", "") for m in reversed(t.get("messages", [])) if m.get("role") == "claude"), "")[:2000]
    subs = "; ".join(f"{s_['worker']} {s_['status']} {s_.get('seconds') or '?'}s lần {s_['attempt']}" for s_ in subtasks(t))[:800]
    prompt = (f"Task của một công cụ AI (Claude quản lý, giao việc cho ChatGPT/Gemini). Chế độ: {t.get('mode')}.\n"
              f"Yêu cầu: {' | '.join(asks)}\nKết quả cuối: {ans}\nWorker: {subs or '(không)'}\n\n"
              f"Trả về DUY NHẤT 1 JSON: {{\"category\": \"<1 trong: {', '.join(CATEGORIES)}>\", \"lessons\": [\"<tối đa 3 câu>\"]}}. "
              "Bài học = điều giúp MỌI task sau nhanh/đúng hơn (chia việc, worker hợp việc gì, lỗi hay gặp, bước thừa). "
              "Không có gì thật sự mới -> []. KHÔNG ghi tên dự án/game/người, link, đường dẫn, nội dung, số liệu riêng.")
    try:
        p = subprocess.run([exe, "-p", prompt, "--model", "haiku"], cwd=str(tdir(t)), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=NO_WINDOW)
        out = p.stdout or ""
        d = json.loads(out[out.index("{"):out.rindex("}") + 1])
        return d if isinstance(d, dict) else {}
    except Exception as e:
        dlog(f"learn_extract {t.get('id')}: {e}")
        return {}


def learn_report(t):
    """Báo cáo ẩn danh của 1 task: loại việc, bài học (đã lọc), AI nào làm/bao lâu/đạt không. Không có nội dung task."""
    w = wdir(t)
    les = rjson(w / "lessons.json", None) or learn_extract(t)
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
        if time.time() - max(ends or [0]) > 3600:
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


def usage_loop():
    while True:
        time.sleep(120)
        try:
            if any(t["status"] in ("running", "queued") for t in list(TASKS.values())):
                usage_refresh()
        except Exception as e:
            dlog(f"usage_loop: {e}")


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


def new_task(text, mode="balanced", project="", template="", kind="", pdir="", judge=None):
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
    if judge is not None:
        t["judge"] = bool(judge)
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


def pick_pm(mode=None):
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
FONT_NAMES = ("Geist-Variable.woff2", "GeistMono-Variable.woff2")


def font_file(name):
    if name not in FONT_NAMES:
        return None
    if FONTS_B64:
        b = (json.loads(FONTS_B64) or {}).get(name)
        return base64.b64decode(b) if b else None
    f = APP.parent / "fonts" / name                 # chạy từ mã nguồn (dev)
    return f.read_bytes() if f.is_file() else None


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
    d["judge"] = judge_on(t)
    d["cost"] = t.get("cost") or {}
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
        timep = min(0.95, (time.time() - cur.get("started_ts", time.time())) / expected_secs(t.get("mode", "balanced")))
        ws = cur.get("subtasks") or []                       # app tự tính: worker đã xong / (worker + 1 bước tổng hợp)
        if tot:                                              # task cũ còn plan.json
            est = sum(x in ("done", "completed", "failed", "skipped", "partial") for x in st) / tot
        elif ws:
            wfin = sum(x.get("status") != "running" for x in ws)
            est = max(0.9 * wfin / (len(ws) + 1), timep * 0.85)
            tot = len(ws) + 1
        else:
            est = timep
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
        if "Cache-Control" not in (headers or {}):
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
        if u.path.startswith("/fonts/"):            # font giao diện (nhúng sẵn, không tải từ mạng)
            data = font_file(u.path[7:])
            if not data:
                return self.send(404, {"error": "not found"})
            return self.send(200, data, "font/woff2", {"Cache-Control": "max-age=31536000, immutable"})
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
        if path == "/api/usage":
            return usage_state(force=bool(d.get("force")))
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
                            d.get("kind", ""), d.get("pdir", ""), d.get("judge"))
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


def timing_summary(lines):
    """Cộng thời gian theo loại bước từ nhật ký giờ (khoảng cách tới bước kế tiếp trong cùng lượt, tối đa 30 phút/bước)."""
    rows = []
    for x in lines:
        p = x.split("\t", 1)
        try:
            rows.append((datetime.datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S").timestamp(), p[1] if len(p) > 1 else ""))
        except Exception:
            pass
    by = {}                                   # giữ đúng thứ tự trong nhật ký (sắp xếp lại làm lệch khi trùng giây)
    for (t0, a), (t1, _) in zip(rows, rows[1:]):
        if 0 <= t1 - t0 <= 1800 and a != "(xong lượt)":
            k = ("Giao worker + chờ" if a.startswith("Giao ") else "Chạy lệnh" if a.startswith("Chạy") else
                 "Đọc/tìm file" if a.startswith(("Đọc", "Tìm", "Xem")) else "Ghi/sửa file" if a.startswith(("Ghi", "Sửa")) else
                 "Kiểm tra" if a.startswith("Kiểm tra") else "Sub-agent" if a.startswith("Sub-agent") else
                 "Web" if "web" in a.lower() else "Claude suy nghĩ/viết")
            n, sec = by.get(k, (0, 0))
            by[k] = (n + 1, sec + t1 - t0)
    if not by:
        return []
    tot = sum(v[1] for v in by.values()) or 1
    return ["  Thời gian theo loại bước: " + "; ".join(f"{k} {int(v[1]) // 60}m{int(v[1]) % 60:02d}s ({v[0]} bước, {round(100 * v[1] / tot)}%)"
                                                      for k, v in sorted(by.items(), key=lambda kv: -kv[1][1])), ""]


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
            tls = [f for f in dict.fromkeys([tdir(t) / "timeline.log", d / "timeline.log"]) if f.is_file()]
            for f in tls:
                z.write(f, f"{t['id']}/{'timeline.log' if f.parent == tdir(t) else 'timeline_dang_lam.log'}")
            lines += timing_summary([x for f in tls for x in f.read_text("utf-8", "replace").splitlines()])
            if t.get("cost"):
                lines += ["  Mức dùng cả task ≈ " + "; ".join(f"{k} +{v.get('five_hour', 0)}% phiên +{v.get('week', 0)}% tuần"
                                                          for k, v in t["cost"].items()), ""]
            for f in [tdir(t) / "task.json", d / "run.log", d / "CLAUDE.md", d / "AGENTS.md"]:
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
    threading.Thread(target=usage_loop, daemon=True).start()
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
        hard = os.environ.get("AIO_HARD") == "1" or (os.environ.get("AIO_MODE") == "balanced" and (a.hard or v.name != "v1"))
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


IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


IMG_MAKE = re.compile(r"vẽ|tạo (?:ảnh|hình|asset|icon|banner|keyframe|storyboard|sprite|logo|thumbnail)|render|imagegen|keyframe|"
                      r"sprite|\.png|\.jpe?g|\.webp|minh ho[ạa]|thumbnail|poster|banner|artwork|background|\bbg\b|logo|asset", re.I)
IMG_FAIL_F = "img_fail.json"
IMG_FAIL_TTL = 30 * 60          # 1 bên không vẽ được: 30 phút tới giao thẳng bên kia (chỉ thử 1 lần)


def img_failed_recent(worker):
    return time.time() - float((rjson(DATA / IMG_FAIL_F, {}) or {}).get(worker, 0)) < IMG_FAIL_TTL


def mark_img_fail(worker):
    d = rjson(DATA / IMG_FAIL_F, {}) or {}
    d[worker] = int(time.time())
    wjson(DATA / IMG_FAIL_F, d)


def worker_fallback(st, v, a, edit, want_img):
    """Worker lỗi / hết lượt / báo xong mà không ra ảnh -> app tự giao worker kia làm lại NGAY trong cùng lệnh
    (không quay về Claude). Gemini -> GPT cho mọi việc; GPT -> Gemini chỉ cho việc vẽ ảnh.
    Bên nào không vẽ được ảnh -> nhớ 30 phút, việc vẽ sau giao thẳng bên kia (chỉ thử 1 lần)."""
    src = a.worker
    dst = "codex" if src == "gemini" else "gemini"
    no_img = want_img and st.get("status") == "completed" and not any(
        f.suffix.lower() in IMG_EXT for f in v.rglob("*") if "_backup" not in f.parts) and not any(
        str(x).lower().endswith(IMG_EXT) for x in st.get("edited") or [])
    failed = st.get("status") in ("limit", "failed", "timeout")
    if want_img and (no_img or failed) and "giao thẳng" not in st.get("error", ""):
        mark_img_fail(src)                       # thật sự đã thử mà không vẽ được (không tính lần bỏ qua)
    if not (no_img or failed) or (src == "codex" and not want_img):
        return
    cfgd = rjson(CFG_FILE, {}) or {}
    ok = ((cfgd.get("ais") or {}).get(dst) or {}).get("ok") is not False
    if not find_bin("codex" if dst == "codex" else "agy") or not ok or usage_block(dst, fresh=dst == "codex") \
            or (want_img and img_failed_recent(dst) and st.get("error", "").find("giao thẳng") < 0):
        return                                   # bên kia không dùng được / cũng vừa không vẽ được -> để Claude quyết
    gv = v / ("gpt" if dst == "codex" else "gemini")
    gv.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(APP), "worker", dst, "--task", str(v / "TASK.md"), "--out", str(v / (gv.name + "_out")),
           "--vdir", str(gv), "--timeout", str(a.timeout)] + (["--edit", str(edit)] if edit else []) + \
          (["--write"] if dst == "gemini" and want_img and not edit else [])
    try:
        subprocess.run(cmd, cwd=str(Path.cwd()), env=dict(os.environ, AIO_WCHILD="1", AIO_NOFALLBACK="1"),
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=a.timeout + 180, creationflags=NO_WINDOW)
    except Exception as e:
        dlog(f"worker_fallback: {e}")
    gs = rjson(gv / "status.json", {}) or {}
    if (gv / "result.md").is_file():
        shutil.copy2(gv / "result.md", v / "result.md")
    st[src + "_error"] = (("Gemini" if src == "gemini" else "GPT") + " không tạo ra ảnh" if no_img else st.get("error", ""))[:300]
    tag = "gpt" if dst == "codex" else "gemini"
    st.update(status=gs.get("status") or "failed", model=f"{tag}:" + str(gs.get("model") or "?"), via=f"{tag}-fallback",
              error=gs.get("error", ""), seconds=(st.get("seconds") or 0) + (gs.get("seconds") or 0))
    if gs.get("edited"):
        st["edited"] = gs["edited"]


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
2. Việc lớn: nghĩ kế hoạch ngắn trước (không cần ghi file); việc nhỏ thì làm luôn.
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


def code_md(txt, t, py="", app=""):
    """Hướng dẫn Code mode: thay mục Vai trò/Chia việc/Nghiệm thu/chế độ bằng quy trình code + chạy thử."""
    code = CODE_MD.format(pdir=t.get("pdir", ""))
    txt = re.sub(r"(?ms)^## Vai trò\n.*?(?=^## )", lambda m: code + "\n", txt, count=1)
    for sec in ("Chia việc (Gemini nhanh, GPT chính xác hơn)", "Nghiệm thu", "Ảnh có đánh dấu", "Ảnh & storyboard"):
        txt = re.sub(r"(?ms)^## " + re.escape(sec) + r"\n.*?(?=^## )", "", txt, count=1)
    txt = re.sub(r"(?ms)^## Chế độ: .*?(?=^## )",
                 "## Chế độ: Code (Opus, suy nghĩ medium)\n- Ưu tiên chạy được và đúng; chạy thử thật trước khi báo xong.\n\n"
                 + (f"""## Giám sát (BẬT)
Sau khi chạy thử/test đã qua: gọi giám khảo độc lập soát phần code đã sửa theo đúng yêu cầu (viết yêu cầu + điều phải đạt
vào tasks/criteria.md trước):
  "{py}" "{app}" judge --files <file/thư mục đã sửa> --criteria tasks/criteria.md --brief "<yêu cầu>" --out work/J1
Fail -> sửa đúng lỗi nêu, chạy test lại, chấm lại; lặp tới khi ĐẠT (tối đa 4 vòng; vẫn fail thì báo rõ còn lỗi gì).

""" if judge_on(t) and t.get("pm", "claude") == "claude" else ""), txt, count=1)
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
    ap.add_argument("--bg", action="store_true", help="giao xong trả về ngay (worker chạy nền); chờ cả lô bằng lệnh wait")
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
        if a.bg:                            # giao nhanh cả lô, chờ 1 lần: wait work/S1 work/S2 ...
            print(json.dumps({**st, "status": "running", "wait": str(out)}, ensure_ascii=False))
            sys.exit(0)
        return wait_status(v, a.timeout + 180)
    exe = find_bin("codex" if a.worker == "codex" else "agy")
    task_txt = task.read_text(encoding="utf-8") if task.exists() else ""
    want_img = (a.worker == "codex" or a.write or bool(edit)) and bool(IMG_MAKE.search(task_txt))
    skip_gem = want_img and img_failed_recent(a.worker)       # bên này vừa không vẽ được (30 phút) -> giao thẳng bên kia
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
    ub = usage_block(a.worker, fresh=a.worker == "codex") if not use_api else 0
    if bad_edit:
        st.update(status="failed", error=bad_edit)
    elif ub:
        st.update(status="limit", reset=ub, error=("Gói đã dùng gần hết — Maestro tạm dừng AI này để không dùng credit; "
                                                   "dùng lại lúc " + datetime.datetime.fromtimestamp(ub).strftime("%H:%M %d/%m")))
    elif not task.exists():
        st.update(status="failed", error=f"Không thấy {task}")
    elif skip_gem and not os.environ.get("AIO_NOFALLBACK"):
        (v / "TASK.md").write_text(task_txt, encoding="utf-8")
        st.update(status="failed", error=("Gemini" if a.worker == "gemini" else "GPT") + " vừa không tạo được ảnh (30 phút qua) — giao thẳng bên kia")
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
            # Balanced: medium; lên high khi Claude đánh dấu việc khó (--hard) hoặc giao lại vì lỗi (v2+). Pro: bước quá khó.
            hard_ = os.environ.get("AIO_HARD") == "1" or (md_ == "balanced" and (a.hard or v.name != "v1"))
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
    if not os.environ.get("AIO_NOFALLBACK"):
        worker_fallback(st, v, a, edit, want_img)
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


# ---------------------------------------------------------------- mức dùng (usage) + chỉ dùng trong gói (không dùng credit)
USAGE_F = DATA / "usage.json"
IN_PLAN_LEFT = 2.0          # còn <= 2% của cửa sổ 5 giờ hoặc tuần -> coi như hết, không chạy (để không tràn sang credit)


def _pct_left(used):
    try:
        return max(0.0, min(100.0, 100.0 - float(used)))
    except (TypeError, ValueError):
        return None


def _ts(v):
    """Thời điểm reset: số giây epoch / mili giây / chuỗi ISO -> epoch giây (None nếu không đọc được)."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
        return int(f / 1000) if f > 1e11 else int(f)
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _find_key(o, key, depth=0):
    if depth > 6:
        return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find_key(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_key(v, key, depth + 1)
            if r is not None:
                return r
    return None


def codex_usage():
    """Mức dùng ChatGPT/Codex: Codex tự lưu trong file phiên trên máy (sự kiện có 'rate_limits', như lệnh /status) — không tốn lượt."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    if not home.is_dir():
        return {"known": False, "note": "Chưa có dữ liệu (chưa chạy ChatGPT lần nào)"}
    files = sorted(home.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:8]
    for f in files:
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, f.stat().st_size - 400_000))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except Exception:
            continue
        for line in reversed(tail):
            if "rate_limits" not in line:
                continue
            try:
                rl = _find_key(json.loads(line), "rate_limits")
            except Exception:
                continue
            if not isinstance(rl, dict):
                continue
            at = f.stat().st_mtime
            out = {"known": True, "at": int(at), "plan": str(rl.get("plan_type") or "")}
            for k, name in (("primary", "five_hour"), ("secondary", "week")):
                w = rl.get(k) or {}
                if isinstance(w, dict) and w.get("used_percent") is not None:
                    rs = _ts(w.get("resets_at")) or (int(at + float(w["resets_in_seconds"])) if w.get("resets_in_seconds") is not None else None)
                    out[name] = {"left": _pct_left(w.get("used_percent")), "resets_at": rs, "minutes": w.get("window_minutes")}
            cr = rl.get("credits") or {}
            if isinstance(cr, dict):
                out["credits"] = {"has": bool(cr.get("has_credits")), "unlimited": bool(cr.get("unlimited")), "balance": cr.get("balance")}
            for name in ("five_hour", "week"):      # số đã cũ mà qua giờ reset -> coi như đầy lại
                w = out.get(name)
                if w and w.get("resets_at") and w["resets_at"] < time.time():
                    w.update(left=100.0, stale=True)
            return out
    return {"known": False, "note": "Chưa có dữ liệu mức dùng (chạy ChatGPT 1 lần để có)"}


def claude_token():
    """Token đăng nhập Claude Code trên máy (chỉ dùng để hỏi mức dùng của chính tài khoản, gửi tới Anthropic; không lưu lại)."""
    try:
        if IS_MAC:
            r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                               capture_output=True, text=True, timeout=10)
            d = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
        else:
            d = rjson(Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json", {}) or {}
        return str((d.get("claudeAiOauth") or {}).get("accessToken") or "")
    except Exception:
        return ""


def claude_usage():
    """Mức dùng Claude (5 giờ / tuần / extra usage) — cùng kênh lệnh /usage của Claude Code dùng. Kênh nội bộ: có thể đổi."""
    import urllib.error
    import urllib.request
    tok = claude_token()
    if not tok:
        return {"known": False, "note": "Không đọc được đăng nhập Claude trên máy"}
    req = urllib.request.Request(os.environ.get("AIO_CLAUDE_USAGE") or "https://api.anthropic.com/api/oauth/usage",
                                 headers={"Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20",
                                          "User-Agent": "maestro", "Accept": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"known": False, "note": f"Claude chưa trả mức dùng (HTTP {e.code}) — chạy 1 task Claude rồi thử lại"}
    except Exception as e:
        return {"known": False, "note": f"Không kết nối được: {e}"[:120]}
    out = {"known": True, "at": int(time.time())}
    for k, name in (("five_hour", "five_hour"), ("seven_day", "week"), ("seven_day_opus", "week_opus")):
        w = d.get(k)
        if isinstance(w, dict) and w.get("utilization") is not None:
            out[name] = {"left": _pct_left(w.get("utilization")), "resets_at": _ts(w.get("resets_at"))}
    ex = d.get("extra_usage")
    if isinstance(ex, dict):
        out["credits"] = {"has": bool(ex.get("is_enabled")), "used": ex.get("used_credits"), "limit": ex.get("monthly_limit")}
    return out if len(out) > 2 else {"known": False, "note": "Claude trả dữ liệu không đọc được"}


_usage_lock = threading.Lock()


def usage_refresh(force=False, only=None):
    """Đọc lại mức dùng (Claude: tối đa 1 lần/2 phút; ChatGPT: đọc file trên máy) -> data/usage.json (worker đọc chung)."""
    with _usage_lock:
        d = rjson(USAGE_F, {}) or {}
        for ai, fn, ttl in (("codex", codex_usage, 20), ("claude", claude_usage, 120)):
            if only and ai not in only:
                continue
            if force or time.time() - (d.get(ai) or {}).get("checked", 0) > ttl:
                try:
                    v = fn()
                except Exception as e:
                    v = {"known": False, "note": str(e)[:120]}
                v["checked"] = int(time.time())
                d[ai] = v
        try:
            wjson(USAGE_F, d)
        except Exception:
            pass
        return d


def in_plan_only(ai):
    """Luôn chỉ dùng trong gói: không bao giờ để AI tràn sang credit / extra usage (mặc định, không tắt được)."""
    return True


def usage_block(ai, fresh=False):
    """Đã dùng gần hết gói (cửa sổ 5 giờ hoặc tuần) và đang bật 'chỉ dùng trong gói' -> giờ dùng lại được (epoch), không thì 0."""
    if ai not in ("claude", "codex") or not in_plan_only(ai):
        return 0
    u = (usage_refresh(force=True, only=[ai]) if fresh else (rjson(USAGE_F, {}) or {})).get(ai) or {}
    if not u.get("known"):
        return 0
    worst = 0
    for name in ("five_hour", "week"):
        w = u.get(name) or {}
        if w.get("left") is not None and w["left"] <= IN_PLAN_LEFT and not w.get("stale"):
            worst = max(worst, w.get("resets_at") or int(time.time() + 1800))
    return worst if worst > time.time() else 0


def usage_snap():
    d = usage_refresh(force=True)
    out = {}
    for ai in ("claude", "codex"):
        u = d.get(ai) or {}
        if u.get("known"):
            out[ai] = {k: round(100 - u[k]["left"], 1) for k in ("five_hour", "week") if (u.get(k) or {}).get("left") is not None}
    return out


def usage_task_start(t):
    try:
        t["u0"] = usage_snap()
    except Exception as e:
        dlog(f"usage_snap: {e}")


def usage_task_done(t):
    """Phần % tăng từ lúc task bắt đầu tới khi hoàn thành; task được giao tiếp sau đó thì đo lại và cộng dồn."""
    u0, u1 = t.get("u0") or {}, usage_snap()
    cost = t.get("cost") or {}
    for ai, v1 in u1.items():
        v0 = u0.get(ai) or {}
        for k in v1:
            if k in v0 and v1[k] - v0[k] > 0:
                cost.setdefault(ai, {})[k] = round(cost.get(ai, {}).get(k, 0) + v1[k] - v0[k], 1)
    t["cost"] = cost
    t["u0"] = u1                                # mốc cho lần giao tiếp theo (không phải đo lại lúc bắt đầu)


def usage_counts():
    """App tự đếm: số lượt / phút của mỗi AI hôm nay và 7 ngày qua (từ lịch sử task trên máy)."""
    now_ = time.time()
    day0 = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
    out = {k: {ai: {"runs": 0, "mins": 0} for ai in ("claude", "codex", "gemini")} for k in ("today", "week")}
    for t in list(TASKS.values()):
        pm = t.get("pm") or "claude"
        for m in t.get("messages", []):
            if m.get("role") != "work" or not m.get("started_ts"):
                continue
            items = [(pm, (m.get("ended_ts") or now_) - m["started_ts"], m["started_ts"])]
            items += [(x.get("worker"), x.get("seconds") or 0, x.get("ts") or m["started_ts"]) for x in m.get("subs") or []
                      if x.get("worker") in ("codex", "gemini")]
            for ai, sec, ts in items:
                for k, since in (("today", day0), ("week", now_ - 7 * 86400)):
                    if ts >= since and ai in out[k]:
                        out[k][ai]["runs"] += 1
                        out[k][ai]["mins"] += max(0, sec) / 60
    for k in out:
        for ai in out[k]:
            out[k][ai]["mins"] = round(out[k][ai]["mins"])
    return out


def usage_state(force=False):
    d = usage_refresh(force=force)
    lm = limits()
    return {"claude": d.get("claude") or {}, "codex": d.get("codex") or {},
            "gemini": {"known": False, "note": "Gemini chưa cho đọc mức dùng — chỉ biết khi hết lượt",
                       **({"limit_until": lm["gemini"]["until"]} if "gemini" in lm else {})},
            "plans": {k: CFG["ais"].get(k, {}).get("plan", "") for k in ("claude", "codex")},
            "in_plan": {k: in_plan_only(k) for k in ("claude", "codex")}, "counts": usage_counts()}


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
    """wait <thư mục> [thư mục ...] [giây]: chờ (cả lô) worker đang chạy xong rồi in kết quả từng cái."""
    limit = next((int(x) for x in argv if x.isdigit()), 1500)
    dirs = []
    for x in argv:
        if x.isdigit():
            continue
        p = Path(x).resolve()
        if not (p / "status.json").exists():
            vs = sorted((y for y in p.glob("v*") if y.is_dir() and y.name[1:].isdigit()), key=lambda y: int(y.name[1:]))
            p = vs[-1] if vs else p
        dirs.append(p)
    if len(dirs) == 1:
        wait_status(dirs[0], limit)
    t0, out = time.time(), {}
    while time.time() - t0 < limit:
        out = {str(p): rjson(p / "status.json", {}) or {} for p in dirs}
        if all(x.get("status") not in (None, "running") for x in out.values()):
            break
        time.sleep(3)
    res = [{"dir": k, **{kk: vv for kk, vv in x.items() if kk in ("status", "worker", "model", "error", "files", "edited", "via", "seconds")}}
           for k, x in out.items()]
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if all(x.get("status") == "completed" for x in res) else 1)


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
