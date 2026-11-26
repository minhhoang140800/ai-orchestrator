# Maestro — core/scheduler.py: Lịch chạy: sched_tick, start_run, watch (đọc stream + chống treo), file kết quả mới.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

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
            title = re.sub(r"^(?:tiêu đề|tên|title)\s*:\s*", "", title, flags=re.I).strip(" \"'*#`.")
            if 2 <= len(title) <= 80 and not t.get("renamed"):
                t["title"] = title
    except Exception:
        pass
    with _tlock:
        t["titling"] = False
        tsave(t)


ASK_END = re.compile(r"\?\s*$")


def asks_user(text):
    """Câu trả lời cuối kết thúc bằng câu hỏi cho người dùng (vd 'Bạn chọn A hay B?') -> task chờ trả lời, không coi là Xong."""
    lines = [re.sub(r"[*_`>#\s]+", " ", x).strip() for x in (text or "").strip().splitlines()]
    lines = [x for x in lines if x]
    return bool(lines) and bool(ASK_END.search(lines[-1])) and len(lines[-1]) <= 400


FOLDER_NO = re.compile(r"^(\d{1,4}) - ")


def next_folder(parent, title):
    """Thư mục kết quả của đoạn chat: '<số thứ tự> - <tên đoạn chat>' (01, 02, ...), đánh số tiếp theo trong cùng thư mục cha."""
    root = Path(CFG["output"]) / parent
    nums = [int(m.group(1)) for x in (root.iterdir() if root.is_dir() else []) if x.is_dir() for m in [FOLDER_NO.match(x.name)] if m]
    nums += [int(m.group(1)) for x in TASKS.values() if x.get("folder") and Path(x["folder"]).parent == parent
             for m in [FOLDER_NO.match(Path(x["folder"]).name)] if m]
    n = max(nums, default=0) + 1
    while True:
        name = parent / f"{n:02d} - {safe_name(title, 70)}"
        if not (Path(CFG["output"]) / name).exists():
            return str(name)
        n += 1


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
    if not t.get("folder"):                   # [<dự án>/]<số thứ tự> - <tên đoạn chat>; mỗi lần xuất: thư mục con YYYYMMDD-<tên>
        pj = project_of(t)
        t["folder"] = next_folder(Path(safe_name(pj["name"], 60)) if pj else Path(), t["title"])
    out_dir(t).mkdir(parents=True, exist_ok=True)
    if t.get("kind") == "code" and not t.get("pdir"):    # Code mode: tự tạo thư mục dự án mới trong thư mục kết quả
        t["pdir"] = str(out_dir(t) / "code")
        Path(t["pdir"]).mkdir(parents=True, exist_ok=True)
    write_claude_md(t)
    restore_notes(t)
    d = wdir(t)
    q = d / "question.md"
    if q.exists():
        q.unlink()
    ef = (wdir(t) / "tasks" / "effort.txt")
    hard = t.get("mode") == "pro" and ef.exists() and ef.read_text("utf-8", "ignore").strip() == "xhigh"
    code = t.get("kind") == "code"
    cm, ce = pick_model(pm, "code" if code else t.get("mode", "balanced"), hard)   # Pro + bước quá khó: mức cao nhất
    if pm == "claude" and not hard and route(t, nxt) == "ask":
        ce = "low"                             # câu hỏi / tra cứu đơn giản: Opus mức suy nghĩ low (nhanh, đỡ hạn mức)
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
            [] if t.get("session_started") else ["-C", str(d)]) + [pm_prompt_file(d, build_prompt(t, nxt))]
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
    threading.Thread(target=usage_turn, args=(t, work(t), "u0"), daemon=True).start()   # đo mức dùng trước lượt (chạy nền)
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


PM_IDLE_MAX = int(os.environ.get("AIO_PM_IDLE") or 3600)        # không có hoạt động quá 60 phút -> coi là treo
PM_TOTAL_MAX = int(os.environ.get("AIO_PM_TOTAL") or 4 * 3600)  # 1 lượt tối đa 4 giờ


def watch(t, p, nxt):
    raw = open(wdir(t) / "run.log", "a", encoding="utf-8")
    raw.write(f"\n===== {now()} {nxt.get('kind')} =====\n")
    result = None
    w = work(t)
    since = (w or {}).get("started_ts", time.time())
    pm = t.get("pm") or "claude"
    cx = {"last": "", "err": ""}               # ChatGPT làm quản lý: câu trả lời/lỗi gần nhất
    errbuf = []
    th_err = threading.Thread(target=lambda: errbuf.extend(p.stderr), daemon=True)   # đọc song song: stderr đầy không làm treo
    th_err.start()
    act = {"at": time.time(), "t0": time.time(), "done": False}

    def watchdog():                            # không có hoạt động 60 phút / chạy quá 4 giờ -> dừng, báo lỗi rõ
        while not act["done"]:
            time.sleep(min(30, PM_IDLE_MAX / 2))
            now_ = time.time()
            if p.poll() is None and (now_ - act["at"] > PM_IDLE_MAX or now_ - act["t0"] > PM_TOTAL_MAX):
                t["_timeout"] = True
                kill_tree(p)
                return
    threading.Thread(target=watchdog, daemon=True).start()
    for line in p.stdout:
        act["at"] = time.time()
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
    act["done"] = True
    th_err.join(timeout=10)
    err = "".join(errbuf)
    if t.pop("_timeout", None):
        err += ("\nQuá thời gian: AI không phản hồi " + str(PM_IDLE_MAX // 60) + " phút (hoặc chạy quá "
                + str(PM_TOTAL_MAX // 3600) + " giờ). Bấm Tiếp tục để chạy lại.")
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
            if asks_user(rt):                  # câu trả lời kết thúc bằng câu hỏi cho người dùng -> "Chờ bạn trả lời" + thông báo
                t["status"] = "waiting"
                tmsg(t, "claude", rt, kind="question", files=fs, dir=fd)
                threading.Thread(target=mem_update, args=(t,), daemon=True).start()     # giữ nháp (task chưa xong), vẫn ghi nhớ
            else:
                tmsg(t, "claude", rt, files=fs, dir=fd)
                threading.Thread(target=after_turn, args=(t, t.get("wi")), daemon=True).start()   # dọn nháp + cập nhật bộ nhớ (nền)
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
    try:
        usage_turn(t, w, "u1")              # đo sau lượt (chạy sau khi đã trả kết quả) -> lượt này tốn bao nhiêu, cộng vào cả task
        tsave(t)
    except Exception as e:
        dlog(f"usage_turn: {e}")


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
            if t.get("titling") and not t.get("folder") and time.time() - t.get("created_ts", 0) < 10:
                continue          # lượt đầu: chờ đặt tên tối đa 10 giây để thư mục kết quả mang đúng tên đoạn chat
            if t.get("_cleaning"):
                continue          # đang dọn nháp lượt trước (vài giây) -> chạy ngay sau đó
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
