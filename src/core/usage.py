# Maestro — core/usage.py: Hạn mức: nhận biết hết lượt, giờ reset, đo mức dùng, chặn khi còn ít (chỉ dùng trong gói).
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def usage_loop():
    while True:
        time.sleep(120)
        try:
            if any(t["status"] in ("running", "queued") for t in list(TASKS.values())):
                usage_refresh()
        except Exception as e:
            dlog(f"usage_loop: {e}")


# Chỉ cụm từ mà Claude/Codex/Gemini thật sự in ra khi hết hạn mức (chữ lẻ như "quota", "429" dễ trùng nội dung bài làm)
LIMIT_RE = re.compile(r"usage limit|limit reached|reached your (?:usage |rate )?limit|hit your (?:usage )?limit|rate limit(?:ed| exceeded)?"
                      r"|quota (?:exceeded|exhausted)|exceeded (?:your |the )?(?:current )?quota|resource_exhausted|resource exhausted"
                      r"|too many requests|\b429\b[^\n]{0,40}(?:limit|quota|too many)|limit will reset|out of (?:usage|credits)")


def is_limit(text):
    """Thông báo lỗi có phải do hết hạn mức không. Chỉ xét ~2000 ký tự cuối (phần lỗi), không xét nội dung bài làm."""
    return bool(LIMIT_RE.search((text or "")[-2000:].lower()))


def parse_reset(text, now_ts=None):
    """Đọc giờ được dùng lại từ thông báo hết hạn mức (Claude/Codex/Gemini). Không đọc được -> None."""
    now_ts = now_ts or time.time()
    s = (text or "").lower()
    m = re.search(r"\|(\d{10})\b", s) or re.search(r"reset[^0-9]{0,20}(\d{10})\b", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:in|after)\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,and]*)+)", s)
    if m:
        sec = 0
        for n, u in re.findall(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", m.group(1)):
            sec += int(n) * (86400 if u.startswith("d") else 3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
        if sec:
            return int(now_ts + sec)
    m = re.search(r"(?:resets?|reset at|try again at|available at|retry at)\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            d = datetime.datetime.fromtimestamp(now_ts).replace(hour=h, minute=mi, second=0, microsecond=0)
            if d.timestamp() <= now_ts:
                d += datetime.timedelta(days=1)
            return int(d.timestamp())
    return None


def note_limit(ai, text):
    """Ghi hạn mức của 1 AI (worker chạy ở tiến trình riêng -> dùng file chung)."""
    f = DATA / "limits.json"
    d = rjson(f, {}) or {}
    until = parse_reset(text)
    d[ai] = {"until": until or int(time.time() + 1800), "known": bool(until), "msg": (text or "").strip()[-300:], "at": int(time.time())}
    try:
        wjson(f, d)
    except Exception:
        pass
    return d[ai]


def limits():
    d = rjson(DATA / "limits.json", {}) or {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("until", 0) > time.time()}


USAGE_F = DATA / "usage.json"
IN_PLAN_LEFT = 2.0          # còn <= 2% của cửa sổ 5 giờ hoặc tuần -> coi như hết, không chạy (để không tràn sang credit)


def _pct_left(used):
    try:
        return max(0.0, min(100.0, 100.0 - float(used)))
    except (TypeError, ValueError):
        return None


def _ts(v):
    """Thời điểm reset: số giây epoch / mili giây / chuỗi ISO -> epoch giây (None nếu không đọc được)."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
        return int(f / 1000) if f > 1e11 else int(f)
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _find_key(o, key, depth=0):
    if depth > 6:
        return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find_key(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_key(v, key, depth + 1)
            if r is not None:
                return r
    return None


_usage_lock = threading.Lock()


def usage_refresh(force=False, only=None):
    """Đọc lại mức dùng (Claude: tối đa 1 lần/2 phút; ChatGPT: đọc file trên máy) -> data/usage.json (worker đọc chung)."""
    with _usage_lock:
        d = rjson(USAGE_F, {}) or {}
        for ai, fn, ttl in (("codex", codex_usage, 20), ("claude", claude_usage, 120)):
            if only and ai not in only:
                continue
            if force or time.time() - (d.get(ai) or {}).get("checked", 0) > ttl:
                try:
                    v = fn()
                except Exception as e:
                    v = {"known": False, "note": str(e)[:120]}
                v["checked"] = int(time.time())
                d[ai] = v
        try:
            wjson(USAGE_F, d)
        except Exception:
            pass
        return d


def in_plan_only(ai):
    """Luôn chỉ dùng trong gói: không bao giờ để AI tràn sang credit / extra usage (mặc định, không tắt được)."""
    return True


def usage_block(ai, fresh=False):
    """Đã dùng gần hết gói (cửa sổ 5 giờ hoặc tuần) và đang bật 'chỉ dùng trong gói' -> giờ dùng lại được (epoch), không thì 0."""
    if ai not in ("claude", "codex") or not in_plan_only(ai):
        return 0
    u = (usage_refresh(force=True, only=[ai]) if fresh else (rjson(USAGE_F, {}) or {})).get(ai) or {}
    if not u.get("known"):
        return 0
    worst = 0
    for name in ("five_hour", "week"):
        w = u.get(name) or {}
        if w.get("left") is not None and w["left"] <= IN_PLAN_LEFT and not w.get("stale"):
            worst = max(worst, w.get("resets_at") or int(time.time() + 1800))
    return worst if worst > time.time() else 0


def usage_snap():
    d = usage_refresh(force=True)
    out = {}
    for ai in ("claude", "codex"):
        u = d.get(ai) or {}
        if u.get("known"):
            out[ai] = {k: round(100 - u[k]["left"], 1) for k in ("five_hour", "week") if (u.get(k) or {}).get("left") is not None}
    return out


def usage_turn(t, w, key):
    """Đo mức dùng trước (u0) / sau (u1) một lượt làm (chỉ đọc số mức dùng, không tốn lượt AI);
    đủ cả hai thì ghi phần tăng vào lượt đó (cost) và cộng dồn vào cả task (t.cost)."""
    if w is None:
        return
    try:
        snap = usage_snap()
    except Exception as e:
        dlog(f"usage_snap: {e}")
        return
    if key == "u0":
        w["u0"] = snap
        return
    u0 = w.pop("u0", None)
    if u0 is None:
        return
    cost, tot = {}, t.get("cost") or {}
    for ai, v1 in snap.items():
        v0 = u0.get(ai) or {}
        for k in v1:
            if k in v0 and v1[k] - v0[k] > 0:
                cost.setdefault(ai, {})[k] = round(v1[k] - v0[k], 1)
                tot.setdefault(ai, {})[k] = round(tot.get(ai, {}).get(k, 0) + v1[k] - v0[k], 1)
    w["cost"] = cost
    t["cost"] = tot


def quota_md(fresh=False):
    """Hạn mức còn lại + giờ reset của từng AI (chỉ đọc số, không tốn lượt) -> vài dòng cho Claude tự chọn số worker."""
    try:
        d = usage_refresh(force=fresh) or {}
    except Exception:
        d = rjson(USAGE_F, {}) or {}
    lm = limits()
    hm = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%H:%M" if ts - time.time() < 20 * 3600 else "%d/%m %H:%M")
    out = []
    for ai, name in (("codex", "ChatGPT"), ("gemini", "Gemini"), ("claude", "Claude")):
        u, parts = d.get(ai) or {}, []
        if u.get("known"):
            for k, lab in (("five_hour", "5 giờ"), ("week", "tuần")):
                w = u.get(k) or {}
                if w.get("left") is not None:
                    parts.append(f"{lab} còn {round(w['left'])}%" + (f" (reset {hm(w['resets_at'])})" if w.get("resets_at") else ""))
        if ai in lm:
            parts.insert(0, f"ĐANG HẾT LƯỢT tới {hm(lm[ai]['until'])}")
        try:
            iu = img_block_until(ai) if ai != "claude" else 0
        except Exception:
            iu = 0
        if iu:
            parts.append(f"vẽ ảnh hết lượt tới {hm(iu)}")
        out.append(f"{name}: " + ("; ".join(parts) if parts else ("không đọc được mức dùng (chỉ biết khi hết lượt)" if ai == "gemini"
                                                                  else "chưa đọc được mức dùng")))
    return " · ".join(out)


def usage_counts():
    """App tự đếm: số lượt / phút của mỗi AI hôm nay và 7 ngày qua (từ lịch sử task trên máy)."""
    now_ = time.time()
    day0 = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
    out = {k: {ai: {"runs": 0, "mins": 0} for ai in ("claude", "codex", "gemini")} for k in ("today", "week")}
    for t in list(TASKS.values()):
        pm = t.get("pm") or "claude"
        for m in t.get("messages", []):
            if m.get("role") != "work" or not m.get("started_ts"):
                continue
            items = [(pm, (m.get("ended_ts") or now_) - m["started_ts"], m["started_ts"])]
            items += [(x.get("worker"), x.get("seconds") or 0, x.get("ts") or m["started_ts"]) for x in m.get("subs") or []
                      if x.get("worker") in ("codex", "gemini")]
            for ai, sec, ts in items:
                for k, since in (("today", day0), ("week", now_ - 7 * 86400)):
                    if ts >= since and ai in out[k]:
                        out[k][ai]["runs"] += 1
                        out[k][ai]["mins"] += max(0, sec) / 60
    for k in out:
        for ai in out[k]:
            out[k][ai]["mins"] = round(out[k][ai]["mins"])
    return out


def usage_state(force=False):
    d = usage_refresh(force=force)
    lm = limits()
    return {"claude": d.get("claude") or {}, "codex": d.get("codex") or {},
            "gemini": {"known": False, "note": "Gemini chưa cho đọc mức dùng — chỉ biết khi hết lượt",
                       **({"limit_until": lm["gemini"]["until"]} if "gemini" in lm else {})},
            "plans": {k: CFG["ais"].get(k, {}).get("plan", "") for k in ("claude", "codex")},
            "in_plan": {k: in_plan_only(k) for k in ("claude", "codex")}, "counts": usage_counts()}
