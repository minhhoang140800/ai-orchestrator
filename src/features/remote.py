# Maestro — features/remote.py: Dùng trên điện thoại: Cloudflare Quick Tunnel, ntfy, class HR, mã QR.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

# Cổng riêng chỉ nghe 127.0.0.1; cloudflared (Cloudflare Quick Tunnel, miễn phí, không cần tài khoản) mở 1 link https
# ra Internet trỏ vào cổng này. Vào được phải có mã bí mật (link/QR lần đầu -> cookie). Điện thoại chỉ cần trình duyệt.
REMOTE_PORT = int(os.environ.get("AIO_REMOTE_PORT") or 8790)
REMOTE = {"state": "off", "url": "", "msg": ""}
_rlock = threading.Lock()
_rsrv = []
CF_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
# Mã QR cố định: trỏ về trang tĩnh (GitHub Pages, nhánh gh-pages) kèm #t=<kênh>&k=<mã>; app báo link hiện tại của máy
# lên kênh ntfy.sh (miễn phí, không cần tài khoản), trang đọc kênh rồi chuyển sang. Link Cloudflare đổi -> QR vẫn giữ nguyên.
MOBILE_PAGE = os.environ.get("AIO_MOBILE_PAGE") or "https://minhhoang140800.github.io/ai-orchestrator/"
NTFY = os.environ.get("AIO_NTFY") or "https://ntfy.sh/"


def cf_bin():
    return HOME / "bin" / ("cloudflared.exe" if IS_WIN else "cloudflared")


def cf_download():
    import platform
    import tarfile
    import urllib.request
    b = cf_bin()
    if b.exists() and b.stat().st_size > 1_000_000:
        return b
    b.parent.mkdir(parents=True, exist_ok=True)
    arm = platform.machine().lower() in ("arm64", "aarch64")
    name = ("cloudflared-windows-amd64.exe" if IS_WIN else
            f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz" if IS_MAC else
            f"cloudflared-linux-{'arm64' if arm else 'amd64'}")
    tmp = b.with_name(b.name + ".part")
    REMOTE["msg"] = "Đang tải bộ kết nối (khoảng 20-40 MB, chỉ lần đầu)…"
    req = urllib.request.Request(CF_BASE + name, headers={"User-Agent": "maestro"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    if name.endswith(".tgz"):
        with tarfile.open(tmp) as t:
            m = next(x for x in t.getmembers() if x.name.endswith("cloudflared"))
            with t.extractfile(m) as src, open(b, "wb") as dst:
                shutil.copyfileobj(src, dst)
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, b)
    if not IS_WIN:
        b.chmod(0o755)
    return b


def remote_key():
    if not CFG.get("remote_key"):
        CFG["remote_key"] = secrets.token_urlsafe(24)
        save_cfg()
    return CFG["remote_key"]


def remote_topic():
    if not CFG.get("remote_topic"):
        CFG["remote_topic"] = "maestro-" + secrets.token_urlsafe(18)
        save_cfg()
    return CFG["remote_topic"]


def remote_link():
    return f"{MOBILE_PAGE}#t={remote_topic()}&k={remote_key()}"


def remote_publish():
    """Báo link hiện tại lên kênh (tin trên ntfy.sh hết hạn sau 12 giờ -> báo lại định kỳ)."""
    import urllib.request
    if not REMOTE["url"]:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(NTFY + remote_topic(), data=REMOTE["url"].encode(),
                                                      headers={"User-Agent": "maestro", "Cache": "yes"}), timeout=20).read()
        REMOTE["pub"] = time.time()
    except Exception as e:
        dlog(f"remote_publish: {e}")


def remote_ok(cookie):
    import hmac
    k = CFG.get("remote_key") or ""
    m = re.search(r"(?:^|;\s*)aio_k=([A-Za-z0-9_\-]+)", cookie or "")
    return bool(k and m and hmac.compare_digest(m.group(1), k))


def remote_alive(url):
    import urllib.request
    try:
        r = urllib.request.urlopen(urllib.request.Request(url + "/remote-ping", headers={"User-Agent": "maestro"}), timeout=8)
        return b"maestro-remote" in r.read(100)
    except Exception:
        return False


def remote_kill(pid):
    if not pid:
        return
    try:
        if IS_WIN:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                               text=True, creationflags=NO_WINDOW)
            if "cloudflared" in (r.stdout or "").lower():
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10)
            if "cloudflared" in (r.stdout or ""):
                os.kill(pid, 9)
    except Exception:
        pass


def remote_server():
    """Cổng cho điện thoại (127.0.0.1:REMOTE_PORT, chỉ cloudflared trên máy này nối vào)."""
    if _rsrv:
        return True
    for _ in range(20):                      # vừa tự cập nhật: chờ bản cũ nhả cổng
        try:
            s = ThreadingHTTPServer(("127.0.0.1", REMOTE_PORT), HR)
            break
        except OSError:
            time.sleep(0.5)
    else:
        return False
    _rsrv.append(s)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return True


def remote_start():
    with _rlock:
        try:
            REMOTE.update(state="starting", msg="Đang bật…")
            if not remote_server():
                raise RuntimeError(f"Cổng {REMOTE_PORT} đang bị chương trình khác dùng")
            remote_key()
            st = rjson(DATA / "remote.json", {}) or {}
            if st.get("url") and remote_alive(st["url"]):        # kết nối cũ còn chạy (vd app vừa cập nhật) -> giữ link
                REMOTE.update(state="on", url=st["url"], msg="")
                remote_publish()
                return
            remote_kill(st.get("pid"))
            exe = cf_download()
            REMOTE["msg"] = "Đang tạo link…"
            log = DATA / "cloudflared.log"
            log.unlink(missing_ok=True)
            flags = (0x00000008 | 0x00000200 | 0x08000000) if IS_WIN else 0   # DETACHED | NEW_GROUP | NO_WINDOW
            cmd = [str(exe), "tunnel", "--no-autoupdate", "--logfile", str(log), "--url", f"http://127.0.0.1:{REMOTE_PORT}"]
            # qua tiến trình trung gian: cloudflared chạy tách khỏi app -> app cập nhật/khởi động lại vẫn giữ link
            p = subprocess.run([sys.executable, "-c", "import subprocess,sys,os;print(subprocess.Popen(sys.argv[2:],creationflags=int(sys.argv[1]),"
                                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=os.name!='nt').pid)",
                                str(flags), *cmd], capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30)
            pid = int((p.stdout or "0").strip() or 0)
            url = ""
            for _ in range(90):
                time.sleep(1)
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                              log.read_text(encoding="utf-8", errors="replace") if log.exists() else "")
                if m:
                    url = m.group(0)
                    break
            if not url:
                remote_kill(pid)
                raise RuntimeError("Không tạo được link (kiểm tra mạng Internet rồi bật lại)")
            wjson(DATA / "remote.json", {"pid": pid, "url": url})
            REMOTE.update(state="on", url=url, msg="")
            remote_publish()
        except Exception as e:
            dlog(f"remote_start: {e}")
            REMOTE.update(state="error", url="", msg=str(e)[:200])


def remote_stop():
    with _rlock:
        st = rjson(DATA / "remote.json", {}) or {}
        remote_kill(st.get("pid"))
        (DATA / "remote.json").unlink(missing_ok=True)
        REMOTE.update(state="off", url="", msg="")


def remote_watch():
    """Bật sẵn khi mở app; kết nối rớt (mất mạng, máy ngủ dậy) -> tự tạo lại."""
    while True:
        if CFG.get("remote_on"):
            if REMOTE["state"] in ("off", "error") or (REMOTE["state"] == "on" and
                                                         not any(remote_alive(REMOTE["url"]) for _ in range(2))):
                if REMOTE["state"] == "on":
                    (DATA / "remote.json").unlink(missing_ok=True)
                remote_start()
            elif REMOTE["state"] == "on" and time.time() - REMOTE.get("pub", 0) > 3 * 3600:
                remote_publish()
        time.sleep(60)


class HR(H):
    """Cổng điện thoại: thay kiểm tra Host/máy-này bằng mã bí mật (cookie)."""
    def ok_host(self):
        return remote_ok(self.headers.get("Cookie", ""))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/remote-ping":
            return self.send(200, b"maestro-remote", "text/plain")
        k = parse_qs(u.query).get("k", [""])[0]
        if k and remote_ok("aio_k=" + k):    # mở link/QR lần đầu -> nhớ mã 1 năm, bỏ mã khỏi thanh địa chỉ
            return self.send(302, b"", "text/plain", {"Location": "/", "Set-Cookie":
                             f"aio_k={k}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Lax"})
        if not self.ok_host():
            return self.send(401, LOCKED_HTML, "text/html") if u.path == "/" else self.send(403, {"error": "forbidden"})
        return super().do_GET()


def remote_state():
    return {"on": bool(CFG.get("remote_on")), "state": REMOTE["state"], "msg": REMOTE["msg"], "link": remote_link()}


def set_remote(d):
    if d.get("rotate"):                       # đổi mã: mọi điện thoại đã vào phải quét lại mã mới
        CFG["remote_key"] = secrets.token_urlsafe(24)
        CFG["remote_topic"] = "maestro-" + secrets.token_urlsafe(18)
        save_cfg()
        remote_publish()
        return {"ok": True, "msg": "Đã đổi mã. Điện thoại cần quét lại mã QR mới."}
    on = bool(d.get("on"))
    CFG["remote_on"] = on
    save_cfg()
    if on:
        REMOTE.update(state="starting", msg="Đang bật…")
        threading.Thread(target=remote_start, daemon=True).start()
    else:
        remote_stop()
    return {"ok": True}


LOCKED_HTML = """<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maestro</title></head><body style="font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0;color:#333;text-align:center;padding:24px">
<div><b style="font-size:20px">Maestro</b><p>Chưa vào được. Trên máy tính, mở Maestro → <b>Cài đặt</b> → biểu tượng điện thoại,<br>rồi quét mã QR bằng camera điện thoại.</p></div></body></html>""".encode()
