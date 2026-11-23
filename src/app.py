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
MODES = {"fast": "Nhanh", "balanced": "Cân bằng", "saver": "Tiết kiệm"}
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
            with open(tdir(t) / "timeline.log", "a", encoding="utf-8") as f:
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
    w = tdir(t) / "work"
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
- Thư mục làm việc (nháp, log): thư mục hiện tại
- File người dùng gửi: ./input/
- Được ĐỌC mọi ổ đĩa của máy: {', '.join(drives())}. Người dùng nhắc tới file/thư mục nào thì tự tìm.
  Không sửa, không xóa, không di chuyển file nào ngoài thư mục làm việc và OUTPUT.
- OUTPUT (nơi lưu sản phẩm giao người dùng): {out_dir(t)}
- Sản phẩm của các task trước (để dùng lại, tránh làm lại): danh mục ở {DATA / "registry.json"}

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
    (tdir(t) / "CLAUDE.md").write_text(txt, encoding="utf-8")


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
    fs = [f for f in od.rglob("*") if f.is_file() and f.stat().st_mtime >= since - 1]
    fs.sort(key=str)
    return [str(f.relative_to(od)).replace("\\", "/") for f in fs][:30]


def watch(t, p, nxt):
    raw = open(tdir(t) / "run.log", "a", encoding="utf-8")
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
        q = tdir(t) / "question.md"
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
         "mode": mode if mode in MODES else "balanced"}
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

    def __init__(self, args, cwd):
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
            self.p = PtyProcess.spawn([str(x) for x in args], cwd=cwd, dimensions=(50, 500))
            self.pid = self.p.pid
            self._read = lambda: self.p.read(4096)
            self._write = self.p.write
            self.alive = self.p.isalive
        else:
            import pty
            m, s = pty.openpty()
            self.sp = subprocess.Popen(args, cwd=cwd, stdin=s, stdout=s, stderr=s, close_fds=True, start_new_session=True)
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
                p = subprocess.Popen(args, cwd=str(d), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)   # CLI tự mở trình duyệt
                LOGINS[ai] = p
                t0 = time.time()
                while time.time() - t0 < 600:
                    time.sleep(2)
                    if p.poll() is not None or (time.time() - t0 > 6 and ai_status(ai).get("ok")):
                        break
                time.sleep(1)
                kill_tree(p)
                ai_status(ai)
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
        m["subtasks"] = []
    for s in subs:   # gắn sub-task vào lượt làm việc chứa thời điểm của nó
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
    dec = rjson(tdir(t) / "tasks" / "decisions.json", [])
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
            CFG["output"] = o
            save_cfg()
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
                if f.is_file():
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
        wjson(DATA / "registry.json", {"output_root": CFG.get("output", ""), "tasks": [
            {"title": x["title"], "folder": x["folder"], "created": x["created"], "status": x["status"],
             "files": [a["path"] for a in arts if a["task"] == x["id"]][:200]} for x in tasks]})
    except Exception:
        pass
    return _reg["data"]


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
            d = tdir(t)
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
            for f in [d / "task.json", d / "timeline.log", d / "run.log", d / "tasks" / "plan.json", d / "CLAUDE.md"]:
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
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
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
