import base64
import hashlib
import json
from pathlib import Path

d = Path(__file__).parent
MSG = {
    "title": "Maestro — cài đặt",
    "mode_fresh": "Cài mới, khoảng 5–15 phút. Không tắt cửa sổ này.",
    "mode_update": "Đã cài trước đó → đang cập nhật. Dữ liệu và task giữ nguyên.",
    "uac": "Nếu Windows hỏi quyền cài đặt: bấm Yes.",
    "opening": "Đang mở Maestro…",
    "have": "đã có",
    "installing": "đang cài…",
    "done": "xong",
    "failed": "lỗi",
    "libs": "Thư viện kiểm tra file",
    "app": "Ứng dụng",
    "no_winget": "không có, sẽ tải bộ cài trực tiếp",
    "no_python": "Cài Python chưa xong. Khởi động lại máy rồi chạy lại file này.",
    "no_claude": "Cài Claude Code chưa xong. Kiểm tra mạng rồi chạy lại file này.",
    "no_start": "Ứng dụng không khởi động được.",
    "log": "Chi tiết:",
    "ok": "Xong. Trang Maestro đã mở trong trình duyệt. Lưu bookmark link dưới đây — app tự chạy nền khi bật máy nên link luôn mở được:",
}
import unicodedata as _u
# Cửa sổ cmd hay lỗi font tiếng Việt có dấu -> dùng tiếng Việt không dấu
MSG = {k: _u.normalize("NFKD", v.replace("đ", "d").replace("Đ", "D").replace("–", "-").replace("→", "->").replace("—", "-")).encode("ascii", "ignore").decode().replace("…", "...")
       for k, v in MSG.items()}
import sys as _sys0
_sys0.path.insert(0, str(d))
from assemble import assemble          # ghép các module (core/providers/workers/features/web) + điểm vào -> 1 file
app = assemble(d)
# giao diện: chuẩn hoá xuống dòng LF để bản build không phụ thuộc máy build (checkout CRLF trên Windows)
ui = base64.b64encode((d / "web" / "ui.html").read_bytes().replace(b"\r\n", b"\n")).decode()
assert 'UI_B64 = ""' in app
import os
built = app.replace('UI_B64 = ""', f'UI_B64 = "{ui}"', 1)
# bài học chung đã duyệt + nơi nhận bài học (Google Form) -> nhúng vào app (cập nhật hàng tuần qua bản mới)
_lm = (d / "learnings.md").read_text(encoding="utf-8") if (d / "learnings.md").exists() else ""
_lf = (d / "learn_form.json").read_text(encoding="utf-8") if (d / "learn_form.json").exists() else ""
assert 'LEARN_MD = ""' in built and 'LEARN_FORM = ""' in built
built = built.replace('LEARN_MD = ""', "LEARN_MD = " + json.dumps(_lm), 1).replace('LEARN_FORM = ""', "LEARN_FORM = " + json.dumps(_lf), 1)
_fonts = {f.name: base64.b64encode(f.read_bytes()).decode() for f in sorted((d / "fonts").glob("*.woff2"))}
assert 'FONTS_B64 = ""' in built and len(_fonts) == 2, "Thiếu font Geist trong src/fonts"
built = built.replace('FONTS_B64 = ""', "FONTS_B64 = " + json.dumps(json.dumps(_fonts)), 1)
built = built.replace('ICO_B64 = ""', 'ICO_B64 = "' + base64.b64encode((d / "icon.ico").read_bytes()).decode() + '"', 1)
import datetime
ver = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d.%H%M") + "-" + hashlib.sha1(built.encode()).hexdigest()[:6]  # so sánh được theo thời gian
repo = os.environ.get("AIO_REPO", "minhhoang140800/ai-orchestrator")          # owner/repo trên GitHub
base = f"https://raw.githubusercontent.com/{repo}/main/" if repo else ""
rel = (d / "RELEASE").read_text().strip()          # số phiên bản hiển thị, tăng mỗi lần phát hành
built = built.replace('RELEASE = "dev"', f'RELEASE = "{rel}"', 1)
built = built.replace('VERSION = "dev"', f'VERSION = "{ver}"', 1).replace('UPDATE_BASE = ""', f'UPDATE_BASE = "{base}"', 1)
(d / "dist").mkdir(exist_ok=True)
(d / "dist" / "app.py").write_bytes(built.encode("utf-8"))
# ---- kiểm tra trước khi phát hành: thiếu địa chỉ cập nhật / lỗi cú pháp / không khởi động được -> dừng
assert base, "Thiếu địa chỉ cập nhật (UPDATE_BASE)"
import py_compile, subprocess, tempfile, time, urllib.request, sys as _s
py_compile.compile(str(d / "dist" / "app.py"), doraise=True)
_h = tempfile.mkdtemp()
_p = subprocess.Popen([_s.executable, str(d / "dist" / "app.py"), "--background"],
                      env=dict(os.environ, AIO_HOME=_h, AIO_PORT="18765", AIO_NO_BROWSER="1", AIO_CHILD="1"),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
_ok = False
for _ in range(40):
    time.sleep(0.5)
    try:
        _ok = b"<svg" in urllib.request.urlopen("http://127.0.0.1:18765/icon.svg", timeout=2).read()[:200] and \
              b"<" in urllib.request.urlopen("http://127.0.0.1:18765/", timeout=3).read()[:50]
        if _ok:
            break
    except Exception:
        pass
_p.kill()
assert _ok, "Bản build không khởi động được: " + (_p.stderr.read() or b"").decode(errors="replace")[-800:]
(d / "dist" / "latest.json").write_text(json.dumps({"version": ver, "sha256": hashlib.sha256(built.encode("utf-8")).hexdigest()}), encoding="utf-8")
ps = (d / "install.tpl.ps1").read_text(encoding="utf-8")
ps = (ps.replace("__APP_B64__", base64.b64encode(built.encode("utf-8")).decode())
        .replace("__ICO_B64__", base64.b64encode((d / "icon.ico").read_bytes()).decode())
        .replace("__VER__", ver)
        .replace("__MSG_B64__", base64.b64encode(json.dumps(MSG, ensure_ascii=False).encode("utf-8")).decode()))

# Chuyển các comment tiếng Việt trong phần PowerShell về ASCII (file .bat phải là ASCII)
import unicodedata
ps = "\n".join(unicodedata.normalize("NFKD", l.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
               if l.lstrip().startswith("#") else l for l in ps.splitlines())
ps.encode("ascii")

bat = r"""@echo off
chcp 65001 >nul
title Maestro v""" + rel + r"""
set "AIO_SELF=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:AIO_SELF); $m='#'+'##PS-BEGIN###'; $i=$s.IndexOf($m); if ($i -lt 0) { Write-Host 'File loi'; exit 1 }; Invoke-Expression $s.Substring($i); exit $LASTEXITCODE"
if errorlevel 1 (
  echo.
  echo Co loi. Chup man hinh cua so nay gui lai. Log: %LOCALAPPDATA%\AIOrchestrator\install.log
  pause
)
exit /b
###PS-BEGIN###
""" + ps + "\n"
for old in (d / "dist").glob("*.bat"):
    old.unlink()
out = d / "dist" / f"Maestro_v{rel}.bat"
out.write_bytes(bat.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
print(out, out.stat().st_size, ver)

# ---- bản Mac: Maestro_setup.command (đóng zip để giữ quyền chạy khi tải về)
import zipfile
sh = (d / "install_mac.tpl.sh").read_text(encoding="utf-8")
sh = (sh.replace("__APP_B64__", base64.b64encode(built.encode("utf-8")).decode())
        .replace("__PNG_B64__", base64.b64encode((d / "icon1024.png").read_bytes()).decode())
        .replace("__VER__", ver).replace("__REL__", "v" + rel))
cmdf = d / "dist" / "Maestro_setup.command"
cmdf.write_bytes(sh.encode("utf-8"))
os.chmod(cmdf, 0o755)
zp = d / "dist" / "Maestro_mac.zip"
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    zi = zipfile.ZipInfo("Maestro_setup.command")
    zi.external_attr = (0o100755 & 0xFFFF) << 16
    zi.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(zi, sh.encode("utf-8"))
print(zp, zp.stat().st_size)
