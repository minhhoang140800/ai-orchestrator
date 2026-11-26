# Maestro — core/prompts.py: Hướng dẫn cho AI (CLAUDE.md theo mô-đun, Code mode, Codex làm PM), prompt mỗi lượt, bài học đã duyệt.
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

MODE_RULES = {
    "code": "",          # Code mode có quy trình riêng (CODE_MD)
    "pro": """- Ưu tiên chất lượng và thời gian. Song song tối đa {n} worker cho phần độc lập.
- Giám khảo (khi Giám sát bật) được dùng model mạnh.""",
    "balanced": """- Cân bằng chất lượng, thời gian, hạn mức. Song song tối đa {n} worker cho phần độc lập; tuần tự khi phụ thuộc.
- Model nhỏ/nhanh cho việc đơn giản, mạnh cho việc khó.""",
    "turbo": """- TỐC ĐỘ TỐI ĐA: bắt tay làm ngay, kế hoạch vài dòng, không hỏi lại trừ khi thiếu dữ kiện bắt buộc. Song song tối đa {n} worker.
- Không mẫu thử trước lô; Giám sát tắt thì không vòng sửa: chỉ check kỹ thuật, sai thì sửa đúng chỗ; tự xem sản phẩm tối đa 1 lần.
- Không làm thêm ngoài yêu cầu (không preview/ghép so sánh). Trả kết quả ngay khi đủ dùng; ghi rõ phần chưa kiểm tra kỹ.""",
    "eco": """- TIẾT KIỆM HẠN MỨC: tuần tự, 1 worker một lúc; việc tự làm được bằng lệnh/script thì không gọi AI.
- Giám sát tắt: tối đa 1 vòng sửa. Không làm thêm ngoài yêu cầu.""",
}


IMG_RE = re.compile(r"ảnh|hình|image|png|jpe?g|webp|keyframe|storyboard|moodboard|concept ?board|asset|banner|icon|thumbnail|poster|sprite|"
                    r"art\b|artwork|vẽ|render|minh hoạ|minh họa|background|\bbg\b|logo|gif", re.I)
EDIT_RE = re.compile(r"sửa|chỉnh|edit|cập nhật|thay|đổi tên|xóa|xoá|clear|\.xlsx|\.docx|\.pptx|\.csv|[A-Za-z]:\\|\\\\|/Users/", re.I)


def judge_on(t):
    """Nút Giám sát của task; chưa bấm thì theo chế độ (Pro, Balanced bật)."""
    v = t.get("judge")
    return bool(v) if v is not None else norm_mode(t.get("mode")) in ("pro", "balanced", "code")


def md_needs(t):
    """Task cần mô-đun hướng dẫn nào (theo mọi tin nhắn + file đính kèm của task)."""
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


def md_image(py, app, pm):
    return f"""## Ảnh & storyboard
- Storyboard: mặc định viết BẢNG CHỮ (cảnh, thời lượng, hình ảnh, chữ trên màn hình, âm thanh, CTA). Chỉ vẽ khung hình khi người
  dùng yêu cầu rõ (keyframe/vẽ/render storyboard).
- Số ảnh theo đúng yêu cầu: yêu cầu 1 board/bảng/lưới (concept board, moodboard...) -> VẼ CẢ BOARD TRONG 1 ẢNH (1 lần tạo,
  các phương án nằm sẵn trong ảnh, có nhãn); yêu cầu từng ảnh/file riêng -> vẽ riêng. Không tự tách ra rồi ghép, không tự gộp.
- Tạo ảnh: {"Gemini (--write) trước, lỗi thì codex ($imagegen)" if pm != "codex" else "gemini (--write), lỗi thì tự tạo ($imagegen)"}.
  Không tạo được (lỗi, hết hạn mức): viết prompt vào OUTPUT/prompts/, hỏi người dùng (question.md) dán vào ChatGPT, lưu ảnh
  vào OUTPUT/images/ rồi trả lời "xong"; sau đó check + chấm như trên.
- Ảnh PNG: kiểm tra size/nền trong suốt bằng lệnh check png trước khi xem bằng mắt.

"""


def md_markup(py, app, pm):
    return """## Ảnh có đánh dấu
- Ảnh "_danhdau.png" người dùng gửi: tin nhắn có dòng [Đánh dấu trên ...] với toạ độ pixel thật của ảnh gốc. Đỏ = lỗi/cần xoá;
  cam = cần xem/sửa/thêm; số 1,2,3 khớp ghi chú "1) ... 2) ...". Sửa ĐÚNG vùng được đánh dấu.
- BẠN đánh dấu ảnh (báo lỗi QA, trước/sau): Pillow, đọc kích thước trước; khung bo góc (radius ~14, nét ~5px với ảnh ~1400px,
  lề 18px); đường dẫn 25–35px cùng độ dày; nhãn 1–3 chữ cùng màu, đặt sát, KHÔNG viền trắng; đỏ #E63946 chỉ cho lỗi/xoá, cam
  #FF9F1C cho phần còn lại; 2–6 chỗ/ảnh (nhiều hơn: đánh số + chú thích); không resize; xem lại ảnh trước khi giao; liệt kê
  từng chỗ (số, màu, toạ độ, ý nghĩa).

"""


def md_drive(py, app, pm):
    if pm == "codex":
        return """## Google Drive / Sheets / Docs / Slides
- Sửa TRỰC TIẾP file Google (giữ link): tự dùng kết nối Google Drive của bạn nếu có.
- Không có: file chia sẻ "bất kỳ ai có link" thì tải bản xuất (Sheets .../export?format=xlsx, Docs .../export?format=docx,
  Slides .../export/pptx), sửa, lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace). File riêng tư: hỏi người
  dùng tải về gửi kèm. Không đổi quyền chia sẻ file.

"""
    return """## Google Drive / Sheets / Docs / Slides
- Công cụ Google Drive của BẠN (mcp__claude_ai_Google_Drive, nạp bằng ToolSearch): tìm, đọc, tải, tạo file — KHÔNG sửa được ô.
  Chỉ đọc/tóm tắt: tự đọc.
- SỬA TRỰC TIẾP file Google (giữ link): giao codex kèm --drive (ChatGPT dùng kết nối Google Drive của nó). Ghi rõ link, tab/vùng
  cần sửa, phần phải giữ (tiêu đề, định dạng, checkbox...); sửa xong giao codex đọc lại để kiểm tra.
- Codex không có kết nối / bị chặn: tải file về work/drive/<tên>/ (.xlsx/.docx/.pptx), giao --edit work/drive/<tên>, kiểm tra,
  cập nhật lại trên Drive nếu công cụ cho phép; không thì lưu vào OUTPUT và chỉ cách thay (File > Import > Upload > Replace).
  Không đổi quyền chia sẻ file.

"""


def md_edit(py, app, pm):
    return """## Sửa file (--edit)
- --edit <thư mục>: worker SỬA TRỰC TIẾP file trong thư mục đó (file người dùng yêu cầu sửa, OUTPUT, file Drive đã tải về). Dùng
  khi sửa nhiều/lặp lại (nhiều file, bảng tính lớn) để khỏi tự chép/sửa. Chọn thư mục NHỎ NHẤT chứa file cần sửa (cả ổ/thư mục
  người dùng sẽ bị từ chối). App tự sao lưu vào work/<ID>/vN/_backup; status.json có "edited" = file đã đổi. Mở lại file đã sửa
  để kiểm tra.

"""


MD_MODULES = {"image": md_image, "markup": md_markup, "drive": md_drive, "edit": md_edit}
MD_TITLES = {"image": "tạo ảnh, storyboard", "markup": "ảnh có đánh dấu (đọc/vẽ khung lỗi)",
             "drive": "Google Drive/Sheets/Docs/Slides (đọc, sửa trực tiếp)", "edit": "worker sửa file trực tiếp (--edit)"}


def write_claude_md(t):
    py, app = py_console(), str(APP)
    mode = norm_mode(t.get("mode"))
    mode_name = MODES[mode]
    mode_rules = MODE_RULES[mode].format(n="1 (máy cấu hình thấp)" if LOW_SPEC else str(MAX_WORKERS))
    pm = t.get("pm") or "claude"
    agy_ok = bool(find_bin("agy")) and CFG["ais"].get("gemini", {}).get("ok") is not False
    can = {"codex": bool(find_bin("codex")) and CFG["ais"].get("codex", {}).get("ok") is not False,
           "gemini": agy_ok or bool(CFG.get("gemini_key"))}
    on = [w for w in ("codex", "gemini") if w != pm and can[w]]
    desc = {"codex": "codex — ChatGPT/Codex: code, CSV/Excel/Word/PowerPoint, script, sửa file, tạo ảnh ($imagegen)",
            "gemini": "gemini — Gemini/Antigravity: research web, tổng hợp, ý tưởng; tạo/sửa ảnh, tạo/sửa file (--write / --edit)"
            + (" (hết lượt/lỗi thì app tự chuyển sang Gemini API key: chỉ trả lời chữ)" if CFG.get("gemini_key") else "")
            if agy_ok else "gemini — Gemini qua API key: CHỈ trả lời bằng chữ (research web, tổng hợp, ý tưởng, viết, đọc ảnh/PDF "
            "gửi kèm: ghi đúng tên file đính kèm trong tasks/<ID>.md); KHÔNG tạo/sửa file -> không dùng --write/--edit"}
    pj = project_of(t)
    brain_md = (f"""## Dự án: {pj['name']} — bộ nhớ dự án (người dùng đã duyệt, áp dụng cho mọi task của dự án)
{pj.get('brain', '').strip() or '(chưa có)'}
- Tuân theo bộ nhớ này; giám khảo cũng chấm theo nó (đưa các mục liên quan vào tiêu chí).
- Thấy quy tắc/quyết định nên áp dụng cho CẢ dự án (style, màu, size chuẩn, đặt tên file, KPI, cách đọc file data...) mà
  bộ nhớ chưa có: ghi đề xuất vào ./brain_suggest.json dạng ["<1 câu ngắn>", ...]. KHÔNG tự sửa bộ nhớ; người dùng duyệt.

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
    need = md_needs(t)
    mods = {k: f(py, app, pm) for k, f in MD_MODULES.items()}
    inline = "".join(mods[k] for k in mods if k in need)
    extra = [k for k in mods if k not in need]
    rd = wdir(t) / "rules"
    rd.mkdir(parents=True, exist_ok=True)
    for k in mods:                              # mô-đun không cần lúc này: để sẵn file, cần thì Claude tự đọc
        (rd / f"{k}.md").write_text(mods[k], encoding="utf-8")
    extra_md = ("## Hướng dẫn thêm — CHỈ đọc khi task cần tới\n" + "\n".join(
        f"- {MD_TITLES[k]}: ./rules/{k}.md" for k in extra) + "\n\n") if extra else ""
    txt = f"""# Task {t['id']}

## Hướng dẫn của người dùng (ưu tiên áp dụng)
{CFG.get('instructions', '').strip() or '(không)'}

{("## Về người dùng — Maestro tự ghi nhớ từ các task trước (tham khảo; hướng dẫn ở trên và yêu cầu hiện tại ưu tiên hơn)" + chr(10) + mem_text() + chr(10) + chr(10)) if mem_text() else ""}## Vai trò
Bạn là PM: lên kế hoạch, giao việc, kiểm tra, tổng hợp; tự viết/biên tập văn bản và TỰ xử lý phần cần tư duy (phân tích,
quyết định hướng làm, chốt nội dung, đánh giá chất lượng). Việc thao tác/khối lượng (vẽ/xử lý ảnh, file hàng loạt, code/script,
research web dài) ưu tiên giao GPT/Gemini. Nghiệm thu trên file thật. Task là cuộc trò chuyện nhiều lượt.

## Đường dẫn
- Thư mục hiện tại: nháp/brief/log, bị XÓA khi task xong -> không để sản phẩm ở đây. File người dùng gửi: {tdir(t) / "input"}
- OUTPUT (sản phẩm giao người dùng): {out_dir(t)} — người dùng chỉ định nơi khác thì lưu đúng nơi đó, ghi đường dẫn đầy đủ.
  Mỗi lần xuất sản phẩm: đặt trong thư mục con YYYYMMDD-<tên ngắn> của OUTPUT (vd {datetime.date.today():%Y%m%d}-concept_boards);
  lần sửa sau tạo thư mục mới cùng quy tắc (vd ...-concept_boards_v2), không ghi đè bản trước.
- Được ĐỌC mọi ổ: {', '.join(drives())}. Chỉ sửa file có sẵn khi người dùng yêu cầu sửa chính file đó; không xóa/di chuyển
  file ngoài thư mục hiện tại và OUTPUT. Sản phẩm task trước (dùng lại, tránh làm lại): {STORE / "registry.json"}

## Worker
{chr(10).join('- ' + desc[w] for w in on) or '- (không có — tự làm)'}
Giao việc: ghi yêu cầu đủ ngữ cảnh vào tasks/<ID>.md (dòng đầu = tên ngắn; worker không thấy cuộc trò chuyện), rồi chạy:
  "{py}" "{app}" worker codex  --task tasks/S1.md --out work/S1 [--edit <thư mục>] [--drive] [--timeout 900]
  "{py}" "{app}" worker gemini --task tasks/S2.md --out work/S2 [--write] [--edit <thư mục>] [--hard]
  --write: Gemini được tạo file/ảnh. --hard: việc khó. --edit / --drive: xem mục sửa file / Google Drive.
Nhiều việc: giao cả lô bằng --bg (trả về ngay), rồi chờ MỘT lần: "{py}" "{app}" wait work/S1 work/S2 work/S3
App TỰ lo: chọn model (không truyền --model), giới hạn số worker chạy cùng lúc, thời gian chờ, giao bên kia làm lại khi
worker lỗi/hết lượt/không vẽ ra ảnh (status via=gpt-fallback/gemini-fallback) — KHÔNG tự kiểm tra rồi giao lại.
Kết quả: work/<ID>/vN/ (result.md, status.json, file). Sửa lỗi: giao lại cùng --out. Worker lỗi/không chạy: coi như không có.

## Chia việc (Gemini nhanh, GPT chính xác hơn)
- Research web: Gemini. Code, script, số liệu, Excel/Word/PowerPoint: GPT. Pro: GPT làm trước việc còn lại;
  Balanced/Turbo/Eco: Gemini trước (idea, kịch bản, brief, ảnh), lỗi mới giao lại ĐÚNG PHẦN lỗi cho GPT.
- Việc độc lập: chạy song song (mỗi worker --out riêng; không 2 worker --edit cùng thư mục).

## Tốc độ (quan trọng)
- Cuối tin nhắn có "Đường xử lý" (HỎI ĐÁP / VIỆC NHỎ) do app phân loại: làm đúng theo đó (không criteria, không giám khảo).
- Văn bản worker viết: dặn ghi TOÀN BỘ vào câu trả lời cuối; không chép/viết lại phần đã đạt. Gộp nhiều lệnh đọc/kiểm tra.
- Research: tối đa ~2 phút (trừ khi người dùng yêu cầu nghiên cứu sâu); hết giờ thì dùng phần đã có, ghi rõ phạm vi đã xem.
  Cần xem ảnh (icon, screenshot, banner đối thủ...): KHÔNG tải ảnh gốc. Tối đa 8 app x 5 ảnh, tải BẢN THU NHỎ (Google Play:
  thay đuôi "=..." của link ảnh play-lh.googleusercontent.com bằng "=w320") SONG SONG trong 1 script (timeout 15 giây/ảnh,
  lỗi thì bỏ qua), ghép thành 1 ảnh tổng hợp (contact sheet, có nhãn tên app) rồi xem 1 lần.

## Nghiệm thu
- Worker "completed" chưa phải là xong: kiểm tra file thật. Đo được: "{py}" "{app}" check files|png ... (xem --help).
{"- Giám sát bật + task có sản phẩm: trước khi làm, viết tasks/criteria.md ngắn, kiểm chứng được (đánh dấu BẮT BUỘC); đổi yêu cầu -> cập nhật." + chr(10) if watch_on else "- Giám sát tắt: không viết criteria.md; tự đối chiếu sản phẩm với đúng yêu cầu gốc." + chr(10)}- Chấm chất lượng: {("GIÁM SÁT BẬT — giám khảo độc lập chấm CẢ LÔ ở cuối (sau check kỹ thuật); fail -> sửa đúng lỗi nêu, chấm lại "
  "file đã sửa, lặp tới khi ĐẠT (tối đa 4 vòng; vẫn fail thì báo rõ còn lỗi gì):") if judge else
  ("GIÁM SÁT BẬT — không có giám khảo Claude: giao gemini soát theo criteria.md, sửa và soát lại tới khi đạt (tối đa 4 vòng).")
  if watch_on else "Giám sát TẮT — không gọi giám khảo; tự xem lại theo yêu cầu 1 lần, lỗi nhỏ thẩm mỹ thì ghi chú cho người dùng."}
{f"""    "{py}" "{app}" judge --files <thư mục/file> --criteria tasks/criteria.md [--refs "<mẫu đã duyệt>"] --brief "<yêu cầu>" --out work/J1
  Chấm NGAY trong work/<ID>/vN (trước khi chép sang OUTPUT); file trượt do worker làm thì app TỰ giao lại worker đó sửa rồi
  chấm lại (review.json: auto_fix) — bạn chỉ xử lý phần app không tự sửa được, rồi chép bản đạt sang OUTPUT.
""" if judge else ""}  Lô lớn (từ 5 sản phẩm cùng loại): làm 1 mẫu, tự xem rồi mới chạy cả lô. Lỗi: sửa tối đa 2 lần/sub-task, quá thì báo partial.

{inline}{extra_md}{brain_md}{tpl_md}## Chế độ: {mode_name}
{mode_rules}
- Worker báo "limit": chuyển worker khác hoặc tự làm; không đảm bảo chất lượng thì hỏi người dùng.{" Bước QUÁ khó (đã thử không ra): ghi xhigh vào tasks/effort.txt -> lượt sau chạy mức cao nhất." if mode == 'pro' else ""}

## Ghi lại (để giao diện hiển thị)
- Người dùng góp ý hoặc chốt điều gì (chỉ khi đó): cập nhật tasks/decisions.json, gom theo phần việc, sửa/gộp mục cũ:
  [{{"group": "<phần việc>", "items": [{{"type": "feedback"|"decision", "text": "<1 câu>", "status": "open"|"done"}}]}}]
  Có file này thì đọc đầu mỗi lượt, theo đúng các quyết định đã chốt, không hỏi lại.
- Task nhiều phần: xong phần nào hữu ích thì ghi ngay vào ./stages/<số>.md (giao diện hiện cho người dùng đọc trong lúc làm).

## Hỏi / nghiên cứu / đề xuất ≠ làm
Tin nhắn là CÂU HỎI ("có thể … không", "làm thế nào", "có cách nào", "nên … không", "tại sao") hoặc nhờ research/phân tích/
đề xuất/so sánh -> CHỈ trả lời (kết luận, phương án, ưu nhược, công sức); không sửa file, không tạo sản phẩm, không chạy việc dài.
Cuối câu trả lời hỏi: có làm không / làm phương án nào. Chỉ làm khi người dùng bảo làm. Không chắc -> coi là hỏi.

## Khi cần người dùng quyết định
- TRƯỚC khi bắt tay (yêu cầu mới / tin nhắn mới): yêu cầu hiểu được 2 cách ra sản phẩm khác hẳn nhau, thiếu dữ kiện bắt buộc
  (file, link, game nào, số lượng, kích thước, thị trường...), hoặc có nghi vấn mà đoán sai sẽ phải research lâu / làm sai hướng
  -> hỏi NGAY, chưa research, chưa làm: ghi tối đa 3 câu ngắn vào ./question.md, mỗi câu kèm phương án mặc định, rồi DỪNG.
- ĐANG làm mà vướng: mọi AI đều không làm được; kết quả chắc chắn không đạt yêu cầu; phải đổi phương án đã chốt; sắp tốn nhiều
  hạn mức hoặc làm việc không hoàn tác được -> ghi câu hỏi + phương án vào ./question.md rồi DỪNG (giữ phần đã làm).
- Còn lại tự quyết, ghi giả định. Yêu cầu rõ thì làm luôn, không hỏi cho có.

## Kết thúc mỗi lượt
Báo cáo, research, so sánh: viết thẳng trong câu trả lời, KHÔNG tạo file .md/.txt; chỉ tạo file khi sản phẩm vốn là file hoặc
người dùng yêu cầu (lưu vào OUTPUT). Trả lời ngắn: đạt/chưa đạt, điểm cần biết, việc người dùng cần làm. Không liệt kê lại file,
KHÔNG kể quá trình/worker trừ khi ảnh hưởng kết quả. Dùng được **đậm**, `code`, gạch đầu dòng.

## Cấm
Không dùng API key. Không ghi mật khẩu/token vào file.
"""
    wdir(t).mkdir(parents=True, exist_ok=True)
    if IS_WIN and wdir(t).name == ".work":
        subprocess.run(["attrib", "+h", str(wdir(t))], creationflags=NO_WINDOW, capture_output=True)
    rules = learn_rules()
    learn_md = (("## Bài học chung của team (đã kiểm chứng, hợp với hầu hết loại task — áp dụng)\n"
                 + "\n".join("- " + r for r in rules) + "\n\n") if rules else "")
    txt = txt.replace("\n## Cấm\n", "\n" + learn_md + "\n## Cấm\n", 1)
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
    if free_setup():
        txt = txt.replace("\n## Cấm\n", "\n" + FREE_RULES + "\n## Cấm\n", 1)
    f = wdir(t) / ("AGENTS.md" if pm == "codex" else "CLAUDE.md")
    import hashlib
    h = hashlib.sha1(txt.encode()).hexdigest()
    if t.get("session_started") and t.get("md_hash") != h:
        t["md_changed"] = True          # lượt tới nhắc Claude đọc lại (giữ tới khi đã nhắc)
    t["md_hash"] = h
    f.write_text(txt, encoding="utf-8")


FREE_RULES = """## Chế độ Free (người dùng không có tài khoản AI trả phí — áp dụng THAY mọi luật chế độ ở trên khi mâu thuẫn)
- Hạn mức rất ít: làm tuần tự, 1 worker tại một thời điểm; không làm mẫu thử; Giám sát tắt thì tối đa 1 vòng sửa.
- Ưu tiên giao Gemini (Antigravity hoặc API key) cho research, ý tưởng, viết; tự làm phần code/file bằng lệnh/script.
- Worker báo "limit": chuyển ngay sang worker/cách khác còn dùng được, không chờ; hết cả thì trả phần đã xong + nói rõ phần còn thiếu.
- Câu trả lời ngắn, đủ ý; không làm thêm ngoài yêu cầu.
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


def build_prompt(t, nxt):
    kind = nxt.get("kind")
    files = nxt.get("files") or []
    fl = f"\nFile mới trong {tdir(t) / 'input'}: {', '.join(files)}." if files else ""
    sm = small_imgs(t, files)
    if sm:
        fl += ("\nẢnh đính kèm: đọc TẤT CẢ trong 1 lần (nhiều lệnh Read song song), dùng các file sau trong thư mục trên "
               "(ảnh lớn đã thu nhỏ; toạ độ đánh dấu vẫn tính theo kích thước gốc): " +
               "; ".join(r + (f" (gốc {n}, {w}×{h}px)" if r != n else "") for n, r, w, h in sm) + ".")
    text = nxt.get("text", "")
    if kind == "start" or not t.get("session_started"):
        base = f"Task mới. Yêu cầu của người dùng:\n\n{t['text']}\n"
        if kind != "start" and text:
            base += f"\nBổ sung của người dùng:\n{text}\n"
        return base + fl + ROUTE_NOTE[route(t, nxt)] + f"\nLàm theo {md_name(t)}."
    if t.get("session_started") and t.get("md_changed"):   # hướng dẫn vừa đổi (app cập nhật / đổi chế độ...) -> đọc lại
        fl += f"\n{md_name(t)} vừa được cập nhật: đọc lại ./{md_name(t)} và áp dụng bản mới nhất (thay cho quy tắc cũ trong phiên)."
    if kind == "answer":
        return f"Người dùng trả lời:\n\n{text}\n{fl}\nTiếp tục task."
    wait = (f'\nWorker vẫn chạy tiếp khi bạn bị ngắt: work/<ID>/vN/status.json còn "running" thì CHỜ bằng lệnh '
            f'"{py_console()}" "{APP}" wait work/<ID> (không giao lại việc đó).')
    if kind == "urgent":
        return (f"Người dùng vừa gửi tin nhắn trong lúc bạn đang làm (bạn đã bị ngắt):\n\n{text}\n{fl}\n"
                f"Xem lại phần đã làm (work/*/status.json, file đã tạo), điều chỉnh theo tin nhắn này rồi tiếp tục. Làm theo {md_name(t)}.{wait}")
    if kind == "followup":
        return f"Tin nhắn mới của người dùng:\n\n{text}\n{fl}{ROUTE_NOTE[route(t, nxt)]}\nLàm theo {md_name(t)}."
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
6. Báo cáo bằng lời dễ hiểu: đã đổi gì (file + ý chính), đã chạy thử gì và kết quả, còn gì chưa xong. Không dán code dài.
- Git: KHÔNG commit/push/đổi nhánh trừ khi người dùng yêu cầu. App tự lưu điểm khôi phục trước mỗi lượt (nút Khôi phục).
- Không xoá file/thư mục của dự án ngoài yêu cầu. Không ghi mật khẩu/key vào code.
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
    """Hướng dẫn Code mode: thay mục Vai trò/Chia việc/Nghiệm thu/chế độ bằng quy trình code + chạy thử."""
    code = CODE_MD.format(pdir=t.get("pdir", ""))
    txt = re.sub(r"(?ms)^## Vai trò\n.*?(?=^## )", lambda m: code + "\n", txt, count=1)
    for sec in ("Chia việc (Gemini nhanh, GPT chính xác hơn)", "Nghiệm thu", "Ảnh có đánh dấu", "Ảnh & storyboard"):
        txt = re.sub(r"(?ms)^## " + re.escape(sec) + r"\n.*?(?=^## )", "", txt, count=1)
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
