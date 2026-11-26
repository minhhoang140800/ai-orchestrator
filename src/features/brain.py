# Maestro — features/brain.py: Dự án, bộ nhớ dự án (Project Brain), Workflow Templates, gợi ý Save Template.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

_plock = threading.Lock()


def projects():
    return rjson(STORE / "projects.json", []) or []


def save_projects(ps):
    wjson(STORE / "projects.json", ps)


def project_of(t):
    return next((p for p in projects() if p["id"] == t.get("project")), None)


def project_api(d):
    a = d.get("action", "")
    with _plock:
        ps = projects()
        p = next((x for x in ps if x["id"] == d.get("id")), None)
        if a == "get":
            return p or {"error": "Không tìm thấy dự án"}
        if a == "create":
            name = (d.get("name") or "").strip()[:60]
            if not name:
                return {"error": "Nhập tên dự án"}
            p = next((x for x in ps if x["name"].lower() == name.lower()), None)
            if not p:
                p = {"id": "p" + secrets.token_hex(4), "name": name, "brain": "", "created": now()}
                ps.append(p)
                save_projects(ps)
            if d.get("task") in TASKS:
                TASKS[d["task"]]["project"] = p["id"]
                tsave(TASKS[d["task"]])
            return {"ok": True, "id": p["id"]}
        if not p and a != "assign":
            return {"error": "Không tìm thấy dự án"}
        if a == "save":
            p["brain"] = (d.get("brain") or "")[:20000]
            if (d.get("name") or "").strip():
                p["name"] = d["name"].strip()[:60]
            save_projects(ps)
            for t in TASKS.values():
                if t.get("project") == p["id"] and t["status"] not in ("running",) and wdir(t).exists():
                    write_claude_md(t)
            return {"ok": True}
        if a == "delete":
            save_projects([x for x in ps if x is not p])
            for t in TASKS.values():
                if t.get("project") == p["id"]:
                    t.pop("project", None)
                    tsave(t)
            return {"ok": True}
        if a == "assign":
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            t["project"] = p["id"] if p else ""
            tsave(t)
            if wdir(t).exists() and t["status"] != "running":
                write_claude_md(t)
            return {"ok": True}
        if a in ("accept", "reject"):              # đề xuất của Claude cho bộ nhớ dự án: người dùng duyệt
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            sug = brain_suggest(t)
            i = int(d.get("idx", -1))
            if not 0 <= i < len(sug):
                return {"error": "Đề xuất không còn"}
            item = sug.pop(i)
            if a == "accept":
                p["brain"] = (p.get("brain", "").rstrip() + "\n- " + item).strip()
                save_projects(ps)
            t["brain_suggest"] = sug
            f = wdir(t) / "brain_suggest.json"
            if f.exists():
                wjson(f, sug)
            tsave(t)
            return {"ok": True}
    return {"error": "bad request"}


def brain_suggest(t):
    f = wdir(t) / "brain_suggest.json"
    v = rjson(f, None) if f.exists() else None
    if isinstance(v, list):
        return [str(x.get("text") if isinstance(x, dict) else x)[:400] for x in v if x][:20]
    return list(t.get("brain_suggest") or [])


def templates():
    return rjson(STORE / "templates.json", []) or []


def template_api(d):
    a = d.get("action", "")
    with _plock:
        ts = templates()
        if a == "save":
            t = TASKS.get(d.get("task", ""))
            if not t:
                return {"error": "Không tìm thấy task"}
            subs = [s for m in task_detail(t)["messages"] if m["role"] == "work" for s in m.get("subtasks", [])]
            asks = [m["text"] for m in t.get("messages", []) if m["role"] == "user" and m.get("text")]
            r = t.get("recipe") or {}
            tp = {"id": "w" + datetime.datetime.now().strftime("%y%m%d%H%M%S"),
                  "name": (d.get("name") or t["title"]).strip()[:80], "created": now(), "from": t["id"],
                  "mode": t.get("mode", "balanced"), "project": t.get("project", ""),
                  "asks": asks[:20], "criteria": r.get("criteria", "")[:8000],
                  "steps": [{"title": s.get("title", ""), "worker": s.get("worker", ""), "model": s.get("model", "")} for s in subs][:40]}
            ts.append(tp)
            wjson(STORE / "templates.json", ts)
            t["tpl_saved"] = tp["id"]
            tsave(t)
            return {"ok": True, "id": tp["id"]}
        if a == "delete":
            wjson(STORE / "templates.json", [x for x in ts if x["id"] != d.get("id")])
            return {"ok": True}
    return {"error": "bad request"}


TPL_STOP = {"cho", "toi", "tao", "lam", "giup", "voi", "cua", "va", "cac", "nhung", "mot", "nay", "theo", "trong", "the",
            "and", "for", "with", "this", "that", "ban", "lai", "duoc", "khong", "can", "file", "task"}


def tpl_words(t):
    txt = unicodedata.normalize("NFD", (t.get("title", "") + " " + (t.get("text") or "")[:200]).lower())
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn").replace("đ", "d")
    return {w for w in re.findall(r"[a-z]{2,}", txt) if w not in TPL_STOP}


def tpl_suggest(t):
    """Gợi ý Save Template: task Xong, không phải Code mode, chưa dùng template, và đã có ít nhất 1 task khác CÙNG LOẠI
    đã xong (tức là từ 2 task cùng loại trở lên) -> việc này hay lặp lại."""
    if t.get("status") != "done" or t.get("kind") == "code" or t.get("template") or t.get("tpl_saved"):
        return False
    me = tpl_words(t)
    if len(me) < 2:
        return False
    for x in list(TASKS.values()):
        if x is t or x.get("status") != "done" or x.get("kind") == "code":
            continue
        o = tpl_words(x)
        if o and len(me & o) / len(me | o) >= 0.4:
            return True
    return False
