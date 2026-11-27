# Maestro — features/diagnostics.py: Export log, dọn nháp/bản trùng (Clear cache).
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

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
                    if m.get("cost"):
                        lines.append("    mức dùng lượt ≈ " + "; ".join(f"{k} +{v.get('five_hour', 0)}% phiên +{v.get('week', 0)}% tuần" for k, v in m["cost"].items()))
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
