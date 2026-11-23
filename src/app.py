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
LOW_SPEC = (os.cpu_count() or 4) <= 4          # máy yếu (vd i3 2 nhân 4 luồng)
MAX_RUNNING = 1 if LOW_SPEC else 2
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


def write_claude_md(t):
    py, app = py_console(), str(APP)
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

## Tối ưu chất lượng, thời gian, hạn mức
- Model nhỏ/nhanh cho việc đơn giản; model mạnh cho việc khó. Áp dụng cho subagent và worker (--model).
  Nâng model khi kết quả không đạt, hạ khi việc lặp lại.
- {"Máy này cấu hình thấp: chỉ chạy 1 worker/sub-agent tại một thời điểm, không chạy song song." if LOW_SPEC else "Song song sub-task độc lập; tuần tự khi phụ thuộc."}
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


def new_task(text):
    if not text.strip():
        return {"error": "Chưa nhập yêu cầu"}
    tid = datetime.datetime.now().strftime("%y%m%d-%H%M%S")
    while tid in TASKS:
        tid += "b"
    t = {"id": tid, "title": re.sub(r"\s+", " ", text.strip())[:50], "text": text.strip(),
         "status": "draft", "created": now(), "created_ts": time.time(), "messages": []}
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


_status_at = {"t": time.time()}


def status_loop():
    """Cập nhật trạng thái Claude/ChatGPT định kỳ khi trang đang mở."""
    while True:
        # chỉ kiểm tra khi trang đang mở; thưa (5 phút) để không tốn máy — sau đăng nhập đã có kiểm tra riêng
        if time.time() - LAST_SEEN["t"] < 60 and time.time() - _status_at["t"] > 300:
            _status_at["t"] = time.time()
            for ai in ("claude", "codex"):
                if not CFG["ais"].get(ai, {}).get("pending"):
                    with _status_lock:
                        before = dict(CFG["ais"].get(ai, {}))
                        ai_status(ai)
                        if before != CFG["ais"].get(ai):
                            save_cfg()
        time.sleep(15)


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
    for ai in ("claude", "codex"):
        ai_status(ai)
    g = CFG["ais"].setdefault("gemini", {})
    if g.get("ok") and not gemini_has_cred():
        g.update(ok=False, msg="")
    save_cfg()


# ---------------------------------------------------------------- HTTP
def ui_html():
    if UI_B64:
        return base64.b64decode(UI_B64)
    return (APP.parent / "ui.html").read_bytes()


def task_detail(t):
    d = {k: t.get(k) for k in ("id", "title", "status", "created", "finished")}
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
                                   "tasks": ts, "version": VERSION, "release": RELEASE, "updated": upd})
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
            return new_task(d.get("text", ""))
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
    threading.Thread(target=status_loop, daemon=True).start()
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
