# Maestro — workers/judge.py: Lệnh con `judge` (Claude gọi): giám khảo độc lập, tự giao lại file trượt.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

JUDGE_PROMPT = """Bạn là GIÁM KHẢO độc lập nghiệm thu sản phẩm. Chấm NHANH và đúng trọng tâm.
Mở từng file cần chấm bằng Read (chỉ ĐỌC; code/bảng tính có thể chạy lệnh đọc) rồi trả kết quả ngay, không tìm hiểu thêm.

"fail" CHỈ KHI vi phạm tiêu chí BẮT BUỘC hoặc sai rõ ràng với yêu cầu: sai/không giống nhân vật, sai hoặc thiếu nội dung được yêu
cầu, sai kích thước/định dạng/nền, có chữ khi yêu cầu không chữ (hoặc sai chữ), ảnh hỏng/méo/thiếu phần chính.
Lỗi thẩm mỹ, bố cục, màu, ánh sáng, chi tiết nhỏ, "có thể đẹp hơn" -> vẫn "pass", ghi vào notes (không bắt làm lại).

TIÊU CHÍ:
{criteria}

YÊU CẦU RIÊNG CỦA LẦN NÀY:
{brief}

FILE CẦN CHẤM{ref_note}:
{images}
{refs}
Chỉ trả về MỘT khối JSON, không thêm chữ nào khác:
{{"overall": "pass" | "fail", "images": [{{"file": "<tên file>", "verdict": "pass" | "fail", "issues": ["<lỗi BẮT BUỘC cụ thể>"],
"notes": ["<góp ý thẩm mỹ, không bắt sửa>"], "fix": "<chỉ dẫn sửa ngắn nếu fail>"}}], "summary": "<1 câu>"}}"""


CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".java", ".kt", ".cs", ".go", ".rs", ".cpp", ".c", ".h",
            ".swift", ".php", ".rb", ".sh", ".ps1", ".lua", ".gd", ".sql", ".vue")


JUDGE_SKIP = {"TASK.md", "status.json", "worker.log", "result.md", "prompt.md", "last.txt"}   # sổ sách của worker, không chấm


JUDGE_PX = 768                       # cạnh dài ảnh đưa giám khảo (đủ thấy nhân vật, chữ, bố cục; đọc nhanh hơn nhiều)
REF_NEED_RE = re.compile(r"mẫu|giống|reference|\bref\b|so với|đồng bộ|nhất quán|cùng phong cách|base", re.I)


def judge_view(f, d):
    """Ảnh lớn -> bản thu nhỏ (cạnh dài JUDGE_PX) cho giám khảo xem nhanh hơn; giữ nguyên file gốc."""
    if f.suffix.lower() not in IMG_EXT:
        return f
    try:
        from PIL import Image
        im = Image.open(f)
        if max(im.size) <= JUDGE_PX + 100:
            return f
        d.mkdir(parents=True, exist_ok=True)
        o = d / (f.stem + "__xem.jpg")
        im.thumbnail((JUDGE_PX, JUDGE_PX))
        im.convert("RGB").save(o, quality=85)
        return o
    except Exception:
        return f


def judge_once(files, refs, crit, brief, model, timeout, v):
    """1 phiên Claude chấm độc lập -> (status, verdict). Ảnh lớn được thu nhỏ trước cho nhanh."""
    exe = find_bin("claude")
    views = {judge_view(f, v / "xem"): f for f in files}            # file xem -> file gốc
    ctext = crit.read_text(encoding="utf-8")[:12000]
    code = any(f.suffix.lower() in CODE_EXT for f in files)
    if refs and not code and not REF_NEED_RE.search(ctext + "\n" + brief):
        refs = []                                   # tiêu chí không cần so mẫu -> không mở ảnh mẫu (đỡ thời gian)
    prompt = JUDGE_PROMPT.format(
        criteria=ctext, brief=brief,
        images="\n".join(f"- {x}" + (f"   (bản thu nhỏ của {o.name})" if x != o else "") for x, o in views.items()),
        ref_note=" và các MẪU ĐÃ DUYỆT (chuẩn để so sánh)" if refs else "",
        refs=("\nMẪU ĐÃ DUYỆT (chỉ để so sánh, không chấm):\n" + "\n".join(f"- {judge_view(f, v / 'xem')}" for f in refs) + "\n") if refs else "")
    # ảnh/văn bản: chỉ cho Read + giới hạn số bước (mở file rồi trả lời ngay, không đi tìm hiểu thêm) -> nhanh
    cmd = [exe, "-p", prompt, "--output-format", "json", "--allowedTools", "Read,Glob,Grep,Bash" if code else "Read",
           "--model", model]
    for d in dict.fromkeys([str(x.parent) for x in list(views) + refs]):
        cmd += ["--add-dir", d]
    t0 = time.time()
    lim = [] if code else ["--max-turns", str(len(views) + len(refs) + 3)]
    rc, so, se, to = run_limited(cmd + lim, v, timeout)
    if rc != 0 and lim and re.search(r"unknown option|unrecognized|max-turns", (se or "") + (so or ""), re.I) and "{" not in (so or ""):
        rc, so, se, to = run_limited(cmd, v, timeout)      # CLI cũ không có --max-turns
    with open(v / "worker.log", "a", encoding="utf-8") as fh:
        fh.write(f"--- stdout ---\n{so}\n--- stderr ---\n{se}\n")
    verdict = None
    try:
        text = json.loads(so).get("result", "")
        verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        pass
    st = {"seconds": round(time.time() - t0), "exit_code": rc}
    if to:
        st.update(status="timeout", error=f"Quá {timeout}s")
    elif not isinstance(verdict, dict) or "images" not in verdict:
        st.update(status="failed", error="Giám khảo không trả kết quả đọc được: " + (se or so).strip()[-300:])
        verdict = None
    else:
        for it in verdict["images"]:                 # tên bản thu nhỏ -> tên file gốc
            for x, o in views.items():
                if str(it.get("file", "")) in (str(x), x.name) and x != o:
                    it["file"] = str(o)
        bad = [i for i in verdict["images"] if i.get("verdict") != "pass"]
        verdict["overall"] = "fail" if bad or verdict.get("overall") != "pass" else "pass"
        st.update(status="completed" if verdict["overall"] == "pass" else "rejected",
                  error="" if not bad else f"{len(bad)}/{len(verdict['images'])} file chưa đạt")
    return st, verdict


def fix_failed(verdict, files, rnd):
    """File trượt nằm trong work/<ID>/vN do worker làm -> app tự giao lại đúng worker đó sửa đúng lỗi (không qua Claude).
    Trả về {file cũ: [file mới...]} của các việc đã giao lại."""
    groups = {}
    for it in verdict.get("images") or []:
        if it.get("verdict") == "pass":
            continue
        name = str(it.get("file", ""))
        f = next((x for x in files if name in (str(x), x.name)), None)
        vd = next((p for p in ([f] + list(f.parents) if f else []) if re.fullmatch(r"v\d+", p.name) and p.parent.parent.name == "work"), None)
        if not vd and f:                                 # file đã được chuyển thẳng ra thư mục kết quả (export_dir)
            hits = [s.parent for s in Path.cwd().glob("work/*/v*/status.json")     # nhiều việc chung 1 thư mục nhóm: so đúng file
                    if (lambda st: st.get("export_dir") and any(Path(st["export_dir"]) / x == f for x in st.get("files") or []))(rjson(s, {}) or {})]
            vd = max(hits, key=lambda p: int(p.name[1:])) if hits else None
        if not vd or not (vd / "TASK.md").is_file():
            continue
        groups.setdefault(vd, []).append((f, it))
    out, threads = {}, []

    def run(vd, items):
        st0 = rjson(vd / "status.json", {}) or {}
        args = list(st0.get("args") or [])
        if not args:
            return
        fixf = vd.parent / f"fix_r{rnd}.md"
        fixf.write_text((vd / "TASK.md").read_text(encoding="utf-8") + "\n\n---\nGIÁM KHẢO CHẤM TRƯỢT — chỉ sửa đúng các lỗi sau, "
                        "giữ nguyên phần đã đạt và giữ tên file:\n" + "\n".join(
                            f"- {f.name}: {'; '.join(map(str, it.get('issues') or []))} -> {it.get('fix', '')}" for f, it in items),
                        encoding="utf-8")
        if "--task" in args:
            args[args.index("--task") + 1] = str(fixf)
        p = subprocess.run([sys.executable, str(APP), "worker", *args], cwd=str(Path.cwd()), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        try:
            ns = json.loads((p.stdout or "").strip().splitlines()[-1])
        except Exception:
            return
        nv = Path(ns.get("out_dir", ""))
        if ns.get("export_dir"):                         # bản sửa đã nằm ở thư mục kết quả
            nv = Path(ns["export_dir"])
        elif ns.get("via") in ("gpt-fallback", "gemini-fallback"):
            nv = nv / ("gpt" if ns["via"].startswith("gpt") else "gemini")
        new = [nv / x for x in ns.get("files") or [] if (nv / x).is_file() and not x.startswith(("gpt/", "gemini/"))]
        for f, _ in items:
            out[str(f)] = [x for x in new if x.name == f.name] or new
    for vd, items in groups.items():
        th = threading.Thread(target=run, args=(vd, items))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    return out


def keep_previous(orig, new):
    """Bản sửa không tốt hơn: đưa bản trước (app đã chuyển vào _ban_cu/) về lại đúng tên, bản sửa vào _ban_cu/ (không mất file).
    Chỉ làm được khi bản sửa thay đúng chỗ bản trước (file đã ở thư mục kết quả). Trả True nếu đã đổi lại."""
    try:
        if Path(new) != Path(orig) or not Path(new).is_file():
            return False
        bc = Path(orig).parent / "_ban_cu"
        olds = sorted(bc.glob(f"{orig.stem}_v*{orig.suffix}"), key=lambda f: f.stat().st_mtime) if bc.is_dir() else []
        if not olds:
            return False
        k = bc / f"{orig.stem}_ban_sua_truot{orig.suffix}"
        i = 2
        while k.exists():
            k = bc / f"{orig.stem}_ban_sua_truot_{i}{orig.suffix}"
            i += 1
        shutil.move(str(new), str(k))
        shutil.move(str(olds[-1]), str(orig))
        return True
    except Exception as e:
        dlog(f"keep_previous {orig}: {e}")
        return False


def cmd_judge(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="judge", description="Giám khảo độc lập (phiên Claude mới) nghiệm thu file theo tiêu chí")
    ap.add_argument("--files", "--images", dest="images", required=True, nargs="+",
                    help="thư mục hoặc file sản phẩm cần chấm (tối đa 12 file/lần)")
    ap.add_argument("--criteria", required=True, help="file .md tiêu chí nghiệm thu của task")
    ap.add_argument("--refs", help="thư mục mẫu đã được người dùng duyệt (tùy chọn)")
    ap.add_argument("--brief", default="(không)", help="yêu cầu riêng của lần này: pose, số áo, kích thước...")
    ap.add_argument("--out", required=True, help="vd work/J1")
    ap.add_argument("--model")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--rounds", type=int, default=1, help="số vòng app tự giao lại file trượt cho worker (0 = không)")
    a = ap.parse_args(argv)
    imgs = []
    for p in a.images:
        p = Path(p).resolve()
        imgs += sorted(f for f in p.rglob("*") if f.is_file() and "xem" not in f.parts
                       and f.name not in JUDGE_SKIP and not f.name.startswith("fix_r")) if p.is_dir() else [p]
    imgs = [f for f in imgs if f.is_file()][:12]
    refs = sorted(f for f in Path(a.refs).resolve().iterdir() if f.is_file())[:5] if a.refs and Path(a.refs).is_dir() else []
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out / f"v{n}").exists():
        n += 1
    v = out / f"v{n}"
    v.mkdir()
    (v / "TASK.md").write_text(f"Chấm độc lập {len(imgs)} file\n\n{a.brief}", encoding="utf-8")
    code = any(f.suffix.lower() in CODE_EXT for f in imgs)
    # nhanh: không phải code thì dùng model nhỏ (Pro: sonnet); code giữ sonnet như cũ
    model = a.model or ("sonnet" if code or os.environ.get("AIO_MODE") == "pro" else "haiku")
    timeout = a.timeout or (600 if code else 300)
    st = {"worker": "judge", "model": model, "out_dir": str(v)}
    wjson(v / "status.json", {**st, "status": "running"})
    crit = Path(a.criteria)
    if not find_bin("claude") or not crit.is_file() or not imgs:
        st.update(status="failed", error="Chưa cài Claude" if not find_bin("claude") else f"Không thấy tiêu chí {crit}" if not crit.is_file() else "Không có file để chấm")
    else:
        todo, final, fixed, rnd = list(imgs), {}, [], 0
        while True:
            r, verdict = judge_once(todo, refs, crit, a.brief, model, timeout, v)
            st.update({k: x for k, x in r.items() if k != "error"}, seconds=st.get("seconds", 0) + r.get("seconds", 0))
            if not verdict:
                st["error"] = r.get("error", "")
                break
            for it in verdict["images"]:
                final[str(it.get("file"))] = it
            bad = [i for i in verdict["images"] if i.get("verdict") != "pass"]
            if not bad or code or rnd >= a.rounds:
                break
            rnd += 1
            prev = {str(i.get("file")): i for i in bad}       # bản trước (trượt): để so với bản sửa
            newmap = fix_failed(verdict, todo, rnd)
            if not newmap:
                break                                  # file trượt không do worker làm (vd đã chép sang OUTPUT) -> Claude xử lý
            fixed.append({"round": rnd, "files": {k: [str(x) for x in vv] for k, vv in newmap.items()}})
            for k in newmap:
                final.pop(k, None)
            todo = [x for vv in newmap.values() for x in vv][:12]
            if not todo:
                break
            r2, v2 = judge_once(todo, refs, crit, a.brief, model, timeout, v)       # chấm lại CHỈ bản sửa
            st.update(seconds=st.get("seconds", 0) + r2.get("seconds", 0))
            for it in (v2 or {}).get("images") or []:
                final[str(it.get("file"))] = it
            if not v2:                                         # chấm lại lỗi: giữ kết quả chấm bản trước
                for k in newmap:
                    final.setdefault(k, prev.get(k) or {"file": k, "verdict": "fail"})
            for k, news in newmap.items():                    # bản sửa vẫn trượt và không ít lỗi hơn -> giữ bản trước
                for nf in news:
                    it = final.get(str(nf))
                    old = prev.get(k)
                    if it and old and it.get("verdict") != "pass" and len(it.get("issues") or []) >= len(old.get("issues") or []):
                        if keep_previous(Path(k), nf):
                            final.pop(str(nf), None)
                            final[k] = {**old, "kept_previous": True}
            break                                              # tối đa 1 vòng sửa (G2)
        if final:
            items = list(final.values())
            bad = [i for i in items if i.get("verdict") != "pass"]
            review = {"overall": "fail" if bad else "pass", "images": items, "auto_fix": fixed,
                      "summary": f"{len(items) - len(bad)}/{len(items)} file đạt" + (f" (app đã tự giao sửa {len(fixed)} vòng)" if fixed else "")}
            wjson(v / "review.json", review)
            st.update(status="completed" if not bad else "rejected", review=review,
                      error="" if not bad else f"{len(bad)}/{len(items)} file chưa đạt")
    wjson(v / "status.json", {k: x for k, x in st.items() if k != "review"})
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(0 if st["status"] == "completed" else 1)
