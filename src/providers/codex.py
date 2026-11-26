# Maestro — providers/codex.py: ChatGPT/Codex: gói, mức dùng.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def codex_plan():
    """Gói ChatGPT ghi trong file đăng nhập Codex (free, plus, pro, team...). '' = không rõ."""
    f = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    toks = (rjson(f) or {}).get("tokens") or {}
    return str((_jwt(toks.get("id_token", "")).get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or "").lower()


def codex_usable():
    c = CFG["ais"].get("codex", {})
    return bool(find_bin("codex")) and bool(c.get("ok"))


def codex_usage():
    """Mức dùng ChatGPT/Codex: Codex tự lưu trong file phiên trên máy (sự kiện có 'rate_limits', như lệnh /status) — không tốn lượt."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    if not home.is_dir():
        return {"known": False, "note": "Chưa có dữ liệu (chưa chạy ChatGPT lần nào)"}
    files = sorted(home.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:8]
    for f in files:
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, f.stat().st_size - 400_000))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except Exception:
            continue
        for line in reversed(tail):
            if "rate_limits" not in line:
                continue
            try:
                rl = _find_key(json.loads(line), "rate_limits")
            except Exception:
                continue
            if not isinstance(rl, dict):
                continue
            at = f.stat().st_mtime
            out = {"known": True, "at": int(at), "plan": str(rl.get("plan_type") or "")}
            for k, name in (("primary", "five_hour"), ("secondary", "week")):
                w = rl.get(k) or {}
                if isinstance(w, dict) and w.get("used_percent") is not None:
                    rs = _ts(w.get("resets_at")) or (int(at + float(w["resets_in_seconds"])) if w.get("resets_in_seconds") is not None else None)
                    out[name] = {"left": _pct_left(w.get("used_percent")), "resets_at": rs, "minutes": w.get("window_minutes")}
            cr = rl.get("credits") or {}
            if isinstance(cr, dict):
                out["credits"] = {"has": bool(cr.get("has_credits")), "unlimited": bool(cr.get("unlimited")), "balance": cr.get("balance")}
            for name in ("five_hour", "week"):      # số đã cũ mà qua giờ reset -> coi như đầy lại
                w = out.get(name)
                if w and w.get("resets_at") and w["resets_at"] < time.time():
                    w.update(left=100.0, stale=True)
            return out
    return {"known": False, "note": "Chưa có dữ liệu mức dùng (chạy ChatGPT 1 lần để có)"}
