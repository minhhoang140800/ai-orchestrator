# Maestro — web/server.py: HTTP: class H (API + giao diện), task_detail, registry file kết quả, ảnh thu nhỏ, serve().
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

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
    return (APP.parent / "web" / "ui.html").read_bytes()      # chạy từ mã nguồn (dev): src/web/ui.html


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
        ws = cur.get("subtasks") or []                       # trạng thái thật (không đoán phần trăm): việc đã giao xong / đang chạy
        n_run = sum(x.get("status") == "running" for x in ws)
        cur["phase"] = (f"{len(ws) - n_run}/{len(ws)} việc xong" + (f" · {n_run} đang chạy" if n_run else "")) if ws else ""
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
    d["tpl_suggest"] = tpl_suggest(t)
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
                       "last": max([m.get("time") or "" for m in t.get("messages", [])] + [t.get("created") or ""]),
           **({"status": "limit"} if t["status"] == "queued" and (t.get("hold_until", 0) > time.time() or pm_limit(t)) else {})}
                      for t in TASKS.values() if t["status"] != "draft"]
                for x in ts:
                    if TASKS[x["id"]].get("need_login") and x["status"] == "queued":
                        x["status"] = "login"
            ts.sort(key=lambda x: x["last"], reverse=True)     # task có tin nhắn mới nhất lên đầu
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
                                   "templates": [{"id": x["id"], "name": x["name"], "steps": len(x.get("steps", []))} for x in templates()], "instructions": CFG["instructions"], "reply_style": CFG.get("reply_style") or "short", "mem_auto": mem_text(),
                                   "tasks": ts, "version": VERSION, "release": RELEASE, "autostart": autostart_on(), "os": "mac" if IS_MAC else "win" if IS_WIN else "other", "updated": upd, "update_fail": fail,
                                   "machine": {"cpu": CPU, "ram": RAM_GB, "max": MAX_RUNNING}, "setup": setup_state(),
                                   "learn": learn_state(), "remote": remote_state()})
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
        if path == "/api/remote":
            return set_remote(d)
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
            if "auto" in d:                              # bộ nhớ Maestro tự ghi (người dùng sửa/xoá phần chung)
                mem_save(d.get("auto"))
            if "instructions" in d:
                CFG["instructions"] = str(d.get("instructions") or "")[:20000]
                save_cfg()
            if d.get("reply_style") in ("short", "detail"):
                CFG["reply_style"] = d["reply_style"]
                save_cfg()
            return {"ok": True, "msg": "Đã lưu ghi nhớ"}
        if path == "/api/gemini-key":
            return set_gemini_key(str(d.get("key") or ""))
        if path == "/api/update":
            return update_now(force=bool(d.get("force")))
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
            return act(d.get("id", ""), d.get("action", ""), d.get("text", ""), d.get("files"), d.get("template", ""),
                       bool(d.get("urgent")))
        if path == "/api/task/file":
            return open_task_file(d.get("id", ""), d.get("path", ""))
        if path == "/api/task/subdir":
            return open_sub_dir(d.get("id", ""), d.get("sub", ""))
        return {"error": "bad request"}


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
    threading.Thread(target=usage_loop, daemon=True).start()
    threading.Thread(target=check_update, daemon=True).start()
    threading.Thread(target=agy_update_loop, daemon=True).start()
    threading.Thread(target=remote_watch, daemon=True).start()
    if not background and not os.environ.get("AIO_NO_BROWSER"):
        webbrowser.open(url)
    print("Maestro:", url, flush=True)
    srv.serve_forever()
