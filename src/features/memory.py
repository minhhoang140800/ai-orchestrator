# Maestro — features/memory.py: Bộ nhớ dùng chung mọi task: cách làm việc + lịch sử của người dùng.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.
#
# Tự cập nhật SAU khi 1 lượt làm xong (luồng nền, không làm chậm task), bằng model nhỏ (haiku).
# Chỉ nằm trên máy người dùng (thư mục _AI-Orchestrator cạnh lịch sử task), được nhúng vào hướng dẫn của mọi task sau.
# Người dùng xem/sửa/xoá trong Cài đặt > Memory.

MEM_MAX = 2500                       # ký tự tối đa (giữ hướng dẫn gọn -> không chậm)
_mlock = threading.Lock()
MEM_PROMPT = """Bạn giữ BỘ NHỚ về người dùng của Maestro (app giao việc cho AI). Bộ nhớ hiện tại:
<<<
{mem}
>>>

Lượt làm việc vừa xong (task "{title}", chế độ {mode}):
<<<
{turn}
>>>

Viết lại TOÀN BỘ bộ nhớ, giữ điều còn đúng, thêm điều mới học được, bỏ điều đã lỗi thời hoặc bị người dùng phủ nhận.
Đúng 2 mục, gạch đầu dòng ngắn, tiếng Việt, tổng dưới {max} ký tự:
## Cách làm việc
(sở thích, cách xưng hô, độ dài/kiểu trả lời, quy tắc người dùng đặt ra, điều người dùng hay sửa/không thích — chỉ điều áp dụng chung cho nhiều task)
## Lịch sử
(tối đa 15 dòng, mới nhất ở đầu: dự án/sản phẩm đang làm, quyết định đã chốt, việc dở dang — mỗi dòng 1 ý, có tên dự án nếu có)
KHÔNG ghi mật khẩu, mã, key, token, số điện thoại, email, số tài khoản hay nội dung bí mật. Không có gì đáng nhớ thì trả lại bộ nhớ cũ.
Chỉ trả về nội dung bộ nhớ, bắt đầu bằng "## Cách làm việc", không giải thích."""


def mem_file():
    return STORE / "memory_auto.md"


def mem_text():
    try:
        return mem_file().read_text(encoding="utf-8").strip()[:MEM_MAX + 500]
    except Exception:
        return ""


def mem_save(text):
    text = str(text or "").replace("\r\n", "\n").strip()[:MEM_MAX + 500]
    with _mlock:
        STORE.mkdir(parents=True, exist_ok=True)
        tmp = mem_file().with_name(f"memory_auto.{os.getpid()}.tmp")
        tmp.write_text(text + ("\n" if text else ""), encoding="utf-8")
        os.replace(tmp, mem_file())


def mem_turn(t):
    """Lượt vừa xong: tin nhắn người dùng từ câu trả lời trước tới giờ + câu trả lời cuối."""
    ms = t.get("messages") or []
    last = max((i for i, m in enumerate(ms) if m.get("role") == "claude" and m.get("kind") != "error"), default=-1)
    if last < 0:
        return ""
    prev = max((i for i, m in enumerate(ms[:last]) if m.get("role") == "claude"), default=-1)
    users = [m.get("text") or "" for m in ms[prev + 1:last] if m.get("role") == "user"]
    return ("NGƯỜI DÙNG:\n" + "\n---\n".join(u[:2500] for u in users) + "\n\nAI TRẢ LỜI:\n" + (ms[last].get("text") or "")[:2500]) if users else ""


def mem_update(t):
    """Gọi sau khi lượt làm xong (luồng nền). Lỗi thì bỏ qua, giữ bộ nhớ cũ."""
    try:
        exe = find_bin("claude")
        turn = mem_turn(t)
        if not exe or not turn or not claude_usable():   # không có Claude trả phí: không tốn hạn mức ChatGPT
            return
        prompt = MEM_PROMPT.format(mem=mem_text() or "(trống)", title=t.get("title", ""), mode=t.get("mode", ""),
                                   turn=turn, max=MEM_MAX)
        p = subprocess.run([exe, "-p", prompt, "--model", "haiku"], cwd=str(STORE), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           creationflags=NO_WINDOW | BELOW_NORMAL)
        out = (p.stdout or "").strip()
        if p.returncode != 0 or "## Cách làm việc" not in out:
            dlog(f"mem_update {t.get('id')}: rc={p.returncode} {(p.stderr or out)[-300:]}")
            return
        mem_save(out[out.index("## Cách làm việc"):][:MEM_MAX + 500])
    except Exception as e:
        dlog(f"mem_update {t.get('id')}: {e}")


def after_turn(t, wi=None):
    """Sau khi 1 lượt xong: dọn nháp trước, rồi mới cập nhật bộ nhớ (cả hai chạy nền)."""
    finish_clean(t, wi)
    mem_update(t)
