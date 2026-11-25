"""Phát hành bản mới: tăng số bản (+0.1), build, chép file phát hành ra gốc repo.
Dùng:  python src/release.py          (tăng 0.1)
       python src/release.py 8.0      (đặt số bản cụ thể)
Sau đó: git add -A && git commit -m "vX.Y: ..." && git push
"""
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
rel_f = SRC / "RELEASE"
cur = rel_f.read_text(encoding="utf-8").strip()
new = sys.argv[1] if len(sys.argv) > 1 else f"{float(cur) + 0.1:.1f}"
rel_f.write_text(new + "\n", encoding="utf-8")
print(f"RELEASE {cur} -> {new}")

r = subprocess.run([sys.executable, str(SRC / "build.py")], cwd=str(SRC))
if r.returncode != 0:
    rel_f.write_text(cur + "\n", encoding="utf-8")
    sys.exit("Build lỗi (đã trả lại số bản cũ). Xem thông báo phía trên.")

dist = SRC / "dist"
for old in ROOT.glob("Maestro_v*.bat"):
    old.unlink()
for name in ("app.py", "latest.json", f"Maestro_v{new}.bat", "Maestro_mac.zip"):
    shutil.copy2(dist / name, ROOT / name)
shutil.copy2(SRC / "uninstall.bat", ROOT / "Maestro_Uninstall.bat")
shutil.copy2(dist / f"Maestro_v{new}.bat", dist / "Maestro_setup.bat")
print(f"Đã chép ra gốc repo. File gửi người dùng: src/dist/Maestro_setup.bat (bản v{new})")
