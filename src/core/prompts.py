# Maestro — core/prompts.py: Hướng dẫn cho AI (CLAUDE.md theo mô-đun, Code mode, Codex làm PM), prompt mỗi lượt, bài học đã duyệt.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

MODE_RULES = {          # v14: mỗi chế độ 1 dòng; phần khác nhau thật (model, mức suy nghĩ, trần worker, Giám sát) do app tự đặt
    "code": "",
    "pro": "- Ưu tiên chất lượng.",
    "balanced": "- Cân bằng chất lượng, thời gian và hạn mức.",
    "turbo": "- Ưu tiên tốc độ: bắt tay làm ngay, không làm mẫu thử, không làm thêm ngoài yêu cầu.",
    "eco": "- Tiết kiệm hạn mức: việc làm được bằng lệnh/script thì không gọi AI; không làm thêm ngoài yêu cầu.",
}


IMG_RE = re.compile(r"ảnh|hình|image|png|jpe?g|webp|keyframe|storyboard|moodboard|concept ?board|asset|banner|icon|thumbnail|poster|sprite|"
                    r"art\b|artwork|vẽ|render|minh hoạ|minh họa|background|\bbg\b|logo|gif", re.I)
EDIT_RE = re.compile(r"sửa|chỉnh|edit|cập nhật|thay|đổi tên|xóa|xoá|clear|\.xlsx|\.docx|\.pptx|\.csv|[A-Za-z]:\\|\\\\|/Users/", re.I)


JUDGE_MODES = ("pro", "code")        # chế độ tự bật Giám sát (Balanced: tắt mặc định — chỉ giao việc + xem 1 lần, như v1.7)


def judge_on(t):
    """Nút Giám sát của task; chưa bấm thì theo chế độ (Pro, Code bật)."""
    v = t.get("judge")
    return bool(v) if v is not None else norm_mode(t.get("mode")) in JUDGE_MODES


def md_needs(t):
    """Task cần đọc file hướng dẫn chuyên biệt nào (theo mọi tin nhắn + file đính kèm của task)."""
    text = " ".join([t.get("text", "")] + [m.get("text", "") for m in t.get("messages", []) if m.get("role") == "user"])
    inp = tdir(t) / "input"
    names = [f.name.lower() for f in inp.iterdir()] if inp.is_dir() else []
    need = set()
    if IMG_RE.search(text) or any(n.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) for n in names):
        need.add("image")
    if "_danhdau" in text or "đánh dấu" in text.lower() or any("_danhdau" in n for n in names):
        need.add("markup")
    if GOOGLE_LINK.search(text) or re.search(r"google (drive|sheet|doc|slide)", text, re.I):
        need.add("drive")
    if EDIT_RE.search(text) or "drive" in need:
        need.add("edit")
    return need


def md_image(pm):
    return f"""# Ảnh & storyboard
- Storyboard: mặc định viết BẢNG CHỮ (cảnh, thời lượng, hình ảnh, chữ trên màn hình, âm thanh, CTA). Chỉ vẽ khung hình khi người
  dùng yêu cầu rõ (keyframe/vẽ/render storyboard).
- Số ảnh theo đúng yêu cầu: yêu cầu 1 board/bảng/lưới (concept board, moodboard...) -> vẽ CẢ BOARD TRONG 1 ẢNH (các phương án
  nằm sẵn trong ảnh, có nhãn); yêu cầu từng ảnh/file riêng -> vẽ riêng. Không tự tách ra rồi ghép, không tự gộp.
- Tạo ảnh: {"gemini (--write) hoặc codex ($imagegen); bên nào lỗi/hết lượt vẽ thì bạn quyết chuyển bên kia" if pm != "codex" else "gemini (--write), lỗi thì tự tạo ($imagegen)"}.
  Không bên nào vẽ được: viết prompt vào OUTPUT/prompts/, hỏi người dùng (question.md) dán vào ChatGPT, lưu ảnh vào
  OUTPUT/images/ rồi trả lời "xong"; sau đó kiểm tra như thường.
- Đề bài vẽ: ghi rõ kích thước, nền (trong suốt hay không), phong cách, ảnh mẫu (đường dẫn), chữ trên ảnh (nguyên văn).
- Ảnh PNG: kiểm tra size/nền trong suốt bằng lệnh check png của app trước khi xem bằng mắt.
"""


def md_markup(pm):
    return """# Ảnh có đánh dấu
- Ảnh "_danhdau.png" người dùng gửi: tin nhắn có dòng [Đánh dấu trên ...] với toạ độ pixel thật của ảnh gốc. Đỏ = lỗi/cần xoá;
  cam = cần xem/sửa/thêm; số 1,2,3 khớp ghi chú "1) ... 2) ...". Sửa ĐÚNG vùng được đánh dấu.
- BẠN đánh dấu ảnh (báo lỗi QA, trước/sau): Pillow, đọc kích thước trước; khung bo góc (radius ~14, nét ~5px với ảnh ~1400px,
  lề 18px); đường dẫn 25–35px cùng độ dày; nhãn 1–3 chữ cùng màu, đặt sát, KHÔNG viền trắng; đỏ #E63946 chỉ cho lỗi/xoá, cam
  #FF9F1C cho phần còn lại; 2–6 chỗ/ảnh (nhiều hơn: đánh số + chú thích); không resize; xem lại ảnh trước khi giao; liệt kê
  từng chỗ (số, màu, toạ độ, ý nghĩa).
"""


def md_drive(pm):
    if pm == "codex":
        return """# Google Drive / Sheets / Docs / Slides
- Sửa TRỰC TIẾP file Google (giữ link): tự dùng kết nối Google Drive của bạn nếu có.
- Không có: file chia sẻ "bất kỳ ai có link" thì tải bản xuất (Sheets .../export?format=xlsx, Docs .../export?format=docx,
  Slides .../export/pptx), sửa, lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace). File riêng tư: hỏi người
  dùng tải về gửi kèm. Không đổi quyền chia sẻ file.
"""
    return """# Google Drive / Sheets / Docs / Slides
- Công cụ Google Drive của BẠN (mcp__claude_ai_Google_Drive, nạp bằng ToolSearch): tìm, đọc, tải, tạo file — KHÔNG sửa được ô.
  Chỉ đọc/tóm tắt: tự đọc.
- SỬA TRỰC TIẾP file Google (giữ link): giao codex kèm --drive (ChatGPT dùng kết nối Google Drive của nó). Ghi rõ link, tab/vùng
  cần sửa, phần phải giữ (tiêu đề, định dạng, checkbox...); sửa xong giao codex đọc lại để kiểm tra.
- Codex không có kết nối / bị chặn: tải file về work/drive/<tên>/ (.xlsx/.docx/.pptx), giao --edit work/drive/<tên>, kiểm tra,
  cập nhật lại trên Drive nếu công cụ cho phép; không thì lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace).
  Không đổi quyền chia sẻ file.
"""


def md_edit(pm):
    return """# Sửa file (--edit)
- --edit <thư mục>: worker SỬA TRỰC TIẾP file trong thư mục đó (file người dùng yêu cầu sửa, OUTPUT, file Drive đã tải về). Dùng
  khi sửa nhiều/lặp lại (nhiều file, bảng tính lớn) để khỏi tự chép/sửa. Chọn thư mục NHỎ NHẤT chứa file cần sửa (cả ổ/thư mục
  người dùng sẽ bị từ chối). App tự sao lưu vào work/<ID>/vN/_backup; status.json có "edited" = file đã đổi. Mở lại file đã sửa
  để kiểm tra.
"""


MD_MODULES = {"image": md_image, "markup": md_markup, "drive": md_drive, "edit": md_edit}
MD_TITLES = {"image": "tạo ảnh, storyboard", "markup": "ảnh có đánh dấu (đọc/vẽ khung lỗi)",
             "drive": "Google Drive/Sheets/Docs/Slides (đọc, sửa trực tiếp)", "edit": "worker sửa file trực tiếp (--edit)"}


REPLY_STYLES = {        # Cài đặt > Kiểu trả lời (của từng người dùng; mặc định: ngắn gọn)
    "short": """Kiểu trả lời: NGẮN GỌN, DỄ HIỂU (người dùng có thể không rành kỹ thuật).
- Câu đầu trả lời thẳng câu hỏi hoặc nêu kết quả. Dùng lời thường, tránh thuật ngữ; thường 3-10 dòng.
- Chi tiết kỹ thuật (file đã sửa, lệnh đã chạy, nguyên nhân sâu) chỉ ghi khi cần, đặt CUỐI câu trả lời dưới dòng "## Chi tiết"
  (app tự thu gọn). Việc người dùng phải làm và lỗi làm kết quả chưa dùng được luôn nằm ở phần trên.
Ví dụ: hỏi "Sao task chạy lâu vậy?" -> "Do ChatGPT hết lượt vẽ lúc 14:10 nên 3 ảnh cuối phải chờ. Tôi đã chuyển sang Gemini,
khoảng 5 phút nữa xong." / Xong việc -> "Xong 5 banner 1080x1080, ở thư mục 20261127-banners.\"""",
    "detail": """Kiểu trả lời: CHI TIẾT (người dùng làm kỹ thuật): kết luận trước, sau đó đủ chi tiết kỹ thuật cần thiết (file, lệnh, nguyên nhân).""",
}


BRAIN_INLINE = 1000           # ký tự bộ nhớ dự án nhúng thẳng vào hướng dẫn


def workers_md(t, py, app, on):
    """Số worker song song: Claude tự quyết; trần (máy, Eco, Free) do app ép bằng mã (worker_slot)."""
    if not on:
        return ""
    if LOW_SPEC or norm_mode(t.get("mode")) == "eco" or free_setup():
        return "- Số worker: 1 worker một lúc (app tự giới hạn)."
    return (f"- Số worker song song: bạn tự quyết theo số việc độc lập, độ nặng (vẽ ảnh, file lớn, research dài = nặng), hạn mức còn lại\n"
            f"  và giờ reset của bên sẽ làm (app gửi kèm tin nhắn mỗi lượt; xem lại: \"{py}\" \"{app}\" quota). Trần {MAX_PER_TASK}/task, app tự\n"
            f"  xếp hàng. Lô cùng phong cách: làm 1 mẫu, đạt rồi mới chạy song song; việc nhẹ thì gộp, không chia vụn.")


def write_rules(t, pm):
    """Hướng dẫn chuyên biệt: ghi ra ./rules/*.md, Claude chỉ đọc khi task cần (CLAUDE.md lõi ngắn)."""
    d = wdir(t) / "rules"
    d.mkdir(parents=True, exist_ok=True)
    for k, fn in MD_MODULES.items():
        (d / f"{k}.md").write_text(fn(pm), encoding="utf-8")


def write_claude_md(t):
    py, app = py_console(), str(APP)
    mode = norm_mode(t.get("mode"))
    pm = t.get("pm") or "claude"
    agy_ok = bool(find_bin("agy")) and CFG["ais"].get("gemini", {}).get("ok") is not False
    on = workers_on(t)
    desc = {"codex": "codex — ChatGPT/Codex: code, CSV/Excel/Word/PowerPoint, script, sửa file, tạo ảnh ($imagegen)",
            "gemini": "gemini — Gemini/Antigravity: research web, tổng hợp, ý tưởng; tạo/sửa ảnh, tạo/sửa file (--write / --edit)"
            + (" (hết lượt/lỗi thì app tự chuyển sang Gemini API key: chỉ trả lời chữ)" if CFG.get("gemini_key") else "")
            if agy_ok else "gemini — Gemini qua API key: CHỈ trả lời bằng chữ (research web, tổng hợp, ý tưởng, viết, đọc ảnh/PDF "
            "gửi kèm: ghi đúng tên file đính kèm trong tasks/<ID>.md); KHÔNG tạo/sửa file -> không dùng --write/--edit"}
    pj = project_of(t)
    brain = (pj or {}).get("brain", "").strip()
    if len(brain) > BRAIN_INLINE:                # dài -> nhúng phần đầu, bản đầy đủ để sẵn file, Claude đọc khi task cần
        (wdir(t) / "rules").mkdir(parents=True, exist_ok=True)
        (wdir(t) / "rules" / "project.md").write_text(brain, encoding="utf-8")
        brain = brain[:brain.rfind(chr(10), 0, BRAIN_INLINE) if chr(10) in brain[:BRAIN_INLINE] else BRAIN_INLINE].rstrip() + (
            chr(10) + "- (còn tiếp — bản đầy đủ: ./rules/project.md; ĐỌC khi task liên quan nhân vật, asset, cơ chế, nguồn, quy cách)")
    brain_md = (f"""## Dự án: {pj['name']} — bộ nhớ dự án (người dùng đã duyệt)
{brain or '(chưa có)'}
- Tuân theo bộ nhớ này (giám khảo cũng chấm theo nó). Thấy quy tắc nên áp dụng cho CẢ dự án mà bộ nhớ chưa có: ghi đề xuất
  vào ./brain_suggest.json dạng ["<1 câu ngắn>", ...]; không tự sửa bộ nhớ, người dùng duyệt.

""") if pj else ""
    tp = t.get("template")
    tpl_md = (f"""## Quy trình mẫu "{tp['name']}" (đã làm đạt trước đây) — làm theo, thay tham số bằng yêu cầu mới
Yêu cầu khi đó: {' / '.join(x[:300] for x in tp.get('asks', [])[:5])}
Các bước: {'; '.join(f"{x['title']} ({x['worker']} {x['model']})".strip() for x in tp.get('steps', []))}
Tiêu chí đã dùng:
{tp.get('criteria', '').strip() or '(không lưu)'}

""") if tp else ""
    watch_on = judge_on(t)                    # nút Giám sát: giám khảo chấm, sửa tới khi đạt
    judge = watch_on and pm == "claude"
    check = f'"{py}" "{app}" check files|png ...'
    if not watch_on:
        nghiem_thu = f"""## Nghiệm thu (Giám sát TẮT — chỉ giao việc)
- Không viết criteria.md, không gọi giám khảo. Nhận file chỉ kiểm tra kỹ thuật nhanh (đủ file, mở được, đúng kích thước:
  {check}); lỗi rõ ràng mới giao lại đúng phần lỗi."""
    elif judge:
        nghiem_thu = f"""## Nghiệm thu (Giám sát BẬT)
- Worker báo "completed" chưa phải là xong: kiểm tra file thật ({check}, xem --help).
- Trước khi giao việc có sản phẩm: viết tasks/criteria.md ngắn, kiểm chứng được (đánh dấu BẮT BUỘC); đổi yêu cầu thì cập nhật.
- File worker làm: app TỰ chấm từng việc vẽ ngay khi xong và tự giao sửa lỗi BẮT BUỘC (status.json có "judge": overall, issues,
  fixed). KHÔNG chấm lại file app đã chấm, KHÔNG chấm cả lô ở cuối; bạn chỉ xử lý phần app không tự sửa được.
- Sản phẩm bạn TỰ làm: "{py}" "{app}" judge --files <thư mục/file> --criteria tasks/criteria.md [--refs "<mẫu đã duyệt>"]
  --brief "<yêu cầu>" --out work/J1; fail thì sửa đúng lỗi nêu, chấm lại (tối đa 4 vòng).
- Lô từ 5 sản phẩm cùng loại: làm 1 mẫu, đạt rồi mới chạy cả lô."""
    else:
        nghiem_thu = f"""## Nghiệm thu (Giám sát BẬT — không có giám khảo Claude)
- Worker báo "completed" chưa phải là xong: kiểm tra file thật ({check}).
- Viết tasks/criteria.md (đánh dấu BẮT BUỘC); giao gemini soát theo đó, sửa và soát lại tới khi đạt (tối đa 4 vòng)."""
    need = md_needs(t)
    rules_md = "\n".join(f"- rules/{k}.md — {MD_TITLES[k]}" + ("  <- task này có vẻ cần: đọc trước khi làm phần đó" if k in need else "")
                         for k in MD_MODULES)
    style = REPLY_STYLES.get(CFG.get("reply_style") or "short", REPLY_STYLES["short"])
    txt = f"""# Task {t['id']}
Thứ tự ưu tiên khi các hướng dẫn khác nhau: tin nhắn hiện tại > Hướng dẫn của người dùng > Ghi nhớ về người dùng (gửi kèm
tin nhắn) > phần còn lại của file này (cách dùng Maestro).

## Hướng dẫn của người dùng
{CFG.get('instructions', '').strip() or '(không)'}
{style}

## Vai trò
Bạn là PM của Maestro: tự lo phần cần tư duy (phân tích, quyết hướng làm, chốt nội dung, đánh giá chất lượng); việc thao tác,
khối lượng (vẽ ảnh, file hàng loạt, code/script, research web dài) ưu tiên giao GPT/Gemini. Task là cuộc trò chuyện nhiều lượt.

## Thư mục
- Nháp: thư mục hiện tại (bị xoá khi task xong). File người dùng gửi: {tdir(t) / "input"}
- OUTPUT (sản phẩm): {out_dir(t)} (người dùng chỉ định nơi khác thì lưu đúng nơi đó và ghi đường dẫn đầy đủ trong câu trả lời). Gom theo nhóm: OUTPUT/<YYYYMMDD>-<nhóm>
  (vd <YYYYMMDD>-icons, ...-screenshots), việc cùng loại chung 1 thư mục. Giao worker kèm --group <nhóm>: app chuyển THẲNG file
  vào đó (bản trước vào _ban_cu/), kiểm/sửa ngay tại chỗ. File tự làm: ghi thẳng vào thư mục nhóm. Xong task: xoá file thử.
- Đọc được mọi ổ ({', '.join(drives())}). Chỉ sửa file có sẵn ở nơi khác khi người dùng yêu cầu sửa chính file đó; app chặn
  xoá/di chuyển file ngoài thư mục task và OUTPUT. Sản phẩm task trước (dùng lại): {STORE / "registry.json"}

## Worker
{chr(10).join('- ' + desc[w] for w in on) or '- (không có — tự làm)'}
Đề bài: tasks/<ID>.md (worker KHÔNG thấy cuộc trò chuyện). Dòng đầu = tên ngắn, rồi 4 phần: Mục tiêu / Bối cảnh (thông tin,
file, mẫu cần biết) / Ràng buộc (kích thước, định dạng, điều không được làm) / Xong khi (điều kiện kiểm được). Rồi chạy:
  "{py}" "{app}" worker codex  --task tasks/S1.md --out work/S1 [--group <nhóm>] [--edit <thư mục>] [--drive] [--bg]
  "{py}" "{app}" worker gemini --task tasks/S2.md --out work/S2 [--group <nhóm>] [--write] [--edit <thư mục>] [--hard] [--bg]
  --write: Gemini tạo file/ảnh; --hard: việc khó; --edit: sửa thẳng file trong thư mục đó (app tự sao lưu); --drive: GPT sửa
  file Google qua kết nối Drive; --bg: giao cả lô rồi chờ MỘT lần: "{py}" "{app}" wait work/S1 work/S2
- Kết quả: work/<ID>/vN/status.json (export_dir, files). Sửa: giao lại cùng --out. Model và thời gian chờ: app tự lo.
- Worker lỗi: app không tự chuyển worker; bạn quyết giao bên kia hay tự làm. Hết lượt: chuyển bên kia, hoặc ghi
  tasks/resume_at.txt (dòng 1: giờ HH:MM; các dòng sau: việc làm tiếp) rồi kết thúc lượt, app tự gọi lại đúng giờ.
  Không viết vòng lặp sleep / chờ ngầm.
{workers_md(t, py, app, on)}

{nghiem_thu}

{brain_md}{tpl_md}## Chế độ: {MODES[mode]}
{MODE_RULES[mode]}
{"- Bước QUÁ khó (đã thử không ra): ghi xhigh vào tasks/effort.txt -> lượt sau chạy mức cao nhất." if mode == 'pro' else ""}

## Hỏi người dùng giữa chừng
Cần người dùng chọn/duyệt mới làm tiếp được: ghi ./question.md (dòng 1: câu hỏi; mỗi lựa chọn 1 dòng "A - <ngắn gọn>", thêm
" (đề xuất)" nếu có) rồi dừng lượt; app hiện thành nút bấm. Câu trả lời không chép lại câu hỏi / các lựa chọn (app tự ghép).

## Đọc thêm khi cần (file trong ./rules/)
{rules_md}
"""
    wdir(t).mkdir(parents=True, exist_ok=True)
    if IS_WIN and wdir(t).name == ".work":
        subprocess.run(["attrib", "+h", str(wdir(t))], creationflags=NO_WINDOW, capture_output=True)
    write_rules(t, pm)
    rules = learn_rules()
    learn_md = (("## Bài học chung của team (đã kiểm chứng)\n" + "\n".join("- " + r for r in rules) + "\n\n") if rules else "")
    anchor = "\n## Hỏi người dùng giữa chừng\n"
    txt = txt.replace(anchor, "\n" + learn_md + (FREE_RULES if free_setup() else "") + anchor, 1)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    if "codex" not in on:
        txt = "\n".join(x for x in txt.split("\n") if '" worker codex ' not in x)
    if t.get("kind") == "code":
        txt = code_md(txt, t, py, app)
    if pm == "codex":
        txt = pm_codex_md(txt)
    full = f'"{py}" "{app}"'                     # lệnh gọi app (đường dẫn dài) chỉ ghi 1 lần, các chỗ khác viết tắt APP
    if "\n## Worker\n" in txt:
        txt = txt.replace("\n## Worker\n", f"\n## Worker\nLệnh app (bên dưới viết tắt APP; khi chạy thay APP bằng nguyên lệnh này): {full}\n", 1)
        j = txt.index("\n", txt.index("Lệnh app (bên dưới"))
        txt = txt[:j] + txt[j:].replace(full, "APP")
    f = wdir(t) / ("AGENTS.md" if pm == "codex" else "CLAUDE.md")
    import hashlib
    h = hashlib.sha1(txt.encode()).hexdigest()
    if t.get("session_started") and t.get("md_hash") != h:
        t["md_changed"] = True          # lượt tới nhắc Claude đọc lại (giữ tới khi đã nhắc)
    t["md_hash"] = h
    f.write_text(txt, encoding="utf-8")


FREE_RULES = """## Chế độ Free (người dùng không có tài khoản AI trả phí)
- Hạn mức rất ít: ưu tiên Gemini (Antigravity hoặc API key) cho research, ý tưởng, viết; tự làm phần code/file bằng lệnh/script.
- Worker báo hết lượt: chuyển ngay cách khác còn dùng được; hết cả thì trả phần đã xong và nói rõ phần còn thiếu.

"""


def pm_codex_md(txt):
    """Hướng dẫn khi ChatGPT (Codex) làm quản lý thay Claude: không có giám khảo Claude, không có công cụ Drive của Claude."""
    txt = txt.replace("## Vai trò\n", "## Vai trò\nBạn là ChatGPT (Codex) làm quản lý thay Claude; không có sub-agent — việc code/file nhỏ tự làm bằng lệnh.\n", 1)
    if IS_WIN:      # Codex chạy lệnh bằng PowerShell: chương trình có đường dẫn trong ngoặc kép phải gọi bằng "&"
        cmd = f'"{py_console()}" "{APP}"'
        txt = txt.replace(cmd, "& " + cmd).replace("\n## Worker\n",
                          "\n## Worker\n(Lệnh chạy trong PowerShell: giữ dấu & ở đầu mỗi lệnh gọi app.)\n", 1)
    return txt


def pm_prompt_file(d, prompt):
    """ChatGPT làm quản lý: đề bài ghi ra file, dòng lệnh chỉ trỏ tới file. Trên Windows codex.cmd chạy qua cmd.exe:
    đề bài dài (>8.000 ký tự) bị cắt, dấu " & | % có thể bị hiểu thành lệnh."""
    (d / ".pm_prompt.md").write_text(prompt, encoding="utf-8")
    return "Đọc toàn bộ yêu cầu của lượt này trong file ./.pm_prompt.md (UTF-8) rồi làm đúng theo đó."


def md_name(t):
    return "AGENTS.md" if t.get("pm") == "codex" else "CLAUDE.md"


IMG_IN = re.compile(r"\.(png|jpe?g|webp|bmp|gif)$", re.I)


def small_imgs(t, files):
    """Ảnh đính kèm: tạo sẵn bản thu nhỏ (cạnh dài <=1568px, JPEG) để Claude đọc nhanh hơn.
    Ảnh đã nhỏ / không có Pillow -> đọc bản gốc."""
    out = []
    try:
        from PIL import Image
    except Exception:
        return out
    src = tdir(t) / "input"
    for n in files:
        f = src / n
        if not IMG_IN.search(n) or not f.exists():
            continue
        try:
            with Image.open(f) as im:
                w, h = im.size
                if max(w, h) <= 1568 and f.stat().st_size <= 1_500_000:
                    out.append((n, n, w, h))
                    continue
                dst = src / ".small" / (Path(n).stem + ".jpg")
                if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime:
                    dst.parent.mkdir(exist_ok=True)
                    im2 = im.convert("RGB")
                    im2.thumbnail((1568, 1568))
                    im2.save(dst, "JPEG", quality=85)
                out.append((n, f".small/{dst.name}", w, h))
        except Exception:
            pass
    return out


def workers_on(t):
    pm = t.get("pm") or "claude"
    agy_ok = bool(find_bin("agy")) and CFG["ais"].get("gemini", {}).get("ok") is not False
    can = {"codex": bool(find_bin("codex")) and CFG["ais"].get("codex", {}).get("ok") is not False,
           "gemini": agy_ok or bool(CFG.get("gemini_key"))}
    return [w for w in ("codex", "gemini") if w != pm and can[w]]


def live_ctx(t, nxt):
    """Thông tin hay thay đổi (KHÔNG để trong CLAUDE.md, nếu không mỗi lượt Claude phải đọc lại cả bản hướng dẫn):
    bộ nhớ người dùng, số liệu chọn worker + mẫu đề bài (gửi lần đầu phiên và khi đổi), hạn mức (mỗi lượt, vài dòng)."""
    try:
        on = workers_on(t)
        mem = mem_block(t)
        stable = mem
        import hashlib
        h = hashlib.sha1(stable.encode()).hexdigest()
        add = stable if stable and (not t.get("session_started") or t.get("live_hash") != h) else ""
        t["live_hash"] = h
        q = ""
        if on and not (LOW_SPEC or norm_mode(t.get("mode")) == "eco") and route(t, nxt) != "ask":
            try:
                q = "## Hạn mức lúc bắt đầu lượt\n" + quota_md() + "\n"
            except Exception as e:
                q = f"## Hạn mức lúc bắt đầu lượt\nchưa đọc được ({e})\n"
        return ("\n\n---\nThông tin app cập nhật (thay cho bản trước trong phiên):\n" + add + q) if (add or q) else ""
    except Exception as e:
        dlog(f"live_ctx {t.get('id')}: {e}")
        return ""


def rules_hint(t):
    """Task vừa có phần chuyên biệt (ảnh, Drive, sửa file...) chưa nhắc: nhắc đọc đúng file ./rules/<x>.md (1 lần mỗi loại)."""
    try:
        new = [k for k in MD_MODULES if k in md_needs(t) and k not in (t.get("rules_hinted") or [])]
    except Exception:
        return ""
    if not new:
        return ""
    t["rules_hinted"] = list(t.get("rules_hinted") or []) + new
    return "\nTask có phần " + ", ".join(MD_TITLES[k] for k in new) + ": đọc " + ", ".join(f"./rules/{k}.md" for k in new) + " trước khi làm phần đó."


def build_prompt(t, nxt):
    return _build_prompt(t, nxt) + live_ctx(t, nxt)


def _build_prompt(t, nxt):
    kind = nxt.get("kind")
    files = nxt.get("files") or []
    fl = f"\nFile mới trong {tdir(t) / 'input'}: {', '.join(files)}." if files else ""
    sm = small_imgs(t, files)
    if sm:
        fl += ("\nẢnh đính kèm: đọc TẤT CẢ trong 1 lần (nhiều lệnh Read song song), dùng các file sau trong thư mục trên "
               "(ảnh lớn đã thu nhỏ; toạ độ đánh dấu vẫn tính theo kích thước gốc): " +
               "; ".join(r + (f" (gốc {n}, {w}×{h}px)" if r != n else "") for n, r, w, h in sm) + ".")
    text = nxt.get("text", "")
    if t.get("handover") and not t.get("session_started") and kind != "start":
        return (f"Phiên mới cho task đang làm dở (phiên cũ quá dài nên app mở phiên mới cho nhanh; tóm tắt dưới đây thay cho lịch sử "
                f"trò chuyện — cần chi tiết thì xem file trong OUTPUT).\n\n{t['handover']}\n\nTin nhắn mới của người dùng:\n{text}\n"
                + fl + rules_hint(t) + f"\nLàm theo {md_name(t)}.")
    if kind == "start" or not t.get("session_started"):
        base = f"Task mới. Yêu cầu của người dùng:\n\n{t['text']}\n"
        if kind != "start" and text:
            base += f"\nBổ sung của người dùng:\n{text}\n"
        return base + fl + rules_hint(t) + f"\nLàm theo {md_name(t)}."
    if t.get("session_started") and t.get("md_changed"):   # hướng dẫn vừa đổi (app cập nhật / đổi chế độ...) -> đọc lại
        fl += f"\n{md_name(t)} vừa được cập nhật: đọc lại ./{md_name(t)} và áp dụng bản mới nhất (thay cho quy tắc cũ trong phiên)."
    if kind == "answer":
        return f"Người dùng trả lời:\n\n{text}\n{fl}\nTiếp tục task."
    wait = (f'\nWorker vẫn chạy tiếp khi bạn bị ngắt: work/<ID>/vN/status.json còn "running" thì CHỜ bằng lệnh '
            f'"{py_console()}" "{APP}" wait work/<ID> (không giao lại việc đó).')
    if kind == "urgent":
        return (f"Người dùng gửi thêm tin nhắn trong lúc bạn đang làm (app tạm ngắt lượt để bạn nhận ngay; worker đã giao vẫn chạy tiếp):\n\n{text}\n{fl}\n"
                f"Đây là yêu cầu thêm: làm cùng với việc đang dở, không làm lại phần đã xong; tin nhắn bảo đổi hướng hay dừng phần nào thì theo "
                f"tin nhắn. Xem lại phần đã làm (work/*/status.json, file đã tạo) rồi tiếp tục. Làm theo {md_name(t)}.{wait}")
    if kind == "timer":
        return (f"Tới giờ hẹn làm tiếp (bạn đã hẹn ở lượt trước). Việc cần làm tiếp:\n\n{text or '(xem lại lượt trước)'}\n\n"
                f"Xem lại hạn mức (\"{py_console()}\" \"{APP}\" quota) rồi tự quyết: làm tiếp, chuyển bên khác hay hẹn tiếp. "
                f"Làm theo {md_name(t)}.{wait}")
    if kind == "followup":
        return f"Tin nhắn mới của người dùng:\n\n{text}\n{fl}{rules_hint(t)}\nLàm theo {md_name(t)}."
    return f"Task bị dừng giữa chừng. Xem lại phần đã làm (work/*/status.json, file đã tạo) và làm tiếp phần còn dở. Làm theo {md_name(t)}.{wait}"


# Bài học chung đã duyệt (src/learnings.md, mục "Đang áp dụng") nhúng sẵn vào app khi build.
# (Tự rút bài học sau mỗi task + gửi Google Form: đã tắt ở v10.6.)
def learn_src(name, embedded):
    if embedded or VERSION != "dev":
        return embedded
    f = APP.parent / name                       # chạy từ mã nguồn (dev): đọc thẳng file trong src/
    return f.read_text("utf-8") if f.is_file() else ""


def learn_rules():
    """Quy tắc đang áp dụng (mục '## Đang áp dụng' trong learnings.md: mỗi dòng '- ...')."""
    txt = learn_src("learnings.md", LEARN_MD)
    m = re.search(r"(?ms)^## Đang áp dụng\s*\n(.*?)(?=^## |\Z)", txt)
    return [x.strip()[2:].strip() for x in (m.group(1) if m else "").splitlines() if x.strip().startswith("- ")]


def learn_state():
    return {"rules": learn_rules()}


CODE_MD = """## Vai trò (Code mode)
Bạn là kỹ sư phần mềm chính, làm như Claude Code: tự làm là chính, viết/sửa code TRỰC TIẾP trong THƯ MỤC DỰ ÁN: {pdir}
(Maestro tự tạo thư mục này cho task; dự án mới thì tạo code ở đây.) Người dùng nêu đường dẫn một dự án CÓ SẴN -> làm trong
thư mục đó; trước khi sửa, chép bản sao các file sẽ sửa vào work/backup/ (app chỉ tự lưu điểm khôi phục cho thư mục ở trên).
Dự án có sẵn: đọc README / CLAUDE.md / AGENTS.md / package.json / pyproject... trước để biết cách chạy, quy ước code.
Task này là một cuộc trò chuyện nhiều lượt.

## Quy trình code
1. Hiểu: tìm đúng file liên quan (Grep/Glob), đọc phần cần đọc; không đọc tràn lan.
2. Việc lớn: nghĩ kế hoạch ngắn trước (không cần ghi file); việc nhỏ thì làm luôn.
3. Sửa: thay đổi nhỏ nhất đúng yêu cầu, giữ phong cách code sẵn có, không "cải thiện" phần không được nhờ.
4. CHẠY THỬ: chạy build/test/lint có sẵn của dự án; không có test thì chạy chương trình hoặc đoạn kiểm tra ngắn.
   Giao diện web: chạy server local, chụp màn hình bằng trình duyệt có sẵn
   (Edge/Chrome: --headless=new --screenshot=<file.png> --window-size=1280,800 <url>) rồi tự xem ảnh, sửa chỗ lệch.
5. Lỗi -> sửa -> chạy lại tới khi qua (tối đa 3 vòng cho một lỗi; vẫn lỗi thì báo rõ lỗi gì, đã thử gì).
- App tự lưu điểm khôi phục thư mục dự án trước mỗi lượt (nút Khôi phục). Không xoá file/thư mục của dự án ngoài yêu cầu.
- Lệnh chạy lâu (server): chạy nền, nhớ tắt khi xong.

## GPT/Gemini hỗ trợ (chỉ khi có lợi rõ; việc chính bạn tự làm)
- codex: ý kiến thứ hai — soát lỗi/thiết kế (giao danh sách file + điều cần soát, dặn không sửa), hoặc viết 1 phần độc lập
  với --edit <thư mục con của dự án>.
- gemini: tra tài liệu, thư viện, thông báo lỗi lạ trên web. Câu hỏi cần tìm nhiều nguồn trên web (so sánh công cụ,
  thư viện, tài liệu mới) -> giao gemini research rồi tổng hợp, thay vì tự WebSearch nhiều lần; câu hỏi đơn giản -> trả lời luôn.

## Thiếu phần mềm
Cần cài thêm (Node.js, Git, thư viện Python/npm...) mà máy chưa có: KHÔNG tự cài. Ghi ./install_request.json rồi DỪNG lượt:
  {{"why": "<1 câu: cần để làm gì>", "items": [{{"name": "Node.js", "winget": "OpenJS.NodeJS.LTS", "brew": "node"}},
   {{"name": "requests", "pip": "requests"}}, {{"name": "typescript", "npm": "typescript"}}]}}
(winget = gói Windows, brew = gói Mac, pip = thư viện Python, npm = gói Node; chỉ tên gói, không lệnh khác.)
App hiện thông báo + nút Cài cho người dùng; cài xong app tự chạy tiếp lượt sau kèm kết quả cài.
"""


def code_md(txt, t, py="", app=""):
    """Hướng dẫn Code mode: thay mục Vai trò/Nghiệm thu/chế độ bằng quy trình code + chạy thử."""
    code = CODE_MD.format(pdir=t.get("pdir", ""))
    txt = re.sub(r"(?ms)^## Vai trò\n.*?(?=^## )", lambda m: code + "\n", txt, count=1)
    txt = re.sub(r"(?ms)^## Nghiệm thu[^\n]*\n.*?(?=^## )", "", txt, count=1)
    mode_sec = ("## Chế độ: Code (Opus, suy nghĩ medium)\n- Ưu tiên chạy được và đúng; chạy thử thật trước khi báo xong.\n\n"
                 + (f"""## Giám sát (BẬT)
- Có test tự động chạy được và ĐÃ QUA HẾT (test cũ + test bạn viết cho từng ý của yêu cầu) -> KHÔNG gọi giám khảo, báo xong luôn.
- Chỉ gọi giám khảo khi không chạy được test tự động (vd giao diện, script khó test) hoặc vẫn còn test fail mà bạn không sửa được:
  viết yêu cầu + điều phải đạt vào tasks/criteria.md trước, rồi:
  "{py}" "{app}" judge --files <file/thư mục đã sửa> --criteria tasks/criteria.md --brief "<yêu cầu>" --out work/J1
Fail -> sửa đúng lỗi nêu, chạy test lại, chấm lại; lặp tới khi ĐẠT (tối đa 4 vòng; vẫn fail thì báo rõ còn lỗi gì).

""" if judge_on(t) and t.get("pm", "claude") == "claude" else ""))
    txt = re.sub(r"(?ms)^## Chế độ: .*?(?=^## )", lambda m: mode_sec, txt, count=1)   # lambda: đường dẫn Windows (C:\\Users) không bị hiểu là ký tự đặc biệt
    return txt
