# Maestro — điểm vào. Khi phát triển: nạp lần lượt các module trong assemble.PARTS vào chung 1 không gian tên
# (giống hệt bản 1 file mà build.py ghép ra cho người dùng), rồi phân lệnh: serve / worker / wait / check / judge.
#   python src/app.py                 chạy app (AIO_HOME, AIO_PORT, AIO_NO_BROWSER để thử)
#   python src/app.py worker codex|gemini | wait | check | judge ...   lệnh con Claude gọi
import sys as _sys
from pathlib import Path as _Path

_SRC = _Path(__file__).resolve().parent
_sys.path.insert(0, str(_SRC))
import assemble as _asm  # noqa: E402

for _p in _asm.PARTS:
    exec(compile((_SRC / _p).read_text(encoding="utf-8"), str(_SRC / _p), "exec"))

# ==== ĐIỂM VÀO (build.py lấy từ dòng này trở xuống, nối sau các module) ====
if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if len(sys.argv) > 1 and sys.argv[1] == "wait":
        cmd_wait(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        cmd_worker(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "check":
        cmd_check(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "judge":
        cmd_judge(sys.argv[2:])
    else:
        if not os.environ.get("AIO_CHILD") and APP.parent == HOME and VERSION != "dev":
            r = boot(sys.argv[1:])
            if r is not None:
                if r is False and "--background" not in sys.argv:
                    webbrowser.open(app_url(running_port()))
                sys.exit(0)
            os.environ["AIO_RESTART"] = "1"
        serve(background="--background" in sys.argv)
