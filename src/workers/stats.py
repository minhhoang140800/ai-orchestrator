# Maestro — workers/stats.py: App tự học chọn worker: ghi kết quả từng việc (làm được không, mất bao lâu) theo loại việc,
# rồi gợi ý / tự giao bên phù hợp hơn. Thay luật cố định "Gemini trước".
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

WSTATS_F = "worker_stats.json"
WSTATS_KEEP = 30                     # mỗi (worker, loại việc) giữ 30 lần gần nhất
WSTATS_RETRY = 24 * 3600            # bên bị bỏ qua vì làm kém: sau 24 giờ cho thử lại 1 lần
WSTATS_MIN = 3                       # ít hơn 3 lần thì chưa đủ dữ liệu -> dùng mặc định
KIND_NAMES = {"image": "Ảnh", "edit": "Sửa file / Excel / Word", "research": "Research web", "text": "Viết / idea / phân tích"}
WN_ = {"codex": "GPT", "gemini": "Gemini"}
DEFAULT_PICK = {"image": "codex", "edit": "codex", "research": "gemini", "text": "codex"}   # khi chưa có dữ liệu
RESEARCH_RE = re.compile(r"research|nghiên cứu|tra cứu|tìm hiểu|tìm (?:trên )?(?:web|mạng)|so sánh|đối thủ|thị trường|"
                         r"google play|app store|xu hướng|trend", re.I)


def task_kind(want_img, edit, drive, text):
    if want_img:
        return "image"
    if edit or drive:
        return "edit"
    return "research" if RESEARCH_RE.search(text or "") else "text"


def record_stat(worker, kind, ok, seconds, note=""):
    """Ghi 1 kết quả. Lỗi ghi thì bỏ qua (không ảnh hưởng việc chính)."""
    try:
        d = rjson(DATA / WSTATS_F, {}) or {}
        k = f"{worker}:{kind}"
        d[k] = (d.get(k) or [])[-(WSTATS_KEEP - 1):] + [{"ok": bool(ok), "s": int(seconds or 0), "t": int(time.time()),
                                                          **({"note": note[:80]} if note else {})}]
        wjson(DATA / WSTATS_F, d)
    except Exception as e:
        dlog(f"record_stat: {e}")


def stat_of(worker, kind, d=None):
    """{n, rate (tỉ lệ làm được), med (thời gian trung vị của lần làm được, giây)} hoặc None nếu chưa đủ dữ liệu."""
    d = d if d is not None else (rjson(DATA / WSTATS_F, {}) or {})
    xs = d.get(f"{worker}:{kind}") or []
    if len(xs) < WSTATS_MIN:
        return None
    oks = sorted(x["s"] for x in xs if x.get("ok"))
    return {"n": len(xs), "rate": len(oks) / len(xs), "med": oks[len(oks) // 2] if oks else 0}


def best_worker(kind, avail=("codex", "gemini"), d=None):
    """Bên nên giao: tỉ lệ làm được cao hơn rõ (>15 điểm %) thì chọn bên đó; ngang nhau thì bên nhanh hơn."""
    d = d if d is not None else (rjson(DATA / WSTATS_F, {}) or {})
    avail = [w for w in avail if w]
    if len(avail) < 2:
        return avail[0] if avail else ""
    a, b = (stat_of(w, kind, d) for w in avail)
    if not a or not b:
        return DEFAULT_PICK[kind] if DEFAULT_PICK[kind] in avail else avail[0]
    if abs(a["rate"] - b["rate"]) > 0.15:
        return avail[0] if a["rate"] > b["rate"] else avail[1]
    return avail[0] if (a["med"] or 1e9) <= (b["med"] or 1e9) else avail[1]


def stat_poor(worker, kind):
    """Bên này gần đây hầu như không làm được loại việc này (>= 4 lần, < 40%) và bên kia làm tốt hơn -> giao thẳng bên kia."""
    d = rjson(DATA / WSTATS_F, {}) or {}
    s = stat_of(worker, kind, d)
    o = stat_of("codex" if worker == "gemini" else "gemini", kind, d)
    last = max((x.get("t", 0) for x in d.get(f"{worker}:{kind}") or []), default=0)
    if time.time() - last > WSTATS_RETRY:          # lâu rồi chưa thử: cho thử lại 1 lần để có số liệu mới (có thể đã tốt lên)
        return False
    return bool(s and s["n"] >= 4 and s["rate"] < 0.4 and (not o or o["rate"] >= s["rate"] + 0.2))


def stat_cap(worker, kind, timeout):
    """Thời gian tối đa hợp lý cho 1 việc: 4 lần thời gian trung vị các lần làm được (tối thiểu 6 phút). Chưa có dữ liệu ->
    giới hạn gốc. Quá mức này là bất thường -> dừng sớm, giao bên kia."""
    s = stat_of(worker, kind)
    return min(timeout, max(360, 4 * s["med"])) if s and s["med"] else timeout


def stats_md(on):
    """Mục 'Chọn worker' cho hướng dẫn: số liệu thật của máy này (app tự cập nhật sau mỗi việc)."""
    d = rjson(DATA / WSTATS_F, {}) or {}
    avail = [w for w in ("codex", "gemini") if w in on]
    lines = []
    for k, nm in KIND_NAMES.items():
        info = []
        for w in avail:
            s = stat_of(w, k, d)
            info.append(f"{WN_[w]} {round(s['rate'] * 100)}% làm được, ~{round(s['med'] / 60, 1)} phút ({s['n']} lần)" if s
                        else f"{WN_[w]} chưa đủ dữ liệu")
        lines.append(f"- {nm}: giao {WN_.get(best_worker(k, avail, d), 'tự làm') if avail else 'tự làm'}"
                     + (f" — {'; '.join(info)}" if avail else ""))
    return "\n".join(lines)
