# Maestro — core/models.py: Model theo chế độ, model lỗi tạm bỏ qua, tự hạ khi sắp hết hạn mức.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

EFFORTS = ["low", "medium", "high", "xhigh"]
# ChatGPT: thử lần lượt (gói Plus có GPT-6.2; gói Free chỉ có dòng Luna) -> cuối cùng là model mặc định của tài khoản
CODEX_MODELS = ["gpt-6.2-sol", "gpt-6-sol", "gpt-6-luna"]
# Gemini: Neon cho Pro (bước quá khó: high), Hydrogen cho các chế độ khác; chỉ dùng model mà `agy models` liệt kê
MODEL_PLAN = {   # (model, mức suy nghĩ) theo chế độ
    "claude": {"pro": ("opus", "high"), "balanced": ("opus", "medium"), "turbo": ("opus", "medium"), "eco": ("opus", "low"),
               "code": ("opus", "medium")},       # Code mode: Opus mới nhất (bí danh "opus" tự theo bản mới), mức medium
    "codex": {"pro": (CODEX_MODELS, "high"), "balanced": (CODEX_MODELS, "medium"), "turbo": (CODEX_MODELS, "low"),
              "eco": (CODEX_MODELS, "low"), "code": (CODEX_MODELS, "medium")},
    "gemini": {"pro": ("neon", "medium"), "balanced": ("hydrogen", "medium"), "turbo": ("hydrogen", "medium"),
               "eco": ("hydrogen", "low"), "code": ("hydrogen", "medium")},
}
GEM_FALLBACK = {"neon": ["hydrogen", "flash"], "hydrogen": ["flash", "neon"]}


def pressure(ai):
    """Sắp hết hạn mức (cảnh báo) hoặc vừa hết trong 6 giờ qua -> hạ 1 bậc để còn dùng được lâu hơn."""
    d = (rjson(DATA / "limits.json", {}) or {}).get(ai) or {}
    return bool(d) and ((d.get("warn") and time.time() - d.get("warn_at", 0) < 6 * 3600)
                        or time.time() - d.get("until", 0) < 6 * 3600)


BAD_MODEL_TTL = 24 * 3600      # model báo lỗi chỉ bị bỏ qua 24 giờ (lỗi có thể chỉ là tạm thời)


def bad_models(ai):
    v = (rjson(DATA / "models_bad.json", {}) or {}).get(ai) or {}
    if not isinstance(v, dict):                     # bản cũ lưu danh sách vĩnh viễn -> bỏ
        return set()
    return {m for m, ts in v.items() if time.time() - float(ts or 0) < BAD_MODEL_TTL}


def mark_bad_model(ai, m):
    f = DATA / "models_bad.json"
    d = rjson(f, {}) or {}
    cur = d.get(ai) if isinstance(d.get(ai), dict) else {}
    cur = {k: ts for k, ts in cur.items() if time.time() - float(ts or 0) < BAD_MODEL_TTL}
    cur[m] = int(time.time())
    d[ai] = cur
    try:
        wjson(f, d)
    except Exception:
        pass


def gem_pick(key, eff):
    """Chọn đúng tên model Gemini có trong `agy models` (theo dòng + mức suy nghĩ); không có thì lùi dòng khác."""
    ls = [x for x in ((rjson(DATA / "config.json", {}) or {}).get("gemini_list") or []) if x not in bad_models("gemini")]
    for k in [key] + GEM_FALLBACK.get(key, []):
        hit = [x for x in ls if k in x.lower()]
        if hit:
            same = [x for x in hit if eff and eff in x.lower()]
            return (same or hit)[0]
    return None                                     # không có danh sách -> để agy tự chọn model mặc định


def pick_model(ai, mode, hard=False):
    mode = norm_mode(mode)
    m, eff = MODEL_PLAN[ai][mode]
    if hard:
        eff = "high" if ai == "gemini" else "xhigh"
    elif pressure(ai) and eff != "low":
        eff = EFFORTS[max(0, EFFORTS.index(eff) - 1)]
    if ai == "gemini":
        return gem_pick(m, eff), eff
    if ai == "codex":
        m = [x for x in m if x not in bad_models("codex")]
    return m, eff


def note_warn(ai, text):
    f = DATA / "limits.json"
    d = rjson(f, {}) or {}
    cur = d.get(ai) or {"until": 0}
    cur.update(warn=True, warn_at=int(time.time()), msg=(text or "")[-200:])
    d[ai] = cur
    try:
        wjson(f, d)
    except Exception:
        pass
