# Maestro — providers/claude.py: Claude: gói, đăng nhập trong terminal ẩn, mức dùng.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

PAID_CLAUDE = {"pro", "max", "team", "enterprise"}


def claude_cfg_plan(email=""):
    """Dự phòng khi `auth status` không báo subscriptionType: đọc oauthAccount.organizationType trong ~/.claude.json
    (vd claude_pro, claude_max). Chỉ nhận khi email khớp tài khoản đang đăng nhập; không đọc token."""
    for f in (Path.home() / ".claude.json", Path.home() / ".claude" / ".claude.json"):
        acc = (rjson(f) or {}).get("oauthAccount") or {}
        if not acc:
            continue
        if email and acc.get("emailAddress") and acc["emailAddress"].lower() != email.lower():
            return ""
        p = str(acc.get("organizationType") or "").lower().removeprefix("claude_")
        return p if p in PAID_CLAUDE else ""
    return ""


def claude_plan(j):
    """Gói Claude từ `claude auth status --json` -> (tên gói, trả phí?). None = không rõ (bản CLI cũ) -> coi như trước đây."""
    if not j.get("loggedIn"):
        return "", None
    plan = j.get("subscriptionType") or ""
    if not plan:                                # có máy không trả subscriptionType: thử planTier (vd max_5x), rồi ~/.claude.json
        tier = str(j.get("planTier") or "").lower().split("_")[0]
        plan = tier if tier in PAID_CLAUDE else claude_cfg_plan(j.get("email") or "")
        if plan:
            return plan, True
        dlog("claude_plan: không đọc được gói " + json.dumps({k: j.get(k) for k in (
            "authMethod", "apiProvider", "subscriptionType", "storedClaudeAiLogin", "planTier")}))
    if j.get("authMethod") not in (None, "claude.ai") or "storedClaudeAiLogin" not in j and "subscriptionType" not in j:
        return plan, None                     # đăng nhập kiểu khác / CLI không báo gói
    return plan or "free", plan in PAID_CLAUDE


def claude_usable():
    c = CFG["ais"].get("claude", {})
    return bool(find_bin("claude")) and bool(c.get("ok")) and c.get("paid") is not False


def claude_term_login(exe, d, cur, env):
    """Claude: chạy 'claude auth login' trong terminal ẩn. Claude tự mở trình duyệt với trang đăng nhập trả kết quả thẳng về
    máy (localhost) -> bấm Authorize là xong. Dự phòng (trình duyệt không mở): link trên màn hình + dán mã vào web."""
    p = HiddenTerm([exe, "auth", "login", "--claudeai"], str(d), env)
    LOGINS["claude"] = p
    t0 = time.time()
    while time.time() - t0 < 900 and p.alive():
        time.sleep(1)
        if not cur.get("url"):
            m = re.search(r"https://claude\.ai/oauth/authorize\S+|https://\S*oauth\S+", p.buf)
            if m:
                cur["url"] = m.group(0)
                cur["code"] = True
                cur["auto"] = "Opening browser" in p.buf
        if time.time() - t0 > 8 and int(time.time() - t0) % 4 == 0 and ai_status("claude").get("ok"):
            break
    time.sleep(1)
    dlog("claude login screen tail:\n" + p.buf[-1500:])
    p.kill()
    if not ai_status("claude").get("ok"):
        cur["msg"] = "Đăng nhập chưa xong. Bấm Đăng nhập lại; nếu vẫn lỗi, Export log gửi tôi."


def claude_token():
    """Token đăng nhập Claude Code trên máy (chỉ dùng để hỏi mức dùng của chính tài khoản, gửi tới Anthropic; không lưu lại)."""
    try:
        if IS_MAC:
            r = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                               capture_output=True, text=True, timeout=10)
            d = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
        else:
            d = rjson(Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json", {}) or {}
        return str((d.get("claudeAiOauth") or {}).get("accessToken") or "")
    except Exception:
        return ""


def claude_usage():
    """Mức dùng Claude (5 giờ / tuần / extra usage) — cùng kênh lệnh /usage của Claude Code dùng. Kênh nội bộ: có thể đổi."""
    import urllib.error
    import urllib.request
    tok = claude_token()
    if not tok:
        return {"known": False, "note": "Không đọc được đăng nhập Claude trên máy"}
    req = urllib.request.Request(os.environ.get("AIO_CLAUDE_USAGE") or "https://api.anthropic.com/api/oauth/usage",
                                 headers={"Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20",
                                          "User-Agent": "maestro", "Accept": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"known": False, "note": f"Claude chưa trả mức dùng (HTTP {e.code}) — chạy 1 task Claude rồi thử lại"}
    except Exception as e:
        return {"known": False, "note": f"Không kết nối được: {e}"[:120]}
    out = {"known": True, "at": int(time.time())}
    for k, name in (("five_hour", "five_hour"), ("seven_day", "week"), ("seven_day_opus", "week_opus")):
        w = d.get(k)
        if isinstance(w, dict) and w.get("utilization") is not None:
            out[name] = {"left": _pct_left(w.get("utilization")), "resets_at": _ts(w.get("resets_at"))}
    ex = d.get("extra_usage")
    if isinstance(ex, dict):
        out["credits"] = {"has": bool(ex.get("is_enabled")), "used": ex.get("used_credits"), "limit": ex.get("monthly_limit")}
    return out if len(out) > 2 else {"known": False, "note": "Claude trả dữ liệu không đọc được"}
