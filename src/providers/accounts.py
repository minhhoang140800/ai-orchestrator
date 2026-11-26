# Maestro — providers/accounts.py: Tài khoản AI: trạng thái, đăng nhập/đăng xuất, chọn PM, chế độ Free.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _jwt(tok):
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)) or {}
    except Exception:
        return {}


def _jwt_email(tok):
    return _jwt(tok).get("email", "")


def ai_email(ai):
    """Email tài khoản đang đăng nhập. Chỉ đọc email, không đọc/ghi token ra ngoài."""
    home = Path.home()
    try:
        if ai == "claude":
            for f in (home / ".claude.json", home / ".claude" / ".claude.json"):
                acc = (rjson(f) or {}).get("oauthAccount") or {}
                if acc.get("emailAddress"):
                    return acc["emailAddress"]
        elif ai == "codex":
            f = Path(os.environ.get("CODEX_HOME") or home / ".codex") / "auth.json"
            toks = (rjson(f) or {}).get("tokens") or {}
            e = _jwt_email(toks.get("id_token", ""))
            if e:
                return e
        elif ai == "gemini":
            for f in (home / ".gemini" / "google_accounts.json", home / ".gemini" / "antigravity-cli" / "google_accounts.json"):
                act = (rjson(f) or {}).get("active")
                if act:
                    return act
            exe = find_bin("agy")
            if exe:
                p = subprocess.run([exe, "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=20, creationflags=NO_WINDOW)
                m = EMAIL_RE.search((p.stdout or "") + (p.stderr or ""))
                if m:
                    return m.group(0)
    except Exception:
        pass
    return ""


def pick_pm(mode=None):
    """Ai làm quản lý (PM): Claude gói trả phí như cũ; không có thì ChatGPT (Codex, kể cả gói Free); không có ai -> ''."""
    return "claude" if claude_usable() else "codex" if codex_usable() else ""


def free_setup():
    """Không có tài khoản trả phí nào (Claude/ChatGPT) -> chế độ Free: tiết kiệm tối đa + mời thêm Gemini API key."""
    a = CFG["ais"]
    return not claude_usable() and a.get("codex", {}).get("paid") is not True


def setup_state():
    pm = pick_pm()
    return {"pm": pm, "free": free_setup(), "gemini_key": bool(CFG.get("gemini_key")),
            "gemini_api_models": len(CFG.get("gemini_api_models") or [])}


def ai_state():
    lm = limits()
    return {k: {"installed": bool(find_bin(v["bin"])), **CFG["ais"].get(k, {}),
                **({"limit_until": lm[k]["until"], "limit_known": lm[k].get("known", True)} if k in lm else {})}
            for k, v in AIS.items()}


LOGINS = {}          # ai -> Popen của tiến trình đăng nhập đang chạy
_status_lock = threading.Lock()


def ai_status(ai):
    """Trạng thái đăng nhập. Claude/ChatGPT: lệnh trạng thái (tức thì, không tốn hạn mức).
    Gemini không có lệnh trạng thái -> giữ kết quả lần đăng nhập gần nhất."""
    exe = find_bin(AIS[ai]["bin"])
    cur = CFG["ais"].setdefault(ai, {})
    if not exe:
        cur.update(ok=False, msg="Chưa cài")
        return cur
    try:
        if ai == "claude":
            p = _run_hidden([exe, "auth", "status", "--json"], 20)
            j = json.loads(p.stdout[p.stdout.index("{"):]) if "{" in (p.stdout or "") else {}
            ok = bool(j.get("loggedIn"))
            plan, paid = claude_plan(j)
            cur.update(ok=ok, msg="", email=(j.get("email") or ai_email(ai)) if ok else "", plan=plan, paid=paid)
        elif ai == "codex":
            p = _run_hidden([exe, "login", "status"], 45)
            out = ((p.stdout or "") + (p.stderr or "")).lower()
            if "node" in out and ("not recognized" in out or "not found" in out):   # PATH thiếu node -> tìm lại rồi thử 1 lần
                refresh_path()
                dlog(f"codex: node missing; which(node)={shutil.which('node')}; PATH={os.environ.get('PATH', '')[:1500]}")
                if shutil.which("node"):
                    p = _run_hidden([exe, "login", "status"], 45)
                    out = ((p.stdout or "") + (p.stderr or "")).lower()
            ok = p.returncode == 0 and "not logged" not in out
            msg = ""
            if not ok and "not logged" not in out:   # lệnh lỗi (không phải chưa đăng nhập) -> không coi là bị văng
                dlog(f"codex login status rc={p.returncode}: {out[-600:]}")
                if "node" in out and ("not recognized" in out or "not found" in out):
                    msg = ("Không tìm thấy Node.js — chạy lại Maestro_setup.bat (sẽ cài Node), rồi Export log nếu vẫn lỗi"
                           if not shutil.which("node") else "Codex lỗi khi chạy — bấm Export log gửi lại")
                elif (Path.home() / ".codex" / "auth.json").exists():
                    ok = True
            plan = codex_plan() if ok else ""
            cur.update(ok=ok, msg=msg, email=ai_email(ai) if ok else "", plan=plan,
                       paid=(plan != "free") if plan else None)
    except Exception as e:
        cur.setdefault("ok", False)
        cur["msg"] = str(e)[:150]
    return cur


_status_at = {"t": 0.0}


def refresh_status():
    """Kiểm tra đăng nhập Claude/ChatGPT theo yêu cầu (khi đang soạn prompt hoặc mở Cài đặt); tối đa 1 lần/phút."""
    if time.time() - _status_at["t"] < 60:
        return {"ok": True}
    _status_at["t"] = time.time()

    def run():
        for ai in ("claude", "codex"):
            if not CFG["ais"].get(ai, {}).get("pending"):
                with _status_lock:
                    before = dict(CFG["ais"].get(ai, {}))
                    ai_status(ai)
                    if before != CFG["ais"].get(ai):
                        save_cfg()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def ai_login(ai, switch=False):
    """Đăng nhập trên trình duyệt, không mở cửa sổ dòng lệnh (trừ khi Gemini bắt buộc)."""
    exe = find_bin(AIS[ai]["bin"])
    if not exe:
        return {"error": "Chưa cài. Chạy lại file cài Maestro (.bat)."}
    cur = CFG["ais"].setdefault(ai, {})
    if cur.get("pending"):
        return {"ok": True}
    cur["pending"] = "login"
    d = DATA / ".login"
    d.mkdir(parents=True, exist_ok=True)

    def run():
        try:
            if ai in ("claude", "codex"):
                if switch:
                    try:
                        _run_hidden([exe, "auth", "logout"] if ai == "claude" else [exe, "logout"], 30)
                    except Exception:
                        pass
                elif ai_status(ai).get("ok"):
                    return
                args = [exe, "auth", "login", "--claudeai"] if ai == "claude" else [exe, "login"]
                old = LOGINS.pop(ai, None)
                if old:
                    kill_tree(old)
                if ai == "codex" and IS_WIN:          # phiên đăng nhập cũ còn giữ cổng 1455 -> codex login thoát ngay
                    _run_hidden(["powershell", "-NoProfile", "-Command",
                                 "Get-NetTCPConnection -LocalPort 1455 -State Listen -EA 0 | % { Stop-Process -Id $_.OwningProcess -Force -EA 0 }"], 20)
                if ai == "claude":                   # Claude tự mở trình duyệt, đăng nhập xong tự nhận (không cần dán mã)
                    env = dict(os.environ)
                    if env.get("BROWSER") == "none":
                        env.pop("BROWSER")
                    return claude_term_login(exe, d, cur, env)
                env = dict(os.environ)                  # ChatGPT: để Codex tự mở trình duyệt (đặt BROWSER làm hỏng đăng nhập)
                logf = d / f"{ai}.out"
                fh = open(logf, "w", encoding="utf-8", errors="replace")
                p = subprocess.Popen(args, cwd=str(d), stdin=subprocess.PIPE, stdout=fh, stderr=subprocess.STDOUT,
                                     env=env, creationflags=NO_WINDOW)
                LOGINS[ai] = p

                def grab():                         # lấy link đăng nhập để web mở trong cửa sổ nhỏ
                    for _ in range(40):
                        time.sleep(0.5)
                        try:
                            m = re.search(r"https://\S+", logf.read_text("utf-8", "replace"))
                        except Exception:
                            m = None
                        if m:
                            cur["url"] = m.group(0).rstrip(").,'\"")
                            return
                        if p.poll() is not None:
                            return
                threading.Thread(target=grab, daemon=True).start()
                t0 = time.time()
                while time.time() - t0 < 600:
                    time.sleep(2)
                    if p.poll() is not None or (time.time() - t0 > 6 and ai_status(ai).get("ok")):
                        break
                time.sleep(1)
                kill_tree(p)
                fh.close()
                ok = ai_status(ai).get("ok")
                if not ok:
                    dlog(f"{ai} login failed rc={p.poll()}; output:\n" + logf.read_text("utf-8", "replace")[-1500:])
                    lo = logf.read_text("utf-8", "replace").lower()
                    cur["msg"] = ("Thiếu Node.js — chạy lại Maestro_setup.bat" if "node" in lo and "not recognized" in lo
                                  else "Đăng nhập chưa xong. Bấm Đăng nhập lại; nếu vẫn lỗi, Export log gửi tôi.")
            else:
                # agy -p không tự hoàn tất đăng nhập được (không có chỗ dán mã) -> chỉ chạy ẩn khi máy đã có phiên lưu
                ok = (not switch) and gemini_has_cred() and gemini_ping(exe, 120)[0]
                if not ok:
                    if switch:
                        gemini_forget()
                    fail = ""
                    try:
                        done = gemini_term_login(exe, d, cur)
                    except Exception:                # không dựng được terminal ảo (thiếu thư viện)
                        import traceback
                        done = False
                        fail = "Không mở được đăng nhập Gemini — chạy lại file cài Maestro, rồi bấm Đăng nhập lại"
                        dlog("gemini hidden login failed:\n" + traceback.format_exc())
                    else:
                        lg0 = LOGINS.get("gemini")
                        dlog(f"gemini hidden login done={done}; screen tail:\n" + (lg0.buf[-1500:] if lg0 else ""))
                    lg = LOGINS.pop("gemini", None)
                    if lg:
                        lg.kill()
                    cur.pop("code", None)
                    cur.pop("url", None)
                    ok = bool(done) and gemini_has_cred() and gemini_ping(exe, 120)[0]
                cur.update(ok=ok, msg="" if ok else (fail or "Chưa đăng nhập được"), email="")
        except Exception as e:
            cur.update(ok=False, msg=str(e)[:150])
        finally:
            LOGINS.pop(ai, None)
            cur.pop("pending", None)
            cur.pop("code", None)
            cur.pop("url", None)
            cur.pop("auto", None)
            cur["at"] = now()
            save_cfg()

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


def ai_cancel(ai):
    p = LOGINS.get(ai)
    if isinstance(p, HiddenTerm):
        p.kill()
    elif p:
        kill_tree(p)
    return {"ok": True}


def ai_code(ai, code):
    p = LOGINS.get(ai)
    code = re.sub(r"\s+", "", code or "")
    if not isinstance(p, HiddenTerm) or not p.alive() or not code:
        return {"error": "Phiên đăng nhập đã hết, bấm Đăng nhập lại"}
    p.send(code)
    time.sleep(0.3)
    p.send("\r")
    return {"ok": True}


def check_startup():
    """Mở app: chỉ kiểm tra AI nào chưa từng kiểm tra (lần đầu); còn lại dùng kết quả đã lưu."""
    threading.Thread(target=gemini_models, daemon=True).start()
    for ai in ("claude", "codex"):
        if "ok" not in CFG["ais"].get(ai, {}):
            ai_status(ai)
    save_cfg()
