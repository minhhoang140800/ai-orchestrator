# Maestro — features/codemode.py: Code mode: điểm khôi phục, cài phần mềm thiếu.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def _git(p, *a, timeout=60):
    r = subprocess.run(["git", "-C", str(p), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def code_checkpoint(t):
    """Trước mỗi lượt Code mode: lưu trạng thái thư mục dự án. Dự án git: 'git stash create' (không đổi file nào);
    không có git: chép file (bỏ .git, node_modules; tối đa 300 MB) vào lịch sử task. Giữ 5 điểm gần nhất."""
    p, cps = Path(t.get("pdir", "")), t.setdefault("checkpoints", [])
    if not p.is_dir():
        return
    n = (cps[-1]["n"] + 1) if cps else 1
    cp = {"n": n, "ts": time.time(), "time": now()}
    try:
        if shutil.which("git") and (p / ".git").exists() and _git(p, "rev-parse", "HEAD")[0] == 0:
            head = _git(p, "rev-parse", "HEAD")[1]
            sha = _git(p, "stash", "create")[1] or head
            _git(p, "update-ref", f"refs/maestro/{t['id']}-{n}", sha)       # giữ lại, không bị git dọn mất
            cp.update(git=sha, head=head)
        else:
            dst = tdir(t) / "checkpoints" / str(n)
            r = backup_dir(p, dst)
            cp.update(dir=r if r == str(dst) else "", note="" if r == str(dst) else r)
    except Exception as e:
        cp["note"] = f"lỗi: {e}"[:200]
    cps.append(cp)
    for old in cps[:-5]:
        if old.get("dir"):
            shutil.rmtree(old["dir"], ignore_errors=True)
        elif old.get("git"):
            _git(p, "update-ref", "-d", f"refs/maestro/{t['id']}-{old['n']}")
    t["checkpoints"] = cps[-5:]


def code_restore(t):
    """Đưa thư mục dự án về điểm khôi phục gần nhất (trước lượt làm cuối). File mới tạo sau đó vẫn giữ."""
    cps, p = t.get("checkpoints") or [], Path(t.get("pdir", ""))
    if not cps or not p.is_dir():
        return {"error": "Chưa có điểm khôi phục"}
    cp = cps[-1]
    if cp.get("git"):
        cur = _git(p, "stash", "create")[1]
        if cur:
            _git(p, "update-ref", f"refs/maestro/{t['id']}-undo", cur)     # bản trước khi khôi phục (phòng khi đổi ý)
        rc, _, err = _git(p, "reset", "--hard", cp["head"])
        if rc == 0 and cp["git"] != cp["head"]:
            rc, _, err = _git(p, "stash", "apply", cp["git"])
        if rc != 0:
            return {"error": "Khôi phục chưa xong: " + err[-200:]}
    elif cp.get("dir") and Path(cp["dir"]).is_dir():
        src = Path(cp["dir"])
        for f in src.rglob("*"):
            if f.is_file():
                o = p / f.relative_to(src)
                o.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, o)
    else:
        return {"error": "Điểm khôi phục không dùng được: " + (cp.get("note") or "thư mục quá lớn để sao lưu")}
    tmsg(t, "claude", f"Đã khôi phục thư mục dự án về lúc **{cp['time']}** (trước lượt làm cuối). File mới tạo sau đó vẫn giữ.")
    return {"ok": True, "msg": "Đã khôi phục"}


INSTALL_ID = {"winget": r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", "brew": r"[a-z0-9][a-z0-9@._+/-]{0,80}",
              "pip": r"[A-Za-z0-9][A-Za-z0-9._,=\[\]-]{0,80}", "npm": r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+(?:@[A-Za-z0-9._-]+)?"}


def read_install(t):
    """Yêu cầu cài phần mềm do Claude ghi (install_request.json) -> danh sách đã kiểm tra (chỉ tên gói hợp lệ, không lệnh tự do)."""
    d = rjson(wdir(t) / "install_request.json", None)
    if not isinstance(d, dict):
        return None
    items = []
    for it in (d.get("items") or [])[:10]:
        if not isinstance(it, dict):
            continue
        ok = {k: str(it[k]).strip() for k in INSTALL_ID if it.get(k) and re.fullmatch(INSTALL_ID[k], str(it[k]).strip())}
        if ok:
            items.append({"name": str(it.get("name") or next(iter(ok.values())))[:60], **ok})
    return {"why": str(d.get("why") or "")[:300], "items": items} if items else None


def install_script(items, log, done):
    """File cài đặt (.bat Windows / .command Mac) chạy lần lượt winget/brew, pip, npm cho các gói đã duyệt."""
    py = py_console()
    if IS_WIN:
        L = ["@echo off", "chcp 65001 >nul", "title Maestro - cai dat phan mem", "echo Maestro dang cai phan mem. Khong tat cua so nay.", ""]
        for it in items:
            if it.get("winget"):
                L += [f'echo === {it["winget"]}', f'winget install -e --id {it["winget"]} --silent --accept-source-agreements --accept-package-agreements',
                      f'echo [%ERRORLEVEL%] winget {it["winget"]}>> "{log}"']
        L.append('set "PATH=%PATH%;%ProgramFiles%\\nodejs;%APPDATA%\\npm"')
        for it in items:
            if it.get("pip"):
                L += [f'echo === {it["pip"]}', f'"{py}" -m pip install --user --disable-pip-version-check {it["pip"]}', f'echo [%ERRORLEVEL%] pip {it["pip"]}>> "{log}"']
            if it.get("npm"):
                L += [f'echo === {it["npm"]}', f'call npm install -g {it["npm"]}', f'echo [%ERRORLEVEL%] npm {it["npm"]}>> "{log}"']
        L += [f'echo done> "{done}"', "echo XONG. Cua so tu dong sau 5 giay.", "timeout /t 5 >nul"]
        return "\r\n".join(L) + "\r\n"
    L = ["#!/bin/bash", 'echo "Maestro đang cài phần mềm. Không tắt cửa sổ này."',
         'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"']
    for it in items:
        if it.get("brew"):
            L += [f'echo "=== {it["brew"]}"', f'if command -v brew >/dev/null; then brew install {it["brew"]}; echo "[$?] brew {it["brew"]}" >> "{log}"; '
                  f'else echo "[1] brew {it["brew"]}: chưa có Homebrew" >> "{log}"; fi']
        if it.get("pip"):
            L += [f'"{py}" -m pip install --user {it["pip"]} || "{py}" -m pip install --user --break-system-packages {it["pip"]}',
                  f'echo "[$?] pip {it["pip"]}" >> "{log}"']
        if it.get("npm"):
            L += [f'npm install -g {it["npm"]}', f'echo "[$?] npm {it["npm"]}" >> "{log}"']
    L += [f'echo done > "{done}"', 'echo "XONG. Có thể đóng cửa sổ này."', "sleep 3", "exit 0"]
    return "\n".join(L) + "\n"


def run_install(t):
    """Người dùng bấm Cài: chạy file cài trong cửa sổ hiện tiến trình, xong thì task tự chạy tiếp và báo kết quả."""
    req = t.get("install") or {}
    items = req.get("items") or []
    d = tdir(t) / "install"
    d.mkdir(parents=True, exist_ok=True)
    log, done = d / "install.log", d / "done.txt"
    log.write_text("", encoding="utf-8")
    done.unlink(missing_ok=True)
    f = d / ("cai_dat.bat" if IS_WIN else "cai_dat.command")
    f.write_text(install_script(items, log, done), encoding="utf-8")
    try:
        if IS_WIN:
            subprocess.run(["cmd.exe", "/c", "start", "/wait", "Maestro - cài đặt", "cmd.exe", "/c", str(f)], creationflags=NO_WINDOW, timeout=3600)
        else:
            os.chmod(f, 0o755)
            subprocess.Popen(["open", "-a", "Terminal", str(f)] if IS_MAC else ["bash", str(f)])
            t0 = time.time()
            while time.time() - t0 < 3600 and not done.exists():
                time.sleep(3)
    except Exception as e:
        dlog(f"run_install: {e}")
    refresh_path()                       # phần mềm vừa cài thấy được ngay
    res = log.read_text("utf-8", "replace").strip() if log.exists() else ""
    names = ", ".join(i["name"] for i in items)
    with _tlock:
        t.pop("installing", None)
        t.pop("install", None)
        tmsg(t, "user", f"Đã chạy cài đặt: {names}")
        t["status"] = "queued"
        t["next"] = {"kind": "answer", "text": f"[Maestro] Người dùng đã bấm Cài. Kết quả (mã [0] = thành công):\n{res or '(không có log)'}\n"
                                               "Kiểm tra lại phần mềm vừa cài rồi làm tiếp."}
        tsave(t)
