"""AI Orchestrator: Claude làm PM, giao việc cho ChatGPT/Codex và Gemini/Antigravity.
Một file duy nhất. Chạy trên máy, dữ liệu nằm trên máy.

  pythonw app.py [--background]       chạy nền + mở giao diện (http://127.0.0.1:8765)
  python app.py worker codex|gemini   (Claude dùng) giao 1 sub-task cho worker
  python app.py check files|png|csv|xlsx ...   (Claude dùng) kiểm tra file kết quả
"""
import base64
import csv
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
MODES = {"fast": "Pro (nhanh, mạnh nhất)", "balanced": "Balanced (cân bằng)", "saver": "Eco (tiết kiệm hạn mức)"}
BELOW_NORMAL = 0x00004000 if IS_WIN else 0     # ưu tiên thấp: máy vẫn mượt khi AI đang chạy
PING = "Reply with the single word PONG and nothing else."

CODEX_SANDBOX = (["--sandbox", "workspace-write", "-c", 'windows.sandbox="unelevated"'] if IS_WIN
                 else ["--sandbox", "workspace-write"])
AIS = {
    "claude": {"bin": "claude", "login": ["auth", "login"], "test": ["-p", PING]},
    "codex": {"bin": "codex", "login": ["login"], "test": ["exec", "--skip-git-repo-check", *CODEX_SANDBOX, PING]},
    "gemini": {"bin": "agy", "login": [], "test": ["-p", PING]},
}
ICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><image width="96" height="96" href="data:image/png;base64,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"/></svg>'


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
    if not pid or not IS_WIN:
        return
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                       text=True, creationflags=NO_WINDOW)
    if "claude" in (r.stdout or "").lower():
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)


PS_OPEN_FOLDER = r"""
$p = $env:AIO_OPEN.TrimEnd('\')
$w = $null
foreach ($x in (New-Object -ComObject Shell.Application).Windows()) {
  try { if ($x.Document.Folder.Self.Path.TrimEnd('\') -ieq $p) { $w = $x; break } } catch {}
}
if ($w) {
  Add-Type -Namespace AIO -Name W -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);'
  $h = [IntPtr]$w.HWND
  if ([AIO.W]::IsIconic($h)) { [void][AIO.W]::ShowWindow($h, 9) }
  (New-Object -ComObject WScript.Shell).SendKeys('%')
  [void][AIO.W]::SetForegroundWindow($h)
} else { Start-Process explorer.exe -ArgumentList ('"' + $p + '"') }
"""


def open_path(p):
    p = str(p)
    if IS_WIN and os.path.isdir(p):      # thư mục đang mở sẵn -> đưa cửa sổ đó lên, không mở thêm
        try:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS_OPEN_FOLDER],
                             env=dict(os.environ, AIO_OPEN=p), creationflags=NO_WINDOW)
            return
        except Exception:
            pass
    if IS_WIN:
        os.startfile(p)  # noqa
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", p])


def refresh_path():
    """Lấy PATH mới nhất từ registry (CLI vừa cài xong vẫn tìm thấy)."""
    if not IS_WIN:
        return
    try:
        import winreg
        parts = []
        for root, key in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")]:
            try:
                with winreg.OpenKey(root, key) as k:
                    parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
        if parts:
            os.environ["PATH"] = ";".join(parts + [os.environ.get("PATH", "")])
    except Exception:
        pass


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
DEFAULT_INSTRUCTIONS = ""
OLD_DEFAULT_PREFIX = "Xưng hô: tôi - bạn. Trả lời tiếng Việt, ngắn gọn"


def load_cfg():
    c = rjson(CFG_FILE, {}) or {}
    c.setdefault("token", secrets.token_urlsafe(18))
    c.setdefault("output", "")
    c.setdefault("instructions", DEFAULT_INSTRUCTIONS)
    if c["instructions"].startswith(OLD_DEFAULT_PREFIX):     # bỏ hướng dẫn mặc định của bản cũ
        c["instructions"] = ""
    c.setdefault("ais", {})
    for a in c["ais"].values():
        a.pop("pending", None)
        a.pop("code", None)
        a.pop("window", None)
        a.pop("url", None)
        a.pop("note", None)
    return c


def save_cfg():
    with _cfg_lock:
        wjson(CFG_FILE, CFG)


def pick_folder():
    try:
        import tkinter
        from tkinter import filedialog
        r = tkinter.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        d = filedialog.askdirectory(parent=r)
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
        if t.get("status") == "running":      # app bị tắt khi đang chạy -> tự chạy tiếp
            kill_orphan(t.get("pid"))
            end_work(t, "interrupted")
            t["status"] = "queued"
            t["next"] = {"kind": "resume"}
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
    "fast": """- Ưu tiên THỜI GIAN và chất lượng. Chạy song song tối đa {n} worker/sub-agent cho các phần độc lập.
- Dùng model mạnh ngay từ đầu để ít phải sửa. Giám khảo được dùng model mạnh, tối đa 2 vòng sửa.""",
    "balanced": """- Cân bằng chất lượng, thời gian, hạn mức. Song song tối đa {n} worker/sub-agent cho phần độc lập; tuần tự khi phụ thuộc.
- Model nhỏ/nhanh cho việc đơn giản; model mạnh cho việc khó. Nâng model khi kết quả không đạt, hạ khi việc lặp lại.""",
    "saver": """- Ưu tiên TIẾT KIỆM HẠN MỨC. Làm tuần tự, 1 worker tại một thời điểm. Việc nào tự làm được bằng lệnh/script thì không gọi AI.
- Dùng model nhỏ nhất đủ làm được việc cho worker và sub-agent (--model); chỉ nâng khi đã thất bại 1 lần.
- Giám khảo: dùng model nhỏ, chỉ chấm sản phẩm cuối, tối đa 1 vòng sửa. Không làm thêm ngoài yêu cầu.""",
}


def write_claude_md(t):
    py, app = py_console(), str(APP)
    mode = t.get("mode") if t.get("mode") in MODES else "balanced"
    mode_name = MODES[mode]
    mode_rules = MODE_RULES[mode].format(n="1 (máy cấu hình thấp)" if LOW_SPEC else str(min(3, MAX_RUNNING + 1)))
    on = [w for w in ("codex", "gemini") if find_bin(AIS[w]["bin"]) and CFG["ais"].get(w, {}).get("ok") is not False]
    desc = {"codex": "codex — ChatGPT/Codex: code, CSV/Excel, script, tạo ảnh ($imagegen)",
            "gemini": "gemini — Gemini/Antigravity: research, tổng hợp, phân tích, ý tưởng (chỉ trả văn bản)"}
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
  Không sửa, không xóa, không di chuyển file nào ngoài thư mục làm việc và OUTPUT.
- OUTPUT (nơi lưu sản phẩm giao người dùng): {out_dir(t)}
- Sản phẩm của các task trước (để dùng lại, tránh làm lại): danh mục ở {STORE / "registry.json"}

## Worker sẵn có trên máy
{chr(10).join('- ' + desc[w] for w in on) or '- (không có — tự làm)'}
Tự chọn worker cho từng sub-task, không bắt buộc dùng. Việc nhỏ hoặc cần phán đoán thì tự làm.
Worker lỗi đăng nhập/không chạy: coi như không có, chuyển worker khác hoặc tự làm.

Giao sub-task: viết yêu cầu đầy đủ ngữ cảnh vào tasks/<ID>.md (dòng đầu = tên ngắn; worker không thấy
cuộc trò chuyện), rồi chạy:
  "{py}" "{app}" worker codex  --task tasks/S1.md --out work/S1 [--model <model>] [--timeout 900]
  "{py}" "{app}" worker gemini --task tasks/S2.md --out work/S2
Kết quả ở work/<ID>/vN/ (result.md, status.json, worker.log, file do worker tạo). Sửa lỗi: giao lại cùng --out.

## Quy trình nghiệm thu
1. TIÊU CHÍ: trước khi làm, viết tasks/criteria.md từ chính yêu cầu của người dùng (sản phẩm phải có gì, đúng gì, định dạng/
   kích thước, style, lỗi cần tránh; đánh dấu BẮT BUỘC). Ngắn, kiểm chứng được. Người dùng đổi yêu cầu -> cập nhật. Không hỏi duyệt.
2. CHẤM ĐÚNG CHỖ (ưu tiên tốc độ):
   - Văn bản hoặc task nhẹ (phân tích, research, idea, kịch bản, báo cáo, tài liệu chữ): chấm 1 LẦN, nhanh — giám khảo
     model nhỏ (judge --model haiku) hoặc giao gemini đọc soát theo criteria.md. Chỉ sửa lỗi nêu, không chấm lại.
   - Sản phẩm hình/âm thanh/code/bảng tính: check kỹ thuật bằng lệnh check trước; rồi gọi giám khảo 1 LẦN cho CẢ LÔ ở cuối:
     "{py}" "{app}" judge --files <thư mục/file> --criteria tasks/criteria.md [--refs "<mẫu đã duyệt>"] --brief "<yêu cầu>" --out work/J1 [--model <model>]
     Kết quả work/J1/vN/review.json. Fail -> sửa đúng lỗi nêu, tối đa 1 vòng sửa, chỉ chấm lại file đã sửa; vẫn fail -> báo người dùng.
   - Lô lớn (từ 5 sản phẩm cùng loại, tốn nhiều thời gian/hạn mức): làm 1 mẫu, tự xem (không cần giám khảo) rồi chạy cả lô.
   - Giám khảo dùng model nhỏ (sonnet/haiku) trừ chế độ Pro.
   Bỏ qua hết khi hỏi đáp/tra cứu hoặc người dùng bảo làm nhanh/nháp.

## Storyboard
Storyboard viết dạng BẢNG CHỮ (cảnh, thời lượng, hình ảnh, chữ trên màn hình, âm thanh, CTA) trong câu trả lời hoặc tài liệu.
KHÔNG tự tạo ảnh/khung hình storyboard, trừ khi người dùng yêu cầu rõ (vẽ/minh hoạ/render storyboard).

## Song song
Nhiều sản phẩm độc lập cùng loại (nhiều ảnh, nhiều file): chia thành 2–3 worker chạy song song (lệnh worker chạy nền, mỗi
worker --out riêng), không dồn cả lô vào 1 worker. Giới hạn theo chế độ bên dưới.

## Trả kết quả theo từng chặng
Task nhiều phần (vd phân tích → idea → sản phẩm): xong phần nào có ích cho người dùng thì ghi ngay nội dung phần đó (markdown
ngắn gọn) vào ./stages/<số thứ tự>.md — giao diện hiện ngay cho người dùng đọc trong lúc bạn làm tiếp. Câu trả lời cuối vẫn phải
đủ ý (gộp kết quả các chặng); các chặng sẽ được ẩn khi task xong.

## Model
Bạn đang chạy {'Opus' if t.get('mode') == 'fast' else 'Sonnet'}. {'' if t.get('mode') == 'fast' else 'Bước khó cần suy luận sâu (phân tích chiến lược, lập kế hoạch phức tạp, gỡ lỗi khó): giao cho sub-agent (Task/Agent) với model opus, chỉ cho đúng bước đó.'}

## Tạo ảnh
Tạo bằng codex ($imagegen) hoặc cách khác có sẵn. Chỉ khi không thể tạo được (lỗi, hết hạn mức): viết prompt vào
OUTPUT/prompts/, rồi hỏi người dùng (question.md): "Dán prompt trong <đường dẫn> vào ChatGPT, lưu ảnh vào OUTPUT/images/
rồi trả lời 'xong'". Khi được trả lời: check + chấm độc lập như trên.

{brain_md}{tpl_md}## Chế độ làm việc người dùng chọn cho task này: {mode_name}
{mode_rules}
- Worker status "limit": chuyển worker khác hoặc tự làm nếu đảm bảo chất lượng; nếu không, hỏi người dùng.

## Nghiệm thu
- status completed của worker chưa phải là xong. Luôn kiểm tra file thật.
- Đo được: "{py}" "{app}" check files|png|csv|xlsx ... (xem --help). Nội dung/chất lượng: theo "Quy trình nghiệm thu".
- Lỗi: giao sửa đúng phần lỗi, tối đa 2 lần mỗi sub-task; quá thì ghi partial.

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
    (wdir(t) / "CLAUDE.md").write_text(txt, encoding="utf-8")


def build_prompt(t, nxt):
    kind = nxt.get("kind")
    files = nxt.get("files") or []
    fl = f"\nFile mới trong {tdir(t) / 'input'}: {', '.join(files)}." if files else ""
    text = nxt.get("text", "")
    if kind == "start" or not t.get("session_started"):
        base = f"Task mới. Yêu cầu của người dùng:\n\n{t['text']}\n"
        if kind != "start" and text:
            base += f"\nBổ sung của người dùng:\n{text}\n"
        return base + fl + "\nLàm theo CLAUDE.md."
    if t.get("session_started"):        # app có thể vừa cập nhật: luật mới trong CLAUDE.md áp dụng cho lượt này
        fl += "\nCLAUDE.md có thể vừa được cập nhật: đọc lại ./CLAUDE.md và áp dụng bản mới nhất (thay cho quy tắc cũ trong phiên)."
    if kind == "answer":
        return f"Người dùng trả lời:\n\n{text}\n{fl}\nTiếp tục task."
    if kind == "urgent":
        return (f"Người dùng vừa gửi tin nhắn trong lúc bạn đang làm (bạn đã bị ngắt):\n\n{text}\n{fl}\n"
                "Đọc tasks/plan.json, điều chỉnh kế hoạch theo tin nhắn này rồi tiếp tục. Làm theo CLAUDE.md.")
    if kind == "followup":
        return f"Tin nhắn mới của người dùng:\n\n{text}\n{fl}\nLàm theo CLAUDE.md."
    return "Task bị dừng giữa chừng. Đọc tasks/plan.json và chạy tiếp từ bước đang dở. Làm theo CLAUDE.md."


def gen_title(t):
    """Đặt tên task ngắn như Claude (model nhỏ, tốn rất ít hạn mức). Lỗi thì giữ tên tạm."""
    exe = find_bin("claude")
    try:
        if exe and CFG["ais"].get("claude", {}).get("ok"):
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
    exe = find_bin("claude")
    nxt = t.pop("next", None) or {"kind": "start"}
    d = tdir(t)
    if not exe or not CFG.get("output"):
        t["status"] = "failed"
        tmsg(t, "claude", "Chưa cài Claude Code" if not exe else "Chưa chọn thư mục kết quả (Cài đặt → Set up)", kind="error")
        return tsave(t)
    if not t.get("folder"):
        base = safe_name(t["title"], 80)          # thư mục kết quả = tên task, nằm ngay trong thư mục export
        used = {x.get("folder") for x in TASKS.values() if x is not t}
        name, n = base, 2
        while name in used or (Path(CFG["output"]) / name).exists():
            name, n = f"{base} ({n})", n + 1
        t["folder"] = name
    out_dir(t).mkdir(parents=True, exist_ok=True)
    write_claude_md(t)
    d = wdir(t)
    q = d / "question.md"
    if q.exists():
        q.unlink()
    args = [exe, "-p", build_prompt(t, nxt), "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash,Read,Write,Edit,MultiEdit,Glob,Grep,LS,WebSearch,WebFetch,Task,Agent,TodoWrite"]
    args += ["--model", "opus" if t.get("mode") == "fast" else "sonnet"]   # Balanced/Eco: Sonnet điều phối, bước khó gọi Opus
    if t.get("session_started"):
        args += ["--resume", t["session"]]
    else:
        t["session"] = str(uuid.uuid4())
        args += ["--session-id", t["session"]]
    for f in dict.fromkeys([str(out_dir(t))] + drives()):
        args += ["--add-dir", f]
    t.update(status="running", started=now(), _pause=False, _cancel=False, _quit=False, _redirect=None)
    t["wi"] = tmsg(t, "work", "", status="running", started=now(), started_ts=time.time(), steps=[])
    tsave(t)
    try:
        proc = subprocess.Popen(args, cwd=str(d), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
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
    m = re.search(r" worker (codex|gemini) .*?--out\s+\"?([^\s\"]+)", s)
    if m:
        sid = re.split(r"[\\/]", m.group(2).rstrip("/\\"))[-1]
        return f"Giao {sid} cho {'ChatGPT' if m.group(1) == 'codex' else 'Gemini'}"
    if " check " in s:
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


def path_in_text(text):
    """Thư mục người dùng/Claude nêu trong câu trả lời (vd export vào chỗ khác thư mục mặc định)."""
    cands = re.findall(r"`([A-Za-z]:[\\/][^`\n]+)`", text or "") + re.findall(r"([A-Za-z]:[\\/][^\n`*\"<>|]+)", text or "")
    for c in cands:
        c = c.strip()
        for _ in range(200):                 # bớt dần ký tự cuối cho tới khi ra đường dẫn có thật
            if len(c) <= 3:
                break
            p = Path(c.rstrip(" .,;:"))
            if p.exists():
                return p if p.is_dir() else p.parent
            c = c[:-1]
    return None


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


def watch(t, p, nxt):
    raw = open(wdir(t) / "run.log", "a", encoding="utf-8")
    raw.write(f"\n===== {now()} {nxt.get('kind')} =====\n")
    result = None
    w = work(t)
    since = (w or {}).get("started_ts", time.time())
    for line in p.stdout:
        raw.write(line)
        raw.flush()
        try:
            ev = json.loads(line)
        except Exception:
            continue
        typ = ev.get("type")
        if ev.get("session_id") and not t.get("session_started"):
            t["session_started"] = True
            t["session"] = ev["session_id"]
            tsave(t)
        if typ == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    step(t, b["text"].strip().split("\n")[0])
                elif b.get("type") == "tool_use":
                    step(t, summarize_tool(b))
        elif typ == "result":
            result = ev
    err = p.stderr.read()
    if err.strip():
        raw.write("--- stderr ---\n" + err)
    raw.close()
    p.wait()
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
            end_work(t, "failed")
            msg = (result or {}).get("result") or err.strip()[-400:] or f"exit {p.returncode}"
            t["status"] = "failed"
            tmsg(t, "claude", msg, kind="error")
        t["_redirect"] = None
        tsave(t)
    try:
        registry(force=True)
    except Exception:
        pass


_ready = {"t": 0.0, "ok": False}


def claude_ready(t):
    """Claude chưa đăng nhập: tự mở trang đăng nhập 1 lần cho task này, task chờ rồi tự chạy."""
    if time.time() - _ready["t"] > (60 if _ready["ok"] else 5):
        _ready["t"] = time.time()
        cur = CFG["ais"].get("claude", {})
        _ready["ok"] = bool(cur.get("ok")) if cur.get("pending") else bool(ai_status("claude").get("ok"))
    if _ready["ok"]:
        if t.pop("need_login", None):
            tsave(t)
        return True
    if not t.get("need_login"):
        t["need_login"] = True
        tsave(t)
        ai_login("claude")
    return False


def scheduler():
    while True:
        with _tlock:
            running = sum(1 for t in TASKS.values() if t["status"] == "running")
            queued = sorted((t for t in TASKS.values() if t["status"] == "queued"), key=lambda x: x["created"])
            for t in queued:
                if running >= MAX_RUNNING:
                    break
                if not claude_ready(t):
                    continue          # chờ đăng nhập Claude (đã tự mở trang đăng nhập)
                if t.get("titling") and time.time() - t.get("created_ts", 0) < 30:
                    continue          # chờ đặt tên xong để thư mục kết quả có tên đẹp
                start_run(t)
                running += 1
        time.sleep(1)


def act(tid, action, text="", files=None):
    t = TASKS.get(tid)
    if not t:
        return {"error": "Không tìm thấy task"}
    s = t["status"]
    files = [f for f in (files or []) if (tdir(t) / "input" / f).exists()]
    text = (text or "").strip()
    with _tlock:
        t.pop("auto_paused", None)
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
        elif action == "send" and (text or files):
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
        elif action == "mode" and text in MODES:
            t["mode"] = text                           # áp dụng từ lượt chạy kế tiếp
            write_claude_md(t)
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


def new_task(text, mode="balanced", project="", template=""):
    if not text.strip():
        return {"error": "Chưa nhập yêu cầu"}
    tid = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    while tid in TASKS:
        tid += "b"
    t = {"id": tid, "title": re.sub(r"\s+", " ", text.strip())[:50], "text": text.strip(),
         "status": "draft", "created": now(), "created_ts": time.time(), "messages": [],
         "mode": mode if mode in MODES else "balanced", "w2": True}
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


def _jwt_email(tok):
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("email", "")
    except Exception:
        return ""


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


def ai_state():
    return {k: {"installed": bool(find_bin(v["bin"])), **CFG["ais"].get(k, {})} for k, v in AIS.items()}


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
            cur.update(ok=ok, msg="", email=(j.get("email") or ai_email(ai)) if ok else "")
        elif ai == "codex":
            p = _run_hidden([exe, "login", "status"], 20)
            out = ((p.stdout or "") + (p.stderr or "")).lower()
            ok = p.returncode == 0 and "not logged" not in out
            cur.update(ok=ok, msg="", email=ai_email(ai) if ok else "")
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
            try:
                from winpty import PtyProcess
            except ImportError:      # cài thư viện terminal ảo (1 lần)
                r = subprocess.run([py_console(), "-m", "pip", "install", "--user", "--disable-pip-version-check",
                                    "pywinpty"], capture_output=True, text=True, errors="replace", timeout=300,
                                   creationflags=NO_WINDOW)
                dlog("pip install pywinpty -> exit %s\n%s\n%s" % (r.returncode, (r.stdout or "")[-1500:], (r.stderr or "")[-1500:]))
                import importlib
                import site
                importlib.invalidate_caches()
                sys.path.append(site.getusersitepackages())
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
                env = dict(os.environ, BROWSER="none")   # không để CLI tự mở tab mới: web mở link trong cửa sổ nhỏ
                if ai == "claude":
                    return claude_term_login(exe, d, cur, env)
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
                    cur["msg"] = "Đăng nhập chưa xong. Bấm Đăng nhập lại; nếu vẫn lỗi, Export log gửi tôi."
            else:
                # agy -p không tự hoàn tất đăng nhập được (không có chỗ dán mã) -> chỉ chạy ẩn khi máy đã có phiên lưu
                ok = (not switch) and gemini_has_cred() and gemini_ping(exe, 120)[0]
                if not ok:
                    if switch:
                        gemini_forget()
                    try:
                        done = gemini_term_login(exe, d, cur)
                    except Exception as e:           # không dựng được terminal ảo -> dùng cửa sổ như cũ
                        import traceback
                        done = None
                        cur["note"] = f"{type(e).__name__}: {e}"[:200]
                        dlog("gemini hidden login failed:\n" + traceback.format_exc())
                    else:
                        lg0 = LOGINS.get("gemini")
                        dlog(f"gemini hidden login done={done}; screen tail:\n" + (lg0.buf[-1500:] if lg0 else ""))
                    lg = LOGINS.pop("gemini", None)
                    if lg:
                        lg.kill()
                    cur.pop("code", None)
                    cur.pop("url", None)
                    if done is None and IS_WIN:
                        cur["window"] = "login"
                        subprocess.run(["cmd.exe", "/c", "start", "/wait", "Maestro - Gemini", exe],
                                       cwd=str(d), creationflags=NO_WINDOW)
                        cur.pop("window", None)
                    ok = gemini_has_cred() and gemini_ping(exe, 120)[0]
                cur.update(ok=ok, msg="" if ok else "Chưa đăng nhập được", email="")
        except Exception as e:
            cur.update(ok=False, msg=str(e)[:150])
        finally:
            LOGINS.pop(ai, None)
            cur.pop("pending", None)
            cur.pop("window", None)
            cur.pop("code", None)
            cur.pop("url", None)
            cur["at"] = now()
            save_cfg()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def claude_term_login(exe, d, cur, env):
    """Claude: chạy 'claude auth login' trong terminal ẩn; người dùng đăng nhập ở cửa sổ nhỏ rồi dán mã vào web."""
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
    for ai in ("claude", "codex"):
        if "ok" not in CFG["ais"].get(ai, {}):
            ai_status(ai)
    save_cfg()


# ---------------------------------------------------------------- HTTP
def ui_html():
    if UI_B64:
        return base64.b64decode(UI_B64)
    return (APP.parent / "ui.html").read_bytes()


def task_detail(t):
    d = {k: t.get(k) for k in ("id", "title", "status", "created", "finished", "mode")}
    if t.get("need_login") and t["status"] == "queued":
        d["status"] = "login"
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
        st = [str(x.get("status", "")).lower() for x in (pl.get("subtasks") or []) if isinstance(x, dict)]
        if st:
            fin = sum(x in ("done", "completed", "partial", "failed", "skipped", "pass", "ok") for x in st)
            cur["progress"] = min(99, round(100 * fin / len(st)))
            cur["nsteps"] = len(st)
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
            page_seen()
            with _tlock:
                ts = [{k: t.get(k) for k in ("id", "title", "status", "created", "pinned")}
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
                                   "tasks": ts, "version": VERSION, "release": RELEASE, "updated": upd, "update_fail": fail,
                                   "machine": {"cpu": CPU, "ram": RAM_GB, "max": MAX_RUNNING}})
        if u.path == "/api/registry":
            return self.send(200, registry(force=q.get("force", [""])[0] == "1"))
        if u.path == "/api/task":
            t = TASKS.get(q.get("id", [""])[0])
            return self.send(200, task_detail(t) if t else {"error": "Không tìm thấy task"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path == "/api/bye" and self.ok_host():   # tab đóng (sendBeacon)
            LAST_SEEN["bye"] = time.time()
            return self.send(200, {"ok": True})
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
        if path == "/api/export-log":
            return export_log()
        if path == "/api/ai/refresh":
            return refresh_status()
        if path == "/api/update":
            return update_now()
        if path == "/api/pick":
            with _pick_lock:
                return {"path": pick_folder()}
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
            return new_task(d.get("text", ""), d.get("mode", "balanced"), d.get("project", ""), d.get("template", ""))
        if path == "/api/project":
            return project_api(d)
        if path == "/api/template":
            return template_api(d)
        if path == "/api/task/submit":
            return submit_task(d.get("id", ""), d.get("files", []))
        if path == "/api/task/act":
            return act(d.get("id", ""), d.get("action", ""), d.get("text", ""), d.get("files"))
        if path == "/api/task/file":
            return open_task_file(d.get("id", ""), d.get("path", ""))
        if path == "/api/quit":
            threading.Thread(target=shutdown, daemon=True).start()
            return {"ok": True}
        return {"error": "bad request"}


LAST_SEEN = {"t": time.time(), "bye": 0.0}


def page_watch():
    """Không còn tab nào mở (đóng tab) -> tạm dừng task đang làm; mở lại trang -> tự chạy tiếp."""
    while True:
        time.sleep(5)
        idle = time.time() - LAST_SEEN["t"]
        closed = LAST_SEEN["bye"] > LAST_SEEN["t"] and time.time() - LAST_SEEN["bye"] > 15
        if closed or idle > 180:
            with _tlock:
                for t in TASKS.values():
                    if t["status"] in ("running", "queued") and not t.get("auto_paused"):
                        t["auto_paused"] = True
                        if t["status"] == "running":
                            t["_pause"] = True
                            kill_tree(PROCS.get(t["id"]))
                        else:
                            t["status"] = "paused"
                            tsave(t)


def page_seen():
    LAST_SEEN["t"] = time.time()
    with _tlock:
        for t in TASKS.values():
            if t.get("auto_paused") and t["status"] == "paused":
                t.pop("auto_paused", None)
                t["status"] = "queued"
                t.setdefault("next", {"kind": "resume"})
                tsave(t)
            elif t.get("auto_paused") and t["status"] != "running":
                t.pop("auto_paused", None)


def shutdown():
    time.sleep(0.3)
    with _tlock:
        for tid, p in list(PROCS.items()):
            TASKS[tid]["_quit"] = True      # lần mở sau tự chạy tiếp
            kill_tree(p)
    time.sleep(0.8)
    os._exit(0)


UPDATED = {"pending": None, "done": ""}
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


def slim_task(t, deep=False):
    """Giảm dung lượng thư mục làm việc của 1 task (giữ lịch sử: tin nhắn, kế hoạch, tiêu chí, quyết định, log chữ).
    - Luôn: xóa file trong work/ trùng hệt file đã có ở thư mục kết quả (bản thật nằm ở đó).
    - deep (nút Xóa cache): xóa thêm bản nháp/ảnh/video trung gian trong work/ và file đính kèm đã dùng xong."""
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
    deep = deep and t.get("status") in ("done", "cancelled")   # task còn dở: chỉ xóa file trùng, giữ bản nháp để làm tiếp
    freed = 0
    for f in list(w.rglob("*")):
        if not f.is_file() or f.name == "status.json":
            continue
        try:
            sz = f.stat().st_size
            drop = False
            if f.suffix.lower() in KEEP_EXT and sz < 2_000_000:
                continue
            if sz in idx:
                h = _fhash(f)
                drop = any(_fhash(o) == h for o in idx[sz])
            if not drop and deep:
                drop = True
            if drop:
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
    """Có task đang làm/chờ/tạm dừng tự động (đóng tab) -> không cập nhật."""
    return any(t["status"] in ("running", "queued") or t.get("auto_paused") or t.get("_redirect") for t in TASKS.values())


def fetch_update():
    """Tải bản mới từ GitHub (chỉ code, không có dữ liệu). Trả về số phiên bản mới, '' nếu đang mới nhất."""
    import hashlib
    import urllib.request
    if not UPDATE_BASE or VERSION == "dev":
        return ""
    base = UPDATE_BASE
    try:      # lấy mã commit mới nhất qua GitHub API rồi tải theo đúng commit đó -> không dính bộ đệm 5 phút của GitHub
        m = re.match(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/", UPDATE_BASE)
        req = urllib.request.Request(f"https://api.github.com/repos/{m.group(1)}/commits/{m.group(2)}",
                                     headers={"Accept": "application/vnd.github.sha", "User-Agent": "ai-orchestrator"})
        sha = urllib.request.urlopen(req, timeout=15).read().decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            base = f"https://raw.githubusercontent.com/{m.group(1)}/{sha}/"
    except Exception:
        pass
    try:      # đọc latest.json qua API (không bị bộ đệm), lỗi thì đọc theo commit
        req = urllib.request.Request(f"https://api.github.com/repos/{m.group(1)}/contents/latest.json?ref={m.group(2)}",
                                     headers={"Accept": "application/vnd.github.raw", "User-Agent": "ai-orchestrator"})
        meta = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:
        meta = json.loads(urllib.request.urlopen(base + "latest.json", timeout=20).read())
    if meta.get("version", "") > VERSION:
        body = urllib.request.urlopen(base + "app.py", timeout=60).read()
        if meta["version"] in ((rjson(DATA / "boot.json", {}) or {}).get("bad_versions") or []):
            return ""                                  # bản này đã lỗi khi khởi động trên máy này -> bỏ qua
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
    threading.Thread(target=check_update, daemon=True).start()
    threading.Thread(target=page_watch, daemon=True).start()
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


def cmd_worker(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="worker")
    ap.add_argument("worker", choices=["codex", "gemini"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args(argv)
    proj = Path.cwd()
    task = Path(a.task).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out / f"v{n}").exists():
        n += 1
    v = out / f"v{n}"
    v.mkdir()
    st = {"worker": a.worker, "model": a.model, "out_dir": str(v)}
    wjson(v / "status.json", {**st, "status": "running"})
    exe = find_bin("codex" if a.worker == "codex" else "agy")
    if not task.exists():
        st.update(status="failed", error=f"Không thấy {task}")
    elif not exe:
        st.update(status="failed", error="Worker chưa cài")
    else:
        text = task.read_text(encoding="utf-8")
        (v / "TASK.md").write_text(text, encoding="utf-8")
        if a.worker == "codex":
            prompt = ("Thực hiện đúng yêu cầu trong TASK.md ở thư mục hiện tại. "
                      f"File đầu vào (chỉ đọc): {proj / 'input'}. Ghi mọi file kết quả vào thư mục hiện tại. "
                      "Cuối cùng liệt kê file đã tạo và cách đã kiểm tra.")
            cmd = [exe, "exec", "--skip-git-repo-check", *CODEX_SANDBOX, "-C", str(v),
                   "--output-last-message", str(v / "result.md")]
            if a.model:
                cmd += ["-m", a.model]
            cmd.append(prompt)
            cwd = v
        else:
            cmd = [exe, "-p", text, "--output-format", "json"]
            cwd = proj
        t0 = time.time()
        code, so, se, to = run_limited(cmd, cwd, a.timeout)
        st.update(seconds=round(time.time() - t0), exit_code=code)
        (v / "worker.log").write_text(f"--- stdout ---\n{so}\n--- stderr ---\n{se}\n", encoding="utf-8")
        if a.worker == "gemini" and so.strip():
            try:
                resp = json.loads(so).get("response") or so
            except Exception:
                resp = so
            (v / "result.md").write_text(resp, encoding="utf-8")
        res = v / "result.md"
        low = (so + se).lower()
        if to:
            st.update(status="timeout", error=f"Quá {a.timeout}s")
        elif any(k in low for k in ("usage limit", "rate limit", "quota", "resource_exhausted", "429")) and code != 0:
            st.update(status="limit", error="Worker hết hạn mức: " + (se or so).strip()[-300:])
        elif code != 0:
            st.update(status="failed", error=(se or so).strip()[-500:])
        elif not res.exists() or not res.read_text(encoding="utf-8").strip():
            st.update(status="failed", error="Worker không trả kết quả")
        else:
            st["status"] = "completed"
    st["files"] = sorted(str(f.relative_to(v)).replace("\\", "/") for f in v.rglob("*")
                         if f.is_file() and f.name not in ("TASK.md", "worker.log", "status.json"))
    wjson(v / "status.json", st)
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st["status"] == "completed" else 1)


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
    p = sp.add_parser("csv"); p.add_argument("--file", required=True); p.add_argument("--cols"); p.add_argument("--min-rows", type=int, default=1); p.add_argument("--sum", action="append")
    p = sp.add_parser("xlsx"); p.add_argument("--file", required=True); p.add_argument("--sheets"); p.add_argument("--min-rows", type=int, default=1)
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
        elif a.cmd == "csv":
            with open(a.file, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            cols = list(rows[0].keys()) if rows else []
            add("đọc CSV", True, f"{len(rows)} dòng, cột: {cols}")
            if a.cols:
                miss = [c for c in a.cols.split(",") if c not in cols]
                add("đủ cột", not miss, f"thiếu {miss}" if miss else "đủ")
            add("số dòng", len(rows) >= a.min_rows, f"{len(rows)} >= {a.min_rows}")
            for s in a.sum or []:
                col, val = s.split("=")
                got = 0.0
                for r in rows:
                    try:
                        got += float(str(r.get(col, "")).replace(",", ""))
                    except ValueError:
                        pass
                add(f"tổng {col}", abs(got - float(val)) <= max(0.01, abs(float(val)) * 1e-6), f"{got} (cần {val})")
        elif a.cmd == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(a.file, data_only=True)
            add("mở XLSX", True, f"sheets {wb.sheetnames}")
            if a.sheets:
                miss = [s for s in a.sheets.split(",") if s not in wb.sheetnames]
                add("đủ sheet", not miss, f"thiếu {miss}" if miss else "đủ")
            for ws in wb.worksheets:
                add(f"sheet {ws.title} có dữ liệu", ws.max_row - 1 >= a.min_rows, f"{ws.max_row - 1} dòng")
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
