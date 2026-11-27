# Maestro — core/tasks.py: Vòng đời task: tạo, gửi, act (tạm dừng/hủy/gửi thêm), dọn nháp, sub-task.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def finish_clean(t, wi=None):
    """Task xong: lưu lại bảng sub-task + feedback/quyết định vào lịch sử, rồi xóa toàn bộ nháp/hướng dẫn/bản trùng.
    wi = lượt vừa xong: người dùng đã nhắn tiếp (lượt mới bắt đầu) -> không xoá nháp của lượt mới."""
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
        with _tlock:
            if wi is not None and (t.get("status") != "done" or t.get("wi") != wi):
                return                          # lượt mới đã bắt đầu: giữ nguyên nháp
            t["_cleaning"] = True
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
    finally:
        t.pop("_cleaning", None)


def resume_at(t):
    """tasks/resume_at.txt do Claude ghi khi muốn chờ (vd worker hết lượt, reset 14:21): dòng 1 = giờ HH:MM, còn lại = việc
    làm tiếp -> (epoch, ghi chú); đọc xong xoá. Giờ đã qua -> chạy ngay; tối đa 24 giờ."""
    f = wdir(t) / "tasks" / "resume_at.txt"
    if not f.is_file():
        return None
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        f.unlink()
        m = re.search(r"(\d{1,2})[:h](\d{2})", lines[0] if lines else "")
        if not m:
            return None
        now_ = datetime.datetime.now()
        at = now_.replace(hour=int(m.group(1)) % 24, minute=int(m.group(2)), second=0, microsecond=0)
        if at < now_ - datetime.timedelta(hours=1):     # giờ đã qua lâu -> hiểu là ngày mai
            at += datetime.timedelta(days=1)
        return max(time.time(), at.timestamp()), "\n".join(lines[1:]).strip()
    except Exception as e:
        dlog(f"resume_at {t.get('id')}: {e}")
        return None


def open_sub_dir(tid, sub):
    """Mở thư mục kết quả của 1 việc đã giao (work/<ID>/vN mới nhất, gồm cả bên làm thay gpt/ gemini/)."""
    t = TASKS.get(tid)
    if not t or not re.fullmatch(r"[\w.-]{1,60}", sub or ""):
        return {"error": "Không tìm thấy việc"}
    d = wdir(t) / "work" / sub
    vs = sorted((v for v in d.iterdir() if v.is_dir() and re.fullmatch(r"v\d+", v.name)), key=lambda v: int(v.name[1:])) if d.is_dir() else []
    sts = [rjson(v / "status.json", {}) or {} for v in reversed(vs)]
    ex = next((x["export_dir"] for x in sts if x.get("export_dir") and Path(x["export_dir"]).is_dir()), None)
    if not ex:                                   # việc đang chạy / chưa xuất file: mở thư mục nhóm sẽ nhận file (không mở nháp work/)
        args = next((x.get("args") for x in sts if x.get("args")), None) or []
        grp = args[args.index("--group") + 1] if "--group" in args and args.index("--group") + 1 < len(args) else ""
        g = export_group(Path(sub), grp)
        old = sorted((p for p in wdir(t).parent.glob("*-" + g) if p.is_dir() and re.fullmatch(r"\d{8}-" + re.escape(g), p.name)), reverse=True)
        ex = str(old[0]) if old else None
    if not ex and not vs:
        return {"error": "Thư mục việc này đã được dọn (task xong: file nằm trong thư mục kết quả)"}
    open_path(ex or wdir(t).parent)              # chưa có thư mục nhóm: mở thư mục kết quả của task
    return {"ok": True}


def restore_notes(t):
    """Lượt mới sau khi nháp đã dọn: ghi lại tasks/decisions.json + tasks/criteria.md từ lịch sử task."""
    d = wdir(t) / "tasks"
    try:
        if t.get("decisions") and not (d / "decisions.json").exists():
            d.mkdir(parents=True, exist_ok=True)
            wjson(d / "decisions.json", t["decisions"])
        crit = (t.get("recipe") or {}).get("criteria")
        if crit and not (d / "criteria.md").exists():
            d.mkdir(parents=True, exist_ok=True)
            (d / "criteria.md").write_text(crit, encoding="utf-8")
    except Exception as e:
        dlog(f"restore_notes {t.get('id')}: {e}")


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
                    "via": st.get("via") or "",
                    "ts": (v / "status.json").stat().st_mtime if (v / "status.json").exists() else v.stat().st_mtime})
    return out


def act(tid, action, text="", files=None, template="", urgent=False):
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
            threading.Thread(target=kill_workers, args=(t,), daemon=True).start()
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
            if s == "running" and urgent:              # nút "Gửi gấp": ngắt, Claude điều chỉnh rồi làm tiếp
                q = t.pop("_queued", None)             # tin đã xếp hàng trước đó: gộp vào tin gấp
                t["_redirect"] = {"kind": "urgent", "text": ((q or {}).get("text", "") + "\n\n" + text).strip(),
                                  "files": (q or {}).get("files", []) + files}
                kill_tree(PROCS.get(tid))
            elif s == "running":                       # mặc định: XẾP HÀNG, không ngắt lượt đang làm (khỏi mất việc);
                q = t.setdefault("_queued", {"text": "", "files": []})   # chạy ngay khi lượt hiện tại xong
                q["text"] = (q["text"] + "\n\n" + text).strip()
                q["files"] = q["files"] + files
            elif s == "queued" and t.get("next"):      # chưa chạy: gộp vào yêu cầu đang chờ
                n = t["next"]
                n["text"] = (n.get("text", "") + "\n\n" + text).strip()
                n["files"] = (n.get("files") or []) + files
                if n.get("kind") in ("resume", "timer"):
                    n["kind"] = "urgent"
                if t.pop("hold_timer", None):          # đang chờ giờ hẹn của Claude (không phải Claude hết lượt): chạy ngay
                    t.pop("hold_until", None)
            else:
                t["status"] = "queued"
                t["next"] = {"kind": "answer" if s == "waiting" else "followup", "text": text, "files": files}
        elif action == "mode" and (text == "code") != (t.get("kind") == "code"):
            return {"error": "Tạo task mới để chạy Code Mode" if text == "code"
                    else "Task Code chỉ chạy ở chế độ Code"}
        elif action == "judge":
            t["judge"] = text == "on"                  # Giám sát: áp dụng từ lượt chạy kế tiếp
            if wdir(t).exists():
                write_claude_md(t)
        elif action == "mode" and MODE_ALIAS.get(text, text) in MODES:
            t["judge"] = norm_mode(text) in JUDGE_MODES   # đổi chế độ: Pro/Code tự bật Giám sát, chế độ khác tắt
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
