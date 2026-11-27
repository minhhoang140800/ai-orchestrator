# Maestro — core/procs.py: Tiến trình: tìm chương trình, dừng cây tiến trình, giữ chỗ worker, mở thư mục, PATH, debug log.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def kill_pid_tree(pid):
    """Dừng tiến trình + mọi tiến trình con của nó."""
    if not pid:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
            return
        kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=10).stdout.split()
        for k in kids:
            kill_pid_tree(int(k))
        os.kill(pid, 9)
    except Exception:
        pass


def kill_tree(p):
    if p and p.poll() is None:
        kill_pid_tree(p.pid)


def pid_alive(pid):
    if not pid:
        return False
    try:
        if IS_WIN:      # không dùng os.kill(pid, 0) trên Windows (sẽ dừng luôn tiến trình)
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                               text=True, creationflags=NO_WINDOW, timeout=15)
            return f'"{pid}"' in (r.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _take_lock(d, n):
    """Giữ 1 trong n khoá ở thư mục d (chờ tới khi có chỗ); tự trả khi tiến trình thoát; khoá của tiến trình đã chết được thu hồi."""
    import atexit
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    while True:
        for i in range(n):
            f = d / f"{i}.lock"
            try:
                fd = os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                atexit.register(lambda f=f: f.unlink(missing_ok=True))
                return f
            except FileExistsError:
                try:
                    pid = int(f.read_text() or 0)
                except Exception:
                    pid = 0
                if pid != os.getpid() and not pid_alive(pid):
                    f.unlink(missing_ok=True)
            except OSError:
                return None
        time.sleep(3)


def worker_slot():
    """Giới hạn CỨNG số worker chạy cùng lúc trên máy (app tự ép, không dựa vào hướng dẫn cho AI).
    Mỗi worker giữ 1 file khoá data/slots/<i>.lock; hết chỗ thì chờ; worker bị tắt ngang -> khoá cũ tự được thu hồi."""
    import atexit
    if os.environ.get("AIO_SOLO") == "1":       # Eco / Free / máy yếu: 1 worker một lúc trong task (ép bằng mã, không viết thành luật)
        _take_lock(Path.cwd() / ".slots", 1)
    d = DATA / "slots"
    d.mkdir(parents=True, exist_ok=True)
    n = max(1, MAX_RUNNING * MAX_WORKERS, MAX_PER_TASK)
    checked = {}                                # kiểm tra khoá cũ tối đa 20 giây/lần (tasklist chậm)
    while True:
        for i in range(n):
            f = d / f"{i}.lock"
            try:
                fd = os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                atexit.register(lambda f=f: f.unlink(missing_ok=True))
                return f
            except FileExistsError:
                try:
                    pid = int(f.read_text() or 0)
                except Exception:
                    pid = 0
                if time.time() - checked.get(i, 0) > 20:
                    checked[i] = time.time()
                    if pid != os.getpid() and not pid_alive(pid):
                        f.unlink(missing_ok=True)
            except OSError:
                return None                     # không tạo được khoá (quyền/ổ đĩa): không chặn worker
        time.sleep(3)


def kill_workers(t):
    """Hủy task: dừng cả worker ChatGPT/Gemini đang chạy tách rời (ghi pid trong work/<ID>/vN/status.json)."""
    w = wdir(t) / "work"
    if not w.exists():
        return
    for f in w.glob("*/v*/status.json"):
        st = rjson(f, {}) or {}
        if st.get("status") == "running" and st.get("pid"):
            kill_pid_tree(int(st["pid"]))
            try:
                wjson(f, {**st, "status": "stopped", "error": "Đã hủy task"})
            except Exception:
                pass


def kill_orphan(pid):
    """Tiến trình claude cũ còn sót khi app bị tắt đột ngột: dừng trước khi chạy tiếp."""
    if not pid:
        return
    if not IS_WIN:
        try:
            r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10)
            if any(k in (r.stdout or "").lower() for k in ("claude", "codex")):
                os.kill(pid, 9)
        except Exception:
            pass
        return
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                       text=True, creationflags=NO_WINDOW)
    if any(k in (r.stdout or "").lower() for k in ("claude", "codex")):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)


PS_OPEN_FOLDER = r"""
$p = $env:AIO_OPEN.TrimEnd('\')
$sel = $env:AIO_SELECT
Add-Type -Namespace AIO -Name W -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h); [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);'
function Find { foreach ($x in (New-Object -ComObject Shell.Application).Windows()) { try { if ($x.Document.Folder.Self.Path.TrimEnd('\') -ieq $p) { return $x } } catch {} } return $null }
$w = Find
if ($w -and $sel) { try { $w.Document.SelectItem($w.Document.Folder.ParseName((Split-Path $sel -Leaf)), 29) } catch {} }
if (-not $w) {
  if ($sel) { Start-Process explorer.exe -ArgumentList ('/select,"' + $sel + '"') } else { Start-Process explorer.exe -ArgumentList ('"' + $p + '"') }
  for ($i = 0; $i -lt 30 -and -not $w; $i++) { Start-Sleep -Milliseconds 150; $w = Find }
}
if ($w) {
  $h = [IntPtr]$w.HWND
  if ([AIO.W]::IsIconic($h)) { [void][AIO.W]::ShowWindow($h, 9) }
  (New-Object -ComObject WScript.Shell).SendKeys('%')
  [void][AIO.W]::BringWindowToTop($h)
  [void][AIO.W]::SetForegroundWindow($h)
}
"""


def open_path(p, select=""):
    p = str(p)
    if IS_WIN and os.path.isdir(p):      # thư mục đang mở sẵn -> đưa lên trên cùng; mở mới cũng đưa lên trên cùng
        try:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS_OPEN_FOLDER],
                             env=dict(os.environ, AIO_OPEN=p, AIO_SELECT=select), creationflags=NO_WINDOW)
            return
        except Exception:
            pass
    if IS_MAC:                            # Finder: mở và đưa lên trên cùng; select -> chọn sẵn file
        subprocess.Popen(["open", "-R", select] if select else ["open", p])
        return
    if IS_WIN:
        os.startfile(p)  # noqa
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", p])


def refresh_path():
    """Lấy PATH mới nhất từ registry (CLI vừa cài xong vẫn tìm thấy) + tìm Node.js ở các nơi hay cài
    (Program Files, nvm, volta, fnm, scoop...) để Codex (chạy bằng node) luôn chạy được."""
    if IS_MAC:     # app mở từ Launchpad/đăng nhập máy có PATH rất ngắn -> thêm chỗ Homebrew, npm, Claude, agy hay cài
        h = str(Path.home())
        extra = ["/opt/homebrew/bin", "/usr/local/bin", h + "/.local/bin", h + "/.npm-global/bin", h + "/.agy/bin",
                 h + "/.local/share/agy/bin", "/opt/homebrew/opt/node/bin"]
        cur = os.environ.get("PATH", "").split(":")
        os.environ["PATH"] = ":".join([x for x in extra if os.path.isdir(x) and x not in cur] + cur)
        return
    if not IS_WIN:
        return
    import glob
    try:
        import winreg
        vals = {}
        for root, key in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")]:
            try:
                with winreg.OpenKey(root, key) as k:
                    for n in range(winreg.QueryInfoKey(k)[1]):
                        name, v, _ = winreg.EnumValue(k, n)
                        if isinstance(v, str):
                            vals.setdefault(name.upper(), []).append(v)
            except OSError:
                pass
        for name, vs in vals.items():          # biến như NVM_SYMLINK, NVM_HOME phải có trước khi mở rộng PATH
            if name != "PATH" and name not in (k.upper() for k in os.environ):
                os.environ[name] = os.path.expandvars(vs[-1])
        parts = [os.path.expandvars(v) for v in vals.get("PATH", [])]
        if parts:
            os.environ["PATH"] = ";".join(parts + [os.environ.get("PATH", "")])
    except Exception:
        pass
    # Bỏ trùng: mỗi lần tự cập nhật app khởi động lại với PATH cũ + PATH registry -> PATH phình to qua nhiều bản,
    # vượt giới hạn của cmd.exe (~8 KB) nên các .cmd (codex) không thấy node. Giữ thứ tự, bỏ mục trùng/rỗng.
    seen, keep = set(), []
    for x in os.environ.get("PATH", "").split(";"):
        k = x.strip().strip('"').rstrip("\\").lower()
        if k and k not in seen:
            seen.add(k)
            keep.append(x.strip())
    os.environ["PATH"] = ";".join(keep)
    if shutil.which("node"):
        return
    E = os.environ.get
    cands = [os.path.join(E("ProgramFiles", r"C:\Program Files"), "nodejs"), os.path.join(E("ProgramFiles(x86)", ""), "nodejs"),
             os.path.join(E("LOCALAPPDATA", ""), "Programs", "nodejs"), E("NVM_SYMLINK", ""), os.path.join(E("LOCALAPPDATA", ""), "Volta", "bin"),
             os.path.join(E("ProgramFiles", ""), "Volta"), os.path.join(E("USERPROFILE", ""), "scoop", "apps", "nodejs", "current"),
             os.path.join(E("USERPROFILE", ""), "scoop", "apps", "nodejs-lts", "current"), r"C:\ProgramData\chocolatey\bin"]
    cands += sorted(glob.glob(os.path.join(E("APPDATA", ""), "nvm", "v*")), reverse=True)
    cands += sorted(glob.glob(os.path.join(E("LOCALAPPDATA", ""), "fnm_multishells", "*")), reverse=True)[:1]
    try:
        cands.insert(0, (HOME / "node_dir.txt").read_text("utf-8-sig").strip())     # file cài ghi lại chỗ node
    except Exception:
        pass
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "node.exe")):
            os.environ["PATH"] = d + ";" + os.environ.get("PATH", "")
            return


LIBS = {"PIL": "pillow", "openpyxl": "openpyxl", "docx": "python-docx", "pptx": "python-pptx",
        **({"winpty": "pywinpty"} if IS_WIN else {})}     # đọc/sửa ảnh, Excel, Word, PowerPoint; terminal ảo (đăng nhập)
LIBS_READY = threading.Event()


def ensure_libs():
    """Mở app: cài thư viện Python còn thiếu (bản cập nhật qua nút Cập nhật không chạy lại bộ cài). Tối đa 1 lần/ngày."""
    import importlib.util
    try:
        miss = [pkg for mod, pkg in LIBS.items() if importlib.util.find_spec(mod) is None]
        last = (CFG or {}).get("libs_at", 0)
        if not miss or time.time() - last < 86400:
            return
        CFG["libs_at"] = int(time.time())
        save_cfg()
        base = [py_console(), "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check"]
        for extra in ([], ["--break-system-packages"]) if IS_MAC else ([],):
            r = subprocess.run(base + extra + miss, capture_output=True, text=True, errors="replace", timeout=600,
                               creationflags=NO_WINDOW)
            dlog(f"pip install {miss} -> exit {r.returncode}\n{(r.stderr or '')[-1200:]}")
            if r.returncode == 0:
                break
        import site
        importlib.invalidate_caches()
        if site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())
    except Exception as e:
        dlog(f"ensure_libs: {e}")
    finally:
        LIBS_READY.set()


def _run_hidden(args, timeout, cwd=None):
    return subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)


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

    def __init__(self, args, cwd, env=None):
        self.buf = ""
        self.pid = None
        if IS_WIN:
            LIBS_READY.wait(300)     # thư viện terminal ảo do bộ cài (hoặc ensure_libs lúc mở app) cài
            from winpty import PtyProcess
            self.p = PtyProcess.spawn([str(x) for x in args], cwd=cwd, env=env, dimensions=(50, 500))
            self.pid = self.p.pid
            self._read = lambda: self.p.read(4096)
            self._write = self.p.write
            self.alive = self.p.isalive
        else:
            import pty
            m, s = pty.openpty()
            self.sp = subprocess.Popen(args, cwd=cwd, env=env, stdin=s, stdout=s, stderr=s, close_fds=True, start_new_session=True)
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


def run_limited(cmd, cwd, timeout, env=None):
    p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW, env=env)
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
