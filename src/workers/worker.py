# Maestro — workers/worker.py: Lệnh con `worker` / `wait` (Claude gọi): giao việc, chuyển worker 2 chiều, ảnh.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

# Codex: lệnh ghi của kết nối (Google Drive...) cần người duyệt; worker chạy nền không ai duyệt -> bị chặn.
# Chỉ bật tự duyệt khi task cần sửa file Google (cờ --drive / tin nhắn có link Google).
DRIVE_APPROVE = ["-c", 'apps._default.default_tools_approval_mode="approve"']
GOOGLE_LINK = re.compile(r"https?://(?:docs|drive|sheets)\.google\.com/", re.I)


IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


IMG_MAKE = re.compile(r"vẽ|tạo (?:ảnh|hình|asset|icon|banner|keyframe|storyboard|sprite|logo|thumbnail)|render|imagegen|keyframe|"
                      r"sprite|\.png|\.jpe?g|\.webp|minh ho[ạa]|thumbnail|poster|banner|artwork|background|\bbg\b|logo|asset", re.I)
IMG_FAIL_F = "img_fail.json"
IMG_FAIL_TTL = 30 * 60          # 1 bên không vẽ được: 30 phút tới giao thẳng bên kia (chỉ thử 1 lần)


# Gemini CLI (Antigravity) hết lượt vẽ ảnh: vẫn báo SUCCESS, lỗi nằm trong câu trả lời / log. Hạn mức vẽ của CLI tách khỏi
# Gemini web/app. Thông báo có giờ mở lại ("Your quota will reset after 3h20m14s").
IMG_QUOTA_RE = re.compile(r"image generation quota exceeded|exhausted your capacity on this model", re.I)
IMG_RESET_RE = re.compile(r"reset after\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)


def img_quota_until(text):
    """Thông báo hết lượt vẽ ảnh -> giờ mở lại (timestamp); không phải lỗi hết lượt vẽ -> 0."""
    if not IMG_QUOTA_RE.search(text or ""):
        return 0
    m = IMG_RESET_RE.search(text)
    secs = sum(int(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1))) if m else 0
    return int(time.time() + (secs if secs > 0 else 3 * 3600))      # không đọc được giờ -> ước 3 giờ


def img_failed_recent(worker):
    d = rjson(DATA / IMG_FAIL_F, {}) or {}
    if d.get(worker + "_until"):                                      # biết giờ mở lại (hết lượt vẽ)
        return time.time() < float(d[worker + "_until"])
    return time.time() - float(d.get(worker, 0)) < IMG_FAIL_TTL


def mark_img_fail(worker, until=0):
    """Bên này không vẽ được: nhớ tới giờ mở lại (nếu biết), không thì 30 phút; tới đó việc vẽ giao thẳng bên kia."""
    d = rjson(DATA / IMG_FAIL_F, {}) or {}
    d[worker] = int(time.time())
    if until:
        d[worker + "_until"] = int(until)
    else:
        d.pop(worker + "_until", None)
    wjson(DATA / IMG_FAIL_F, d)


def img_block_until(worker):
    """Giờ mở lại vẽ ảnh của 1 bên (để hiện trong Cài đặt); 0 = đang vẽ được / không rõ."""
    u = float((rjson(DATA / IMG_FAIL_F, {}) or {}).get(worker + "_until") or 0)
    return int(u) if u > time.time() else 0


CODE_DRAWN = "_ve_bang_code"      # ảnh Gemini tự vẽ bằng code sau khi hết lượt vẽ AI (không tính là ảnh đạt)


def _imgs(d):
    return [f for f in d.rglob("*") if f.suffix.lower() in IMG_EXT and "_backup" not in f.parts and CODE_DRAWN not in f.parts]


AGY_LOG_LINE = re.compile(r"^E(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.\d+\s+\d+ generate_image\.go:\d+\] error generating image: (.*)$", re.M)


def agy_img_error(t0):
    """Log Gemini CLI của lần chạy này (tạo sau t0): lần đầu báo lỗi vẽ ảnh -> (thời điểm lỗi, giờ mở lại); không có -> (0, 0).
    Gemini hết lượt vẽ vẫn báo SUCCESS và có khi tự vẽ bằng code (Python/PIL) mà không nói -> phải đọc log mới biết."""
    d = Path.home() / ".gemini" / "antigravity-cli" / "log"
    try:
        logs = sorted((f for f in d.glob("cli-*.log") if f.stat().st_mtime >= t0 - 5), key=lambda f: f.stat().st_mtime)
    except Exception:
        return 0, 0
    yr = datetime.datetime.now().year
    for f in logs:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in AGY_LOG_LINE.finditer(txt):
            mo, dd, hh, mi, ss, msg = m.groups()
            try:
                at = datetime.datetime(yr, int(mo), int(dd), int(hh), int(mi), int(ss)).timestamp()
            except ValueError:
                continue
            if at >= t0 - 5:
                return at, img_quota_until(msg) or int(time.time() + 3 * 3600)
    return 0, 0


def set_aside_code_drawn(v, since):
    """Ảnh tạo SAU lúc hết lượt vẽ AI = Gemini tự vẽ bằng code -> chuyển vào _ve_bang_code (giữ lại để xem, không tính đạt)."""
    moved = []
    for f in _imgs(v):
        try:
            if f.stat().st_mtime >= since - 1:
                dst = v / CODE_DRAWN / f.relative_to(v)
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(f, dst)
                moved.append(str(f.relative_to(v)).replace("\\", "/"))
        except Exception:
            pass
    return moved


def with_criteria(text, proj):
    """Gửi kèm tiêu chí nghiệm thu (tasks/criteria.md) cho worker ngay từ đầu để tự kiểm trước khi nộp -> ít bị chấm trượt."""
    f = Path(proj) / "tasks" / "criteria.md"
    if os.environ.get("AIO_WATCH") == "0" or not f.is_file() or "TIÊU CHÍ NGHIỆM THU" in text:
        return text                                # Giám sát tắt: đề bài gọn, không gắn tiêu chí (như v1.7)
    return (text + "\n\n---\nTIÊU CHÍ NGHIỆM THU (giám khảo sẽ chấm đúng theo đây — tự kiểm tra từng mục BẮT BUỘC trước khi nộp,"
            " chỉ sửa trong phạm vi việc của bạn):\n" + f.read_text(encoding="utf-8", errors="replace")[:6000])


IMG_QUICK = (" Ảnh: tạo xong là trả lời ngay; KHÔNG tự đo, so từng chi tiết hay kiểm tra lại kỹ (Maestro và Claude kiểm tra "
             "sau) — chỉ sửa khi ảnh hỏng rõ ràng.")
WATCH_IDLE = {"image": 480, "edit": 360, "research": 300, "text": 300}   # GPT không in thêm gì quá lâu -> coi là kẹt (giây)


def _ftail(f, n=4000):
    try:
        f.flush()
        size = os.fstat(f.fileno()).st_size
        f.seek(max(0, size - n))
        return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def run_watched(cmd, cwd, timeout, worker, kind, cap, task_txt):
    """Chạy worker và THEO DÕI trong lúc chạy (thay vì đợi chạy xong mới biết):
    - in ra thông báo hết lượt / hết lượt vẽ ảnh -> dừng ngay (app giao bên kia);
    - GPT (in nhật ký liên tục) im lặng quá WATCH_IDLE -> kẹt -> dừng;
    - chạy quá cap (4 lần thời gian thường thấy của máy này) -> chậm bất thường -> dừng.
    Trả (exit code, stdout, stderr, hết giờ?, lý do dừng sớm: ''|'limit'|'idle'|'slow'|'timeout')."""
    import tempfile
    fo, fe = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, creationflags=NO_WINDOW)
    t0 = last = time.time()
    size, why = 0, ""
    own_limit = is_limit(task_txt) or bool(img_quota_until(task_txt))     # đề bài tự nói về "limit" -> không bắt nhầm
    while p.poll() is None:
        time.sleep(3)
        now = time.time()
        try:
            n = os.fstat(fo.fileno()).st_size + os.fstat(fe.fileno()).st_size
        except Exception:
            n = size
        if n != size:
            size, last = n, now
            tail = _ftail(fe) + "\n" + _ftail(fo)
            if not own_limit and (is_limit(tail) or (kind == "image" and img_quota_until(tail))):
                why = "limit"
        if not why and now - t0 > timeout:
            why = "timeout"
        elif not why and now - t0 > cap:
            why = "slow"
        elif not why and worker == "codex" and now - last > WATCH_IDLE.get(kind, 300):
            why = "idle"
        if why:
            kill_tree(p)
            try:
                p.wait(15)
            except Exception:
                pass
            break
    code = p.returncode if p.returncode is not None else -1
    out = []
    for f in (fo, fe):
        try:
            f.seek(0)
            out.append(f.read().decode("utf-8", "replace"))
        except Exception:
            out.append("")
        f.close()
    return (code if not why else -1), out[0], out[1], why == "timeout", why


def _log_tail(f, n=12):
    """n dòng cuối có nội dung của 1 nhật ký worker (chỉ đọc 64 KB cuối)."""
    try:
        with open(f, "rb") as fh:
            fh.seek(max(0, f.stat().st_size - 65536))
            ls = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip() and not x.startswith("--- ")]
        out = []
        for x in ls[-n:]:
            if x.lstrip().startswith("{"):              # dòng JSON (\u0111...) -> chữ đọc được
                try:
                    x = json.dumps(json.loads(x), ensure_ascii=False)
                except Exception:
                    pass
            out.append(x[:300])
        return "\n".join(out)
    except Exception:
        return ""


def fail_reason(st, v, want_img):
    """Lý do worker lỗi / phải chuyển worker / báo xong mà không ra ảnh. Lưu vào status.json -> lịch sử task
    (còn lại sau khi nháp bị dọn), hiện ở Activity và Export log để biết vì sao, không phải đoán."""
    parts = [f"{n}: {st[k]}" for k, n in (("gemini_error", "Gemini"), ("codex_error", "GPT"), ("agy_error", "Antigravity"))
             if st.get(k)]
    if want_img and st.get("status") == "completed" and not _imgs(v) and not any(
            str(x).lower().endswith(IMG_EXT) for x in st.get("edited") or []) and not st.get("via"):
        parts.append("Báo xong nhưng không tạo ra file ảnh")
    if st.get("status") not in ("completed", "running", None) and st.get("error"):
        parts.append("Lỗi: " + str(st["error"]))
    if not parts:
        return ""
    tails = [f"[{'' if lf.parent == v else lf.parent.name + ' '}nhật ký]\n{tx}" for lf in [v / "worker.log"] + sorted(v.glob("*/worker.log"))
             if lf.is_file() and (tx := _log_tail(lf))]
    return ("\n".join(dict.fromkeys(parts)) + ("\n\n" + "\n\n".join(tails) if tails else ""))[:2000]


def worker_fallback(st, v, a, edit, want_img):
    """Worker lỗi / hết lượt / báo xong mà không ra ảnh -> app tự giao worker kia làm lại NGAY trong cùng lệnh
    (không quay về Claude). Gemini -> GPT cho mọi việc; GPT -> Gemini chỉ cho việc vẽ ảnh.
    Bên nào không vẽ được ảnh -> nhớ 30 phút, việc vẽ sau giao thẳng bên kia (chỉ thử 1 lần)."""
    src = a.worker
    dst = "codex" if src == "gemini" else "gemini"
    no_img = want_img and st.get("status") == "completed" and not _imgs(v) and not any(
        str(x).lower().endswith(IMG_EXT) for x in st.get("edited") or [])
    failed = st.get("status") in ("limit", "failed", "timeout")
    quota = int(st.get("img_quota_until") or 0)            # hết lượt vẽ giữa chừng (Gemini CLI)
    partial = want_img and quota and not no_img and not failed and bool(_imgs(v))   # vẽ dở: có vài ảnh rồi hết lượt
    if want_img and (no_img or failed or partial) and "giao thẳng" not in st.get("error", ""):
        mark_img_fail(src, quota)                # thật sự đã thử mà không vẽ được (không tính lần bỏ qua)
    if not (no_img or failed or partial) or (src == "codex" and not want_img):
        return
    cfgd = rjson(CFG_FILE, {}) or {}
    ok = ((cfgd.get("ais") or {}).get(dst) or {}).get("ok") is not False
    if not find_bin("codex" if dst == "codex" else "agy") or not ok or usage_block(dst, fresh=dst == "codex") \
            or (want_img and img_failed_recent(dst) and st.get("error", "").find("giao thẳng") < 0):
        return                                   # bên kia không dùng được / cũng vừa không vẽ được -> để Claude quyết
    gv = v / ("gpt" if dst == "codex" else "gemini")
    gv.mkdir(parents=True, exist_ok=True)
    wjson(v / "status.json", {**{k: x for k, x in st.items() if not k.startswith("_")}, "status": "running", "now_worker": dst,
                              "pid": os.getpid()})       # giao diện hiện đúng bên đang làm thật
    task_f = v / "TASK.md"
    if partial:                                  # vẽ dở -> bên kia chỉ vẽ NỐT phần còn thiếu, giữ cùng phong cách
        have = [str(f.relative_to(v)).replace("\\", "/") for f in _imgs(v)]
        task_f = gv.parent / "TASK_vẽ_nốt.md"
        task_f.write_text((v / "TASK.md").read_text(encoding="utf-8", errors="replace") + (
            "\n\n---\nPHẦN CÒN THIẾU: worker trước đã vẽ được " + str(len(have)) + " ảnh rồi hết lượt. Ảnh đã có (trong "
            + str(v) + "): " + ", ".join(have) + ".\nChỉ vẽ NỐT những ảnh còn thiếu theo đề bài trên; xem ảnh đã có để giữ cùng "
            "phong cách, kích thước, cách đặt tên; KHÔNG vẽ lại ảnh đã có. Cuối câu trả lời ghi rõ đã vẽ thêm ảnh nào."
            + (" Ảnh trong thư mục " + CODE_DRAWN + " do worker trước tự vẽ bằng code sau khi hết lượt (không đạt): vẽ lại "
               "bằng AI." if st.get("code_drawn") else "")), encoding="utf-8")
    cmd = [sys.executable, str(APP), "worker", dst, "--task", str(task_f), "--out", str(v / (gv.name + "_out")),
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
    who = "Gemini" if src == "gemini" else "GPT"
    st[src + "_error"] = (f"{who} vẽ được {len(_imgs(v)) - len(_imgs(gv))} ảnh rồi hết lượt vẽ; bên kia vẽ nốt phần còn thiếu"
                          if partial else f"{who} không tạo ra ảnh" if no_img else st.get("error", ""))[:300]
    if st.get("code_drawn"):
        st[src + "_error"] += f"; {len(st['code_drawn'])} ảnh {who} tự vẽ bằng code đã chuyển vào {CODE_DRAWN}"
    if quota:
        st[src + "_error"] += " (" + src + " vẽ lại được lúc " + datetime.datetime.fromtimestamp(quota).strftime("%H:%M") + ")"
    tag = "gpt" if dst == "codex" else "gemini"
    st.update(status=gs.get("status") or "failed", model=f"{tag}:" + str(gs.get("model") or "?"), via=f"{tag}-fallback",
              error=gs.get("error", ""), seconds=(st.get("seconds") or 0) + (gs.get("seconds") or 0))
    if gs.get("edited"):
        st["edited"] = gs["edited"]


EXPORT_SKIP_NAMES = {"TASK.md", "worker.log", "status.json", "result.md", "review.json", "prompt.md", "last.txt"}
EXPORT_SKIP_DIRS = {"_backup", CODE_DRAWN, "gpt_out", "gemini_out"}


def export_group(out, group=""):
    """Tên nhóm thư mục kết quả: Claude đặt bằng --group (vd icons, screenshots); không có thì đoán theo tên việc."""
    g = (group or "").strip()
    if not g:
        n = out.name.lower()
        g = ("icons" if re.search(r"icon", n) else "screenshots" if re.search(r"(^|[_-])ss\d|screenshot|banner", n)
             else re.sub(r"([_-]?v\d+|[_-]?\d+)+$", "", out.name) or out.name)
    return safe_name(g, 40)


def export_products(st, v, out, proj, group=""):
    """File sản phẩm của việc vừa xong -> chuyển THẲNG ra thư mục kết quả của task, gom theo nhóm: <OUTPUT>/<YYYYMMDD>-<nhóm>
    (vd icons, screenshots; mọi việc cùng nhóm chung 1 thư mục, không mỗi việc/mỗi lần sửa 1 thư mục).
    Lần sửa (vN>1) của cùng việc: bản mới giữ tên file, bản trước chuyển vào <nhóm>/_ban_cu/ (không ghi đè, không mất).
    Sổ sách (TASK.md, log, status.json, result.md) ở lại nháp .work, tự dọn khi task xong. Chỉ khi chạy trong task (.work),
    không áp dụng --edit."""
    if proj.name != ".work" or st.get("status") != "completed" or st.get("edit_dir"):
        return
    files = [f for f in v.rglob("*") if f.is_file() and f.name not in EXPORT_SKIP_NAMES and not f.name.startswith("TASK")
             and not (set(f.relative_to(v).parts[:-1]) & EXPORT_SKIP_DIRS)]
    if not files:
        return
    n = int(v.name[1:]) if re.fullmatch(r"v\d+", v.name) else 1
    g = export_group(out, group)
    old = sorted((d for d in proj.parent.glob("*-" + g) if d.is_dir() and re.fullmatch(r"\d{8}-" + re.escape(g), d.name)), reverse=True)
    dst = old[0] if old else proj.parent / (datetime.date.today().strftime("%Y%m%d") + "-" + g)   # nhóm đã có (hôm trước) -> dùng lại
    dst.mkdir(parents=True, exist_ok=True)
    mine = set()                                       # file của chính việc này ở các lần trước (được thay bằng bản sửa)
    for pv in out.glob("v*/status.json"):
        ps = rjson(pv, {}) or {}
        if pv.parent != v and ps.get("export_dir") and Path(ps["export_dir"]) == dst:
            mine |= set(ps.get("files") or [])
    moved = []
    for f in files:
        rel = f.relative_to(v)
        if rel.parts[0] in ("gpt", "gemini"):          # ảnh bên làm thay: đưa ra cùng cấp (không để thư mục gpt/ gemini/)
            rel = Path(*rel.parts[1:])
        rp = lambda p: str(p.relative_to(dst)).replace("\\", "/")
        t = dst / rel
        if t.exists() and rp(t) not in mine:            # trùng tên với việc khác: thêm tên việc (giữ cả hai)
            t = t.with_name(f"{safe_name(out.name, 40)}_{t.name}")
        if t.exists() and rp(t) in mine:                # bản sửa của chính việc này: bản trước vào _ban_cu, bản mới giữ tên
            k = dst / "_ban_cu" / f"{t.stem}_v{n - 1}{t.suffix}"
            k.parent.mkdir(parents=True, exist_ok=True)
            i = 2
            while k.exists():
                k = k.with_name(f"{t.stem}_v{n - 1}_{i}{t.suffix}")
                i += 1
            try:
                shutil.move(str(t), str(k))
            except Exception as e:
                dlog(f"export_products old {t}: {e}")
        i, base = 2, t
        while t.exists():                               # vẫn trùng: đánh số, không ghi đè
            t = base.with_name(f"{base.stem}_{i}{base.suffix}")
            i += 1
        t.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(f), str(t))
            moved.append(str(t.relative_to(dst)).replace("\\", "/"))
        except Exception as e:
            dlog(f"export_products {f}: {e}")
    if moved:
        st["export_dir"] = str(dst)
        st["files"] = sorted(moved)


def auto_judge(st, v, out, proj, want_img):
    """Giám sát bật (có tasks/criteria.md) + việc vẽ vừa xong: giám khảo chấm NGAY các file của việc này, trượt thì app tự giao
    lại worker sửa (tối đa 1 vòng; vẫn trượt thì giữ bản tốt hơn) -> các việc khác vẫn chạy song song, không đợi cả lô xong mới chấm rồi làm lại."""
    crit = proj / "tasks" / "criteria.md"
    if (os.environ.get("AIO_NOJUDGE") or not want_img or st.get("status") != "completed" or not st.get("export_dir")
            or not crit.is_file()):
        return
    brief = next((x.strip().lstrip("#").strip() for x in (v / "TASK.md").read_text(encoding="utf-8", errors="replace").splitlines()
                  if x.strip()), "(không)") if (v / "TASK.md").is_file() else "(không)"
    cmd = [sys.executable, str(APP), "judge", "--files", *([str(Path(st["export_dir"]) / x) for x in st.get("files") or []] or [st["export_dir"]]), "--criteria", str(crit), "--brief", brief[:300],
           "--out", str(out.parent / (out.name + "_cham")), "--rounds", "1"]
    try:
        p = subprocess.run(cmd, cwd=str(proj), env=dict(os.environ, AIO_NOJUDGE="1"), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, creationflags=NO_WINDOW)
        r = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        dlog(f"auto_judge {v}: {e}")
        return
    rv = r.get("review") or {}
    st["judge"] = {"overall": rv.get("overall") or r.get("status"), "summary": rv.get("summary") or r.get("error", ""),
                   "review": str(Path(r.get("out_dir", "")) / "review.json"),
                   "fixed": [x for fx in rv.get("auto_fix") or [] for vv in fx.get("files", {}).values() for x in vv],
                   "issues": {Path(str(i.get("file", ""))).name: i.get("issues") for i in rv.get("images") or []
                              if i.get("verdict") != "pass"}}


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
    ap.add_argument("--group", default="", help="nhóm thư mục kết quả (vd icons, screenshots): mọi việc cùng nhóm chung 1 thư mục")
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
    keep, skip = [], False                          # lệnh giao việc gốc (để giám khảo/app giao lại đúng như cũ khi cần sửa)
    for x in argv:
        if skip:
            skip = False
        elif x == "--vdir":
            skip = True
        elif x != "--bg":
            keep.append(x)
    st = {"worker": a.worker, "model": a.model, "out_dir": str(v), "args": keep}
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
        return wait_status(v, a.timeout + 180 + (1800 if (proj / "tasks" / "criteria.md").is_file() else 0))   # + thời gian tự chấm/sửa
    wjson(v / "status.json", {**st, "status": "running", "pid": os.getpid()})   # Hủy task -> dừng được worker này
    if not os.environ.get("AIO_NOFALLBACK"):   # bên làm thay (gọi lồng từ worker đang giữ chỗ) dùng chung chỗ đó
        st["_slot"] = str(worker_slot() or "")  # đủ số worker đang chạy trên máy -> chờ tới lượt
    exe = find_bin("codex" if a.worker == "codex" else "agy")
    task_txt = task.read_text(encoding="utf-8") if task.exists() else ""
    want_img = (a.worker == "codex" or a.write or bool(edit)) and bool(IMG_MAKE.search(task_txt))
    kind = task_kind(want_img, edit, a.drive, task_txt)
    skip_gem = want_img and img_failed_recent(a.worker)       # bên này vừa không vẽ được (30 phút) -> giao thẳng bên kia
    poor = want_img and not skip_gem and stat_poor(a.worker, "image")   # số liệu: bên này gần đây hầu như không vẽ được
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
    elif (skip_gem or poor) and not os.environ.get("AIO_NOFALLBACK"):
        (v / "TASK.md").write_text(task_txt, encoding="utf-8")
        bu = img_block_until(a.worker) if skip_gem else 0
        s_ = stat_of(a.worker, "image") if poor else None
        st.update(status="failed", error=("Gemini" if a.worker == "gemini" else "GPT") + (
            f" gần đây chỉ vẽ được {round(s_['rate'] * s_['n'])}/{s_['n']} lần" if s_ else
            " hết lượt vẽ ảnh tới " + datetime.datetime.fromtimestamp(bu).strftime("%H:%M") if bu else
            " vừa không tạo được ảnh (30 phút qua)") + " — giao thẳng bên kia")
    elif not exe and not use_api:
        st.update(status="failed", error="Worker chưa cài")
    elif use_api:
        text = with_criteria(task.read_text(encoding="utf-8"), proj)
        (v / "TASK.md").write_text(text, encoding="utf-8")
        api_worker(st, v, key, text, a)
    else:
        text = with_criteria(task.read_text(encoding="utf-8"), proj)
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
            short = want_img and not edit and not a.drive and os.environ.get("AIO_WATCH") == "0"
            prompt = ((f"Thực hiện đúng yêu cầu trong {v / 'TASK.md'}. File đầu vào (chỉ đọc): {inp_}. "    # vẽ ảnh, Giám sát tắt:
                       "Ghi mọi file kết quả vào thư mục hiện tại. Cuối cùng liệt kê file đã tạo.") if short else   # đề bài ngắn như v1.7
                      drive_note + f"Thực hiện đúng yêu cầu trong {v / 'TASK.md'}. "
                      f"File đầu vào (chỉ đọc): {inp_}. " + (edit_note or "Ghi mọi file kết quả vào thư mục hiện tại. ") +
                      "Câu trả lời cuối: nếu sản phẩm là văn bản thì ghi ĐẦY ĐỦ nội dung sản phẩm (không chỉ tóm tắt); "
                      "nếu tạo file thì liệt kê file đã tạo và cách đã kiểm tra." + IMG_QUICK * want_img)
            cmd = [exe, "exec", "--skip-git-repo-check", *CODEX_SANDBOX, "-C", str(edit or v),
                   "--output-last-message", str(v / "result.md")]
            mods, eff = pick_model("codex", os.environ.get("AIO_MODE", "balanced"), os.environ.get("AIO_HARD") == "1")
            mods = [a.model] if a.model else list(mods)
            base_cmd = cmd + ["-c", f'model_reasoning_effort="{eff}"'] + (DRIVE_APPROVE if a.drive else [])
            cands = [base_cmd + ["-m", m_, prompt] for m_ in mods] + [base_cmd + [prompt]]   # model không dùng được -> thử model kế, cuối cùng mặc định
            if want_img and not a.model:            # vẽ ảnh: model + mức suy nghĩ MẶC ĐỊNH của tài khoản (như v1.7), không ép
                cands = [cmd + (DRIVE_APPROVE if a.drive else []) + [prompt]]
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
                     "Câu trả lời cuối: nếu sản phẩm là văn bản thì ghi ĐẦY ĐỦ nội dung; nếu tạo file thì liệt kê tên file." + IMG_QUICK * want_img)
            cmd = [exe, "-p", gtext, "--output-format", "json", "--add-dir", str(proj)]
            if os.path.isdir(inp):
                cmd += ["--add-dir", inp]
            if edit:
                cmd += ["--add-dir", str(edit)]
            write_ = a.write or bool(edit)  # cho Gemini tự ghi file không cần hỏi (chỉ khi task cần tạo/sửa file)
            if write_:
                cmd += ["--dangerously-skip-permissions", "--sandbox"]
            plain = [exe, "-p", gtext, "--output-format", "json"] + (["--dangerously-skip-permissions", "--sandbox"] if write_ else [])
            cands = ([cmd + ["--model", a.model or gm]] if (a.model or gm) else []) + [cmd, plain]   # cờ không hỗ trợ -> bản đơn giản
            cmd = cands[0]
            cwd = v
        t0 = time.time()
        st["t0"] = t0
        cap = stat_cap(a.worker, kind, a.timeout)
        why = ""
        for i_, cmd in enumerate(cands):
            code, so, se, to, why = run_watched(cmd, cwd, a.timeout, a.worker, kind, cap, text)
            if why:                             # dừng sớm (hết lượt / kẹt / chậm bất thường): không thử cờ/model khác
                break
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
            if want_img:
                qu = img_quota_until(str(resp) + "\n" + se[-3000:])
                at, qu2 = agy_img_error(t0)
                if at:                                 # hết lượt vẽ AI giữa chừng: ảnh tạo sau lúc đó là vẽ bằng code
                    qu = qu or qu2
                    cd = set_aside_code_drawn(v, at)
                    if cd:
                        st["code_drawn"] = cd
                if qu:
                    st["img_quota_until"] = qu
        res = v / "result.md"
        if why == "limit":
            lm = note_limit(a.worker, se + "\n" + so)
            if want_img and a.worker == "gemini":
                qu = img_quota_until(so + "\n" + se)
                if qu:
                    st["img_quota_until"] = qu
            st.update(status="limit", reset=lm["until"], error="Dừng sớm: worker báo hết lượt giữa chừng, dùng lại lúc " +
                      datetime.datetime.fromtimestamp(lm["until"]).strftime("%H:%M") + ("" if lm["known"] else " (ước tính)"))
        elif why in ("idle", "slow"):
            st.update(status="timeout", error="Dừng sớm: " + (
                f"không có tiến triển {WATCH_IDLE.get(kind, 300) // 60} phút" if why == "idle" else
                f"chạy quá {round(cap / 60)} phút (gấp 4 lần thường thấy ở máy này)"))
        elif to:
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
    if "exit_code" in st:                          # đã thật sự chạy: ghi số liệu để app tự học chọn worker
        own_img = any(f.is_file() and f.suffix.lower() in IMG_EXT for f in v.iterdir())
        ok_ = st.get("status") == "completed" and (not want_img or own_img) and not st.get("code_drawn")
        if ok_:
            note_brief(a.worker, kind, st.get("seconds") or 0, task_txt)      # trước khi ghi số liệu: so với mức cũ
        record_stat(a.worker, kind, ok_, st.get("seconds"), why or st.get("status", ""))
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
    slot = st.pop("_slot", "")
    rs = fail_reason(st, v, want_img)
    if not os.environ.get("AIO_NOFALLBACK"):
        export_products(st, v, out, proj, a.group)          # sau khi đã kiểm ảnh/làm thay xong mới chuyển ra thư mục kết quả
    if rs:
        st["reason"] = rs
    judging = not os.environ.get("AIO_NOFALLBACK") and not os.environ.get("AIO_NOJUDGE") and want_img         and st.get("status") == "completed" and bool(st.get("export_dir")) and (proj / "tasks" / "criteria.md").is_file()
    wjson(v / "status.json", {**st, "status": "running", "phase": "đang chấm"} if judging else st)   # người chờ (wait) đợi chấm xong
    if not os.environ.get("AIO_NOFALLBACK"):
        if slot:                                   # nhả chỗ trước khi chấm: việc sửa do giám khảo giao cần chỗ chạy (máy 1 chỗ)
            Path(slot).unlink(missing_ok=True)
        auto_judge(st, v, out, proj, want_img)     # chấm ngay việc này (không đợi cả lô), trượt thì app tự giao sửa
        wjson(v / "status.json", st)
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st["status"] == "completed" else 1)


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
    limit = next((int(x) for x in argv if x.isdigit()), 3000 if Path("tasks/criteria.md").is_file() else 1500)   # có tự chấm/sửa: chờ lâu hơn
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
