# Maestro — features/memory.py: Bộ nhớ dùng chung mọi task: cách làm việc + lịch sử của người dùng.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.
#
# Tự cập nhật SAU khi 1 lượt làm xong (luồng nền, không làm chậm task), bằng model nhỏ (haiku).
# Chỉ nằm trên máy người dùng (thư mục _AI-Orchestrator cạnh lịch sử task), được nhúng vào hướng dẫn của mọi task sau.
# Người dùng xem/sửa/xoá trong Cài đặt > Memory.

ASK_FMT_RE = re.compile(r"\bA\s*/\s*B\b|A-B-C|A/B/C", re.I)
MEM_STYLE_MAX = 700                  # cách làm việc chung: ngắn, áp dụng cho MỌI task (không làm dài hướng dẫn)
MEM_HIST_MAX = 800                   # lịch sử: riêng từng dự án, chỉ nhúng vào task của dự án đó
_mlock = threading.Lock()
MEM_PROMPT = """Bạn giữ BỘ NHỚ về người dùng của Maestro (app giao việc cho AI).

Cách làm việc chung (áp dụng cho MỌI loại task):
<<<
{style}
>>>
Lịch sử của dự án "{proj}":
<<<
{hist}
>>>

Lượt làm việc vừa xong (task "{title}", chế độ {mode}):
<<<
{turn}
>>>

Viết lại cả 2 mục, giữ điều còn đúng, thêm điều mới học được, bỏ điều lỗi thời hoặc bị người dùng phủ nhận. Tiếng Việt, gạch đầu dòng ngắn.
## Cách làm việc
(dưới {smax} ký tự. CHỈ điều đúng với MỌI loại task: xưng hô, độ dài/kiểu trả lời, điều người dùng hay sửa/không thích.
KHÔNG ghi quy trình riêng của 1 loại việc hay 1 dự án — vd phát hành, chạy test, đặt thư mục, cách vẽ asset — những thứ đó ghi ở Lịch sử.
KHÔNG ghi quy định bắt buộc về cách đặt câu hỏi / lựa chọn A-B-C: app tự quyết theo từng task.)
## Lịch sử
(dưới {hmax} ký tự, tối đa 10 dòng, mới nhất ở đầu: việc đã làm/đang dở, quyết định đã chốt, quy trình riêng của dự án này)
KHÔNG ghi mật khẩu, mã, key, token, số điện thoại, email, số tài khoản hay nội dung bí mật. Không có gì đáng nhớ thì trả lại như cũ.
Chỉ trả về 2 mục trên, bắt đầu bằng "## Cách làm việc", không giải thích."""


def mem_file():
    return STORE / "memory_auto.md"


def mem_hist_file(pid):
    return STORE / "memory_projects" / f"{safe_name(pid or '_chung', 60)}.md"


def _read(f, n):
    try:
        return f.read_text(encoding="utf-8").strip()[:n]
    except Exception:
        return ""


def mem_text():
    """Cách làm việc chung (bản cũ có cả mục Lịch sử trong cùng file: chỉ lấy phần trước Lịch sử)."""
    s = _read(mem_file(), 6000).split("## Lịch sử")[0]
    s = "\n".join(x for x in s.splitlines() if not ASK_FMT_RE.search(x))   # cách hỏi A/B: theo task, không phải luật chung
    return s.strip()[:4000]     # người dùng tự sửa được dài hơn; AI tự ghi thì giới hạn MEM_STYLE_MAX


def mem_block(t):
    """Phần bộ nhớ nhúng vào hướng dẫn của task: cách làm việc chung + lịch sử của đúng dự án của task."""
    style, hist = mem_text(), _read(mem_hist_file(t.get("project")), MEM_HIST_MAX + 300)
    if not style and not hist:
        return ""
    return ("## Về người dùng — Maestro tự ghi nhớ (tham khảo; hướng dẫn ở trên và yêu cầu hiện tại ưu tiên hơn)\n" + style
            + (("\n### Lịch sử dự án này\n" + hist.replace("## Lịch sử", "").strip()) if hist else "") + "\n\n")


def _atomic(f, text):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.tmp")
    tmp.write_text(text + ("\n" if text else ""), encoding="utf-8")
    os.replace(tmp, f)


def mem_save(text, pid=None, hist=None):
    """Lưu cách làm việc chung (text) và/hoặc lịch sử dự án (hist). Người dùng sửa trong Cài đặt: chỉ phần chung."""
    with _mlock:
        if text is not None:
            _atomic(mem_file(), str(text or "").replace("\r\n", "\n").strip()[:4000])
        if hist is not None:
            _atomic(mem_hist_file(pid), str(hist or "").replace("\r\n", "\n").strip()[:MEM_HIST_MAX + 300])


MEM_EVERY = 5                        # cập nhật bộ nhớ: lượt đầu của task, rồi sau mỗi 5 lượt (không phải mỗi lượt)


def mem_turn(t, since=None):
    """Đoạn hội thoại để rút bộ nhớ. since=None: chỉ lượt vừa xong; since=i: mọi tin từ vị trí i (tối đa ~6.000 ký tự)."""
    ms = t.get("messages") or []
    if since is not None:
        seg = [m for m in ms[since:] if m.get("role") in ("user", "claude") and m.get("kind") != "error" and (m.get("text") or "").strip()]
        if not any(m["role"] == "user" for m in seg):
            return ""
        out = "\n\n".join(("NGƯỜI DÙNG: " if m["role"] == "user" else "AI TRẢ LỜI: ") + m["text"][:1500 if m["role"] == "user" else 1200]
                           for m in seg)
        return out[-6000:]
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
        turn = mem_turn(t, t.get("mem_at", 0))
        if not exe or not turn or not claude_usable():   # không có Claude trả phí: không tốn hạn mức ChatGPT
            return
        pid = t.get("project")
        pj = project_of(t)
        old_hist = _read(mem_hist_file(pid), 4000)
        if not old_hist and not pid:                     # bản cũ: lịch sử nằm chung file -> chuyển sang "_chung"
            old = _read(mem_file(), 6000)
            old_hist = old.split("## Lịch sử", 1)[1].strip() if "## Lịch sử" in old else ""
        prompt = MEM_PROMPT.format(style=mem_text() or "(trống)", proj=(pj or {}).get("name") or "chung",
                                   hist=old_hist.replace("## Lịch sử", "").strip() or "(trống)", title=t.get("title", ""),
                                   mode=t.get("mode", ""), turn=turn, smax=MEM_STYLE_MAX, hmax=MEM_HIST_MAX)
        p = subprocess.run([exe, "-p", prompt, "--model", "haiku"], cwd=str(STORE), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           creationflags=NO_WINDOW | BELOW_NORMAL)
        out = (p.stdout or "").strip()
        if p.returncode != 0 or "## Cách làm việc" not in out:
            dlog(f"mem_update {t.get('id')}: rc={p.returncode} {(p.stderr or out)[-300:]}")
            return
        out = out[out.index("## Cách làm việc"):]
        style, _, hist = out.partition("## Lịch sử")
        mem_save(style.strip()[:MEM_STYLE_MAX + 300], pid, ("## Lịch sử\n" + hist.strip()) if hist.strip() else None)
    except Exception as e:
        dlog(f"mem_update {t.get('id')}: {e}")


def mem_maybe(t):
    """Chỉ cập nhật bộ nhớ ở lượt đầu của task và sau mỗi MEM_EVERY lượt (đỡ tốn lượt gọi, hướng dẫn ít đổi)."""
    ms = t.get("messages") or []
    at = t.get("mem_at")
    turns = sum(1 for m in ms[at or 0:] if m.get("role") == "work")
    if at is not None and turns < MEM_EVERY:
        return
    mem_update(t)
    t["mem_at"] = len(ms)
    try:
        tsave(t)
    except Exception:
        pass


def after_turn(t, wi=None):
    """Sau khi 1 lượt xong: dọn nháp trước, rồi mới (nếu tới lượt) cập nhật bộ nhớ (cả hai chạy nền)."""
    finish_clean(t, wi)
    mem_maybe(t)
