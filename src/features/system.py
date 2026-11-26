# Maestro — features/system.py: Tự mở khi bật máy, thoát app.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.maestro.app.plist"


def autostart_on():
    if IS_MAC:
        return MAC_AGENT.exists()
    if IS_WIN:
        return (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Maestro.lnk").exists()
    return False


def set_autostart(on):
    """Bật/tắt: tự chạy nền khi đăng nhập máy (task dở tự chạy tiếp)."""
    boot_py = HOME / "app.py"
    if IS_MAC:
        if on:
            MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
            MAC_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.maestro.app</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{boot_py}</string><string>--background</string></array>
<key>RunAtLoad</key><true/>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get("PATH", "")}</string></dict>
<key>StandardOutPath</key><string>{DATA / "launchd.log"}</string><key>StandardErrorPath</key><string>{DATA / "launchd.log"}</string>
</dict></plist>
""", encoding="utf-8")
        else:
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.maestro.app"], capture_output=True)
            MAC_AGENT.unlink(missing_ok=True)
        return {"ok": True}
    if IS_WIN:
        lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Maestro.lnk"
        if on:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{pyw if pyw.exists() else sys.executable}';"
                  f"$s.Arguments='\"{boot_py}\" --background';$s.WorkingDirectory='{HOME}';$s.IconLocation='{HOME / 'icon.ico'}';$s.Save()")
            _run_hidden(["powershell", "-NoProfile", "-Command", ps], 30)
        else:
            lnk.unlink(missing_ok=True)
        return {"ok": True}
    return {"error": "Chưa hỗ trợ trên hệ điều hành này"}


def quit_app():
    """Thoát Maestro: task đang chạy giữ trạng thái để lần mở sau tự chạy tiếp."""
    if CFG.get("remote_on"):                  # thoát hẳn: tắt link điện thoại (mở lại app sẽ tự tạo link mới)
        remote_stop()
    for t in [t for t in TASKS.values() if t["status"] == "running"]:
        t["_quit"] = True
        kill_tree(PROCS.get(t["id"]))
    time.sleep(1.5)
    os._exit(0)
