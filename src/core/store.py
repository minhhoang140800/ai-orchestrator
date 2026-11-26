# Maestro — core/store.py: Đọc/ghi JSON an toàn, lưu/tải task (task.json), thư mục nháp và kết quả.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def rjson(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def wjson(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    # file tạm riêng cho từng tiến trình/luồng: nhiều tiến trình cùng ghi không giẫm lên file tạm của nhau
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    for i in range(5):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:              # Windows: file đích đang được tiến trình khác mở -> thử lại
            if i == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def safe_name(s, n=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s).strip(" .")
    return re.sub(r"\s+", " ", s)[:n].strip(" .") or "Untitled"


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


def tsave(t):
    with _tlock:
        wjson(tdir(t) / "task.json", {k: v for k, v in t.items() if not k.startswith("_")})


def tmsg(t, role, text, **kw):
    t.setdefault("messages", []).append({"role": role, "text": text, "time": now(), **kw})
    return len(t["messages"]) - 1


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
