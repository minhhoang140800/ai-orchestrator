# Maestro — providers/gemini.py: Gemini: Antigravity (agy), API key, đăng nhập, danh sách model.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

def set_gemini_key(key):
    """Lưu Gemini API key (chỉ trên máy này, data/config.json). Kiểm tra key bằng cách lấy danh sách model."""
    key = re.sub(r"\s+", "", key)
    if not key:
        CFG.pop("gemini_key", None)
        CFG.pop("gemini_api_models", None)
        save_cfg()
        return {"ok": True, "msg": "Đã xoá Gemini API key"}
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,300}", key):      # key cũ AIza…; key mới (auth key) dài hơn, có dấu . và -
        return {"error": "Key không hợp lệ: chỉ gồm chữ, số và dấu - _ . (bấm Copy key trong Google AI Studio rồi dán lại)"}
    try:
        models = gemini_api_models(key)
    except Exception as e:
        return {"error": f"Key chưa dùng được: {e}"[:200]}
    if not models:
        return {"error": "Key hợp lệ nhưng không có model Gemini nào dùng được"}
    CFG["gemini_key"], CFG["gemini_api_models"] = key, models
    save_cfg()
    return {"ok": True, "msg": f"Đã lưu key · {len(models)} model Gemini dùng được"}


def gemini_forget():
    """Xóa phiên Antigravity đã lưu (để đổi tài khoản)."""
    try:
        if IS_WIN:
            _run_hidden(["cmdkey", "/delete:gemini:antigravity"], 15)
        f = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        if f.exists():
            f.unlink()
        if os.environ.get("FAKE_STATE"):
            Path(os.environ["FAKE_STATE"], "agy").unlink(missing_ok=True)
    except Exception:
        pass


def gemini_term_login(exe, d, cur):
    """Đăng nhập Antigravity không cần cửa sổ: tool tự chọn Google OAuth, mã xác nhận dán trên trang web của tool."""
    term = HiddenTerm([exe], str(d))
    LOGINS["gemini"] = term
    m = term.wait_for(r"Select login method|not signed in|@[\w-]+\.[\w.-]+", 40)
    if not m:
        return False
    if "@" in m.group(0) and "signed in" not in term.buf.lower():
        return True                       # đã đăng nhập sẵn
    term.wait_for(r"Select login method", 10)
    term.send("\r")                       # chọn 1. Google OAuth -> agy mở trình duyệt
    time.sleep(1.5)
    u = re.search(r"https://accounts\.google\.com/\S+", re.sub(r"[\r\n]+", "", term.buf))
    cur["url"] = u.group(0) if u else ""
    cur["code"] = True                    # trang web hiện ô dán mã
    t0 = time.time()
    while time.time() - t0 < 600 and term.alive():
        if gemini_has_cred():
            time.sleep(2)
            return True
        time.sleep(1.5)
    return gemini_has_cred()


def gemini_has_cred():
    """Máy đã có phiên đăng nhập Antigravity chưa (Windows Credential Manager hoặc file token)."""
    if os.environ.get("FAKE_STATE"):
        return Path(os.environ["FAKE_STATE"], "agy").exists()
    if (Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists():
        return True
    if not IS_WIN:
        return True
    try:
        return "gemini:antigravity" in _run_hidden(["cmdkey", "/list"], 15).stdout.lower()
    except Exception:
        return False


def gemini_ping(exe, timeout):
    """Gemini: chạy 1 câu ngắn. Đã đăng nhập -> trả lời ngay; chưa -> agy tự mở trình duyệt đăng nhập."""
    d = DATA / ".login"
    d.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([exe, "-p", PING], cwd=str(d), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    LOGINS["gemini"] = p
    t0 = time.time()
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        return False, "timeout", time.time() - t0
    return "PONG" in (out or "").upper(), (out or "").strip()[-200:], time.time() - t0


GEMINI_API = os.environ.get("AIO_GEMINI_BASE") or "https://generativelanguage.googleapis.com"
API_SKIP = ("embedding", "tts", "image", "live", "audio", "aqa", "robotics", "computer-use", "native", "learnlm", "gemma")


def gemini_api_call(key, path, body=None, timeout=30):
    """Gọi Gemini API bằng key của người dùng (key gửi trong header, không nằm trong đường dẫn) -> (mã HTTP, JSON)."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(GEMINI_API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json", "User-Agent": "maestro"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def api_err(j):
    return str(((j or {}).get("error") or {}).get("message") or "")[:300]


def gemini_api_models(key):
    """Danh sách model Gemini mà key dùng được (mới nhất trước). Key sai -> ValueError."""
    code, j = gemini_api_call(key, "/v1beta/models?pageSize=1000", timeout=20)
    if code != 200:
        raise ValueError(api_err(j) or f"HTTP {code}")
    names = []
    for m in j.get("models") or []:
        n = str(m.get("name", "")).split("/")[-1]
        if n.startswith("gemini") and "generateContent" in (m.get("supportedGenerationMethods") or []) and not any(x in n for x in API_SKIP):
            names.append(n)

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0
    return sorted(dict.fromkeys(names), key=lambda n: (-ver(n), "preview" in n or "exp" in n, n))


def api_order(models, hard):
    """Thứ tự thử model: thường Flash -> Flash-Lite -> Pro; việc khó Pro trước. Mỗi nhóm: bản mới nhất trước."""
    flash = [m for m in models if "flash" in m and "lite" not in m]
    lite = [m for m in models if "lite" in m]
    pro = [m for m in models if "pro" in m]
    rest = [m for m in models if m not in flash + lite + pro]
    return (pro + flash + lite if hard else flash + lite + pro) + rest


API_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
            ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain", ".csv": "text/csv", ".json": "application/json",
            ".html": "text/html"}


def api_worker(st, v, key, text, a):
    """Worker Gemini qua API key: chỉ trả lời bằng chữ (đọc được ảnh/PDF/file chữ trong input mà task nhắc tên)."""
    inp = Path(os.environ.get("AIO_INPUT") or Path.cwd() / "input")
    parts, total = [{"text": text + "\n\n---\nTrả lời đầy đủ bằng chữ (markdown). Bạn không tạo/sửa được file."}], 0
    if inp.is_dir():
        for f in sorted(inp.iterdir()):
            mime = API_MIME.get(f.suffix.lower())
            if f.is_file() and mime and f.name in text and total + f.stat().st_size < 15 * 2 ** 20:
                total += f.stat().st_size
                parts.append({"text": f"[File đính kèm: {f.name}]"})
                parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(f.read_bytes()).decode()}})
    models = (rjson(CFG_FILE, {}) or {}).get("gemini_api_models") or []
    t0, log, limited, err, m = time.time(), [], False, "", ""
    try:
        models = models or gemini_api_models(key)
        hard = os.environ.get("AIO_HARD") == "1" or (os.environ.get("AIO_MODE") == "balanced" and (a.hard or v.name != "v1"))
        for m in api_order([x for x in models if x not in bad_models("gemini_api")], hard)[:5]:
            body = {"contents": [{"role": "user", "parts": parts}], "tools": [{"google_search": {}}]}
            code, j = gemini_api_call(key, f"/v1beta/models/{m}:generateContent", body, a.timeout)
            if code == 400 and "tool" in api_err(j).lower() or code == 400 and "search" in api_err(j).lower():
                body.pop("tools")                  # model không hỗ trợ tìm kiếm Google -> gọi không kèm
                code, j = gemini_api_call(key, f"/v1beta/models/{m}:generateContent", body, a.timeout)
            log.append(f"{m}: HTTP {code} {api_err(j)}")
            if code == 200:
                out = "".join(p_.get("text", "") for c in j.get("candidates") or [] for p_ in (c.get("content") or {}).get("parts") or [])
                if out.strip():
                    (v / "result.md").write_text(out, encoding="utf-8")
                    st.update(status="completed", model="api:" + m, via="api", seconds=round(time.time() - t0), error="", exit_code=0)
                    st.pop("reset", None)
                    break
                err = "Gemini API không trả nội dung (có thể bị bộ lọc an toàn chặn)"
            elif code == 429:
                limited, err = True, api_err(j) or "Gemini API hết lượt (429)"
            elif code in (401, 403) or code == 400 and "api key" in api_err(j).lower():
                err = "Gemini API key không dùng được: " + api_err(j)
                break
            elif code == 404:
                mark_bad_model("gemini_api", m)
            else:
                err = api_err(j) or f"HTTP {code}"
    except Exception as e:
        err = f"Không gọi được Gemini API: {e}"[:300]
    if st.get("via") != "api" or st.get("status") != "completed":
        if limited:
            lm = note_limit("gemini_api", err)
            st.update(status="limit", reset=lm["until"], via="api", model="api:" + (m or "?"),
                      error="Gemini API hết lượt, dùng lại lúc " + datetime.datetime.fromtimestamp(lm["until"]).strftime("%H:%M"))
        else:
            st.update(status="failed", via="api", model="api:" + (m or "?"), error=err or "Gemini API lỗi")
    with open(v / "worker.log", "a", encoding="utf-8") as fh:
        fh.write("--- gemini api ---\n" + "\n".join(log) + "\n")


def gemini_models():
    """Lấy tên model Gemini thật của tài khoản (agy models) để chọn Neon/Hydrogen."""
    exe = find_bin("agy")
    if not exe:
        return
    try:
        out = _run_hidden([exe, "models"], 40).stdout or ""
    except Exception:
        return
    found = []
    for line in out.splitlines():
        l2 = line.strip().strip("-*•> ").strip()
        if not l2 or not re.search(r"[a-z]", l2, re.I) or l2.lower().startswith(("available", "usage", "model", "name ")):
            continue
        q = re.findall(r'"([^"]+)"', l2)
        ids = re.findall(r"\b[a-z0-9]+(?:[.\-][a-z0-9]+)+\b", l2.lower())
        found.append(q[0] if q else (ids[0] if ids and any(c.isalpha() for c in ids[0]) else re.split(r"\s{2,}|\t", l2)[0]))
    if found:
        CFG["gemini_list"] = found
        save_cfg()
    dlog(f"agy models -> {found}; raw: {out[:400]}")
