"""Ghép các module của Maestro thành 1 chương trình (dùng cho cả chạy thử lẫn build).

Mọi module chạy chung 1 không gian tên, đúng như bản 1 file gửi người dùng, nên không import chéo.
Thứ tự dưới đây là thứ tự nạp: module sau được dùng tên của module trước ngay lúc nạp
(hằng số, lớp kế thừa...). Trong thân hàm thì gọi tên ở module nào cũng được.
Kiểm tra thứ tự + so khớp: python -m unittest discover -s src/tests
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent
PARTS = [
    "core/config.py", "core/procs.py", "core/store.py", "core/tasks.py", "core/router.py", "core/prompts.py",
    "core/models.py", "core/usage.py", "core/scheduler.py",
    "providers/accounts.py", "providers/claude.py", "providers/codex.py", "providers/gemini.py",
    "workers/worker.py", "workers/check.py", "workers/judge.py",
    "features/brain.py", "features/codemode.py", "features/updater.py", "features/diagnostics.py", "features/system.py", "features/memory.py",
    "web/server.py", "features/remote.py",
]
ENTRY_MARK = "# ==== ĐIỂM VÀO"


def entry(src=SRC):
    t = (src / "app.py").read_text(encoding="utf-8")
    return t[t.index(ENTRY_MARK):]


def assemble(src=SRC):
    """Toàn bộ chương trình thành 1 đoạn mã (build.py nhúng giao diện/font/icon vào rồi ghi dist/app.py)."""
    return "\n\n".join((src / p).read_text(encoding="utf-8").rstrip("\n") for p in PARTS) + "\n\n\n" + entry(src)
