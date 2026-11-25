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
    "fast": """- Ưu tiên THỜI GIAN. Chạy song song tối đa {n} worker/sub-agent cho các phần độc lập.
- Dùng model mạnh ngay từ đầu để ít phải sửa. Giám khảo: chấm 1 lần ở cuối, chỉ chấm lại phần đã sửa.""",
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

## Quy trình nghiệm thu (mọi task có sản phẩm: ảnh, code, bảng tính, tài liệu, báo cáo dạng file...)
1. TIÊU CHÍ: trước khi làm, viết tasks/criteria.md từ chính yêu cầu của người dùng trong task này: sản phẩm phải có gì, đúng
   gì, định dạng/kích thước, chất lượng/style, lỗi cần tránh; đánh dấu tiêu chí nào BẮT BUỘC. Ngắn gọn, kiểm chứng được.
   Người dùng bổ sung/đổi yêu cầu hoặc góp ý ở lượt sau -> cập nhật file. Không hỏi người dùng để duyệt tiêu chí.
2. LÀM THỬ: việc lặp theo lô (từ 3 sản phẩm cùng loại trở lên) -> làm 1 mẫu trước, chấm đạt mới chạy cả lô theo đúng cách đó.
3. CHẤM ĐỘC LẬP: bạn không tự nghiệm thu sản phẩm của mình hay của worker. Sau khi check kỹ thuật đạt, gọi giám khảo:
     "{py}" "{app}" judge --files <thư mục/file sản phẩm> --criteria tasks/criteria.md [--refs "<thư mục mẫu người dùng đã duyệt>"]
        --brief "<yêu cầu riêng của phần này>" --out work/J1 [--model <model>]
   Giám khảo là phiên Claude mới, chỉ thấy sản phẩm + tiêu chí. Kết quả: work/J1/vN/review.json (verdict, điểm, lỗi, cách sửa).
   Fail -> sửa/giao sửa đúng lỗi giám khảo nêu rồi chấm lại (cùng --out), tối đa 2 vòng; vẫn fail -> báo người dùng lỗi còn lại.
   Không bỏ qua kết quả giám khảo; nếu thấy giám khảo sai rõ ràng, nêu trong câu trả lời để người dùng quyết.
   Chọn model giám khảo theo độ khó: việc đơn giản dùng model nhỏ.
Bỏ qua cả quy trình này khi task không tạo sản phẩm (hỏi đáp, tra cứu nhanh, giải thích) hoặc người dùng bảo làm nhanh/nháp.

## Tạo ảnh
Tạo bằng codex ($imagegen) hoặc cách khác có sẵn. Chỉ khi không thể tạo được (lỗi, hết hạn mức): viết prompt vào
OUTPUT/prompts/, rồi hỏi người dùng (question.md): "Dán prompt trong <đường dẫn> vào ChatGPT, lưu ảnh vào OUTPUT/images/
rồi trả lời 'xong'". Khi được trả lời: check + chấm độc lập như trên.

## Chế độ làm việc người dùng chọn cho task này: {mode_name}
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
Cập nhật tasks/plan.json sau mỗi bước: goal, subtasks[id, worker, model, status, attempt, outputs].
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
    fl = f"\nFile mới trong ./input/: {', '.join(files)}." if files else ""
    text = nxt.get("text", "")
    if kind == "start" or not t.get("session_started"):
        base = f"Task mới. Yêu cầu của người dùng:\n\n{t['text']}\n"
        if kind != "start" and text:
            base += f"\nBổ sung của người dùng:\n{text}\n"
        return base + fl + "\nLàm theo CLAUDE.md."
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
    if t.get("mode") == "saver":
        args += ["--model", "sonnet"]
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


def new_files(t, since):
    od = out_dir(t)
    if not od.exists():
        return []
    fs = [f for f in od.rglob("*") if f.is_file() and ".work" not in f.parts and f.stat().st_mtime >= since - 1]
    fs.sort(key=str)
    return [str(f.relative_to(od)).replace("\\", "/") for f in fs][:30]


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
            tmsg(t, "claude", q.read_text(encoding="utf-8").strip(), kind="question", files=new_files(t, since))
        elif p.returncode == 0 and result and not result.get("is_error"):
            end_work(t, "done")
            t["status"] = "done"
            t["finished"] = now()
            tmsg(t, "claude", (result.get("result") or "Xong.").strip(), files=new_files(t, since))
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
        elif action == "rename" and text:
            t["title"] = text[:80]
            t["renamed"] = True
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


def new_task(text, mode="balanced"):
    if not text.strip():
        return {"error": "Chưa nhập yêu cầu"}
    tid = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    while tid in TASKS:
        tid += "b"
    t = {"id": tid, "title": re.sub(r"\s+", " ", text.strip())[:50], "text": text.strip(),
         "status": "draft", "created": now(), "created_ts": time.time(), "messages": [],
         "mode": mode if mode in MODES else "balanced", "w2": True}
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
        return {"error": "Chưa cài. Chạy lại file cài AI-Orchestrator (.bat)."}
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
                        subprocess.run(["cmd.exe", "/c", "start", "/wait", "AI Orchestrator - Gemini", exe],
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
                ts = [{k: t.get(k) for k in ("id", "title", "status", "created")}
                      for t in TASKS.values() if t["status"] != "draft"]
                for x in ts:
                    if TASKS[x["id"]].get("need_login") and x["status"] == "queued":
                        x["status"] = "login"
            ts.sort(key=lambda x: x["created"], reverse=True)
            upd = CFG.pop("updated_to", "")
            if upd:
                save_cfg()
            return self.send(200, {"ais": ai_state(), "output": CFG["output"], "instructions": CFG["instructions"],
                                   "tasks": ts, "version": VERSION, "release": RELEASE, "updated": upd,
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
            return new_task(d.get("text", ""), d.get("mode", "balanced"))
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
    name = "AI-Orchestrator-log-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
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
    meta = json.loads(urllib.request.urlopen(base + "latest.json", timeout=20).read())
    if meta.get("version", "") > VERSION:
        body = urllib.request.urlopen(base + "app.py", timeout=60).read()
        if hashlib.sha256(body).hexdigest() == meta.get("sha256"):
            (HOME / "app.py.new").write_bytes(body)
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
    if busy_tasks():
        return {"ok": True, "msg": f"Có bản v{UPDATED.get('release') or new}. Sẽ tự cập nhật khi các task làm xong."}
    threading.Thread(target=lambda: (time.sleep(0.5), apply_update()), daemon=True).start()
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
    new = HOME / "app.py.new"
    if not new.exists():
        return
    os.replace(new, APP)
    (HOME / "version.txt").write_text(UPDATED["pending"], encoding="ascii")
    CFG["updated_to"] = UPDATED["pending"]
    save_cfg()
    env = dict(os.environ, AIO_RESTART="1")
    subprocess.Popen([sys.executable, str(APP), "--background"], cwd=str(HOME), env=env,
                     creationflags=NO_WINDOW)
    os._exit(0)


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
    load_tasks()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=check_startup, daemon=True).start()
    threading.Thread(target=check_update, daemon=True).start()
    threading.Thread(target=page_watch, daemon=True).start()
    if not background and not os.environ.get("AIO_NO_BROWSER"):
        webbrowser.open(url)
    print("AI Orchestrator:", url, flush=True)
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
        serve(background="--background" in sys.argv)
