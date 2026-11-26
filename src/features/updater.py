# Maestro — features/updater.py: Tự cập nhật: tải bản mới, áp dụng, bộ khởi động (boot), health-check, quay về bản cũ.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

UPDATED = {"pending": None}
VERS = HOME / "versions"


def busy_tasks():
    """Có task đang làm/chờ -> chưa tự cập nhật."""
    return any(t["status"] in ("running", "queued") or t.get("_redirect") for t in TASKS.values())


def _http(url, timeout, accept=None):
    import gzip
    import urllib.request
    h = {"User-Agent": "maestro", "Accept-Encoding": "gzip"}
    if accept:
        h["Accept"] = accept
    r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)
    b = r.read()
    return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b


def _fetch_any(urls, timeout):
    """Tải cùng lúc từ nhiều nguồn (GitHub API, jsDelivr, raw); nguồn nào xong trước dùng nguồn đó."""
    import queue
    q = queue.Queue()
    for u, acc in urls:
        threading.Thread(target=lambda u=u, acc=acc: q.put(_try(lambda: _http(u, timeout, acc))), daemon=True).start()
    err = None
    for _ in urls:
        ok, v = q.get()
        if ok:
            return v
        err = v
    raise err or OSError("download failed")


def _try(f):
    try:
        return True, f()
    except Exception as e:
        return False, e


def fetch_update():
    """Tải bản mới từ GitHub (chỉ code, không có dữ liệu). Trả về số phiên bản mới, '' nếu đang mới nhất."""
    import hashlib
    if not UPDATE_BASE or VERSION == "dev":
        return ""
    if UPDATED["pending"] and (VERS / UPDATED["pending"] / "app.py").exists():
        return UPDATED.get("release") or UPDATED["pending"]          # đã tải sẵn ở nền
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/", UPDATE_BASE)
    repo, ref = m.group(1), m.group(2)
    ok, sha = _try(lambda: _http(f"https://api.github.com/repos/{repo}/commits/{ref}", 8,
                                 "application/vnd.github.sha").decode().strip())
    ref2 = sha if ok and re.fullmatch(r"[0-9a-f]{40}", sha) else ref      # theo đúng commit -> không dính bộ đệm

    def srcs(name):
        u = [(f"https://api.github.com/repos/{repo}/contents/{name}?ref={ref2}", "application/vnd.github.raw"),
             (f"https://raw.githubusercontent.com/{repo}/{ref2}/{name}", None)]
        if ref2 != ref:
            u.append((f"https://cdn.jsdelivr.net/gh/{repo}@{ref2}/{name}", None))
        return u
    meta = json.loads(_fetch_any(srcs("latest.json"), 10))
    if meta.get("version", "") > VERSION:
        if meta["version"] in ((rjson(DATA / "boot.json", {}) or {}).get("bad_versions") or []):
            return ""                                  # bản này đã lỗi khi khởi động trên máy này -> bỏ qua
        body = _fetch_any(srcs("app.py"), 40)
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


def update_now(force=False):
    try:
        new = fetch_update()
    except Exception:
        return {"error": "Không kết nối được GitHub, thử lại sau"}
    if not new and not UPDATED["pending"]:
        return {"ok": True, "msg": f"Đang dùng bản mới nhất (v{RELEASE})"}
    if busy_tasks() and not force:          # có task đang chạy: hỏi trước (không bấm thì tự cập nhật khi các task xong)
        return {"ask": True, "msg": f"Có bản mới v{new or UPDATED.get('release') or ''}"}
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
