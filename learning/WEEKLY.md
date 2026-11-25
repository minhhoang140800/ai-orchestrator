# Cập nhật Maestro hàng tuần (tác vụ hẹn giờ, Chủ nhật ~20h giờ Việt Nam)

Bạn là Claude chạy tự động, không có người trả lời. Làm hết các bước, không hỏi lại. Trả lời cuối: 3–6 dòng tiếng Việt
(đã đổi gì, phát hành bản nào, hoặc vì sao không phát hành).

## 0. Chuẩn bị
- Repo `minhhoang140800/ai-orchestrator`, nhánh `main` (được phép commit và push thẳng lên `main` cho việc này).
- Đọc `CLAUDE.md` của repo trước. Không sửa phần code nào ngoài các mục nêu dưới đây.

## 1. Lấy bài học mới
- `src/learn_form.json` có `"sheet"` (link Google Sheet nhận câu trả lời của Google Form) thì đọc bằng công cụ Google Drive.
  Chưa có -> bỏ qua bước 1–2, chỉ làm bước 3.
- Mỗi dòng: thời điểm + 1 ô JSON (báo cáo ẩn danh: `category`, `lessons`, `subtasks` [worker, model, status, seconds],
  `mode`, `pm`, `plans`, `models`, `release`, `os`). Chỉ lấy dòng mới hơn `learning/state.json` → `last_at`.

## 2. Lọc và gộp bài học vào `src/learnings.md`
- Bỏ bài học có dữ liệu riêng (tên dự án/game/khách hàng/người, link, đường dẫn, số liệu riêng) hoặc mơ hồ, sai, trái
  với hướng dẫn hiện có trong `write_claude_md` của `src/app.py`.
- Gộp các bài học trùng ý thành 1 câu ngắn, rõ, làm theo được. Không giới hạn số quy tắc.
- Với mỗi quy tắc (mới và cũ): xét nó hợp với những loại task nào trong danh sách `CATEGORIES` của `src/app.py`
  (11 loại). Hợp với **ít nhất 10/11 loại** -> mục "Đang áp dụng"; còn lại -> mục "Chưa áp dụng".
  Có thể dùng số liệu báo cáo làm bằng chứng (vd worker/model hay lỗi ở loại việc nào); quy tắc cũ bị số liệu mới bác bỏ thì bỏ.
- Ghi 1 dòng vào mục "Lịch sử": ngày, số báo cáo đọc, số quy tắc thêm/bỏ.

## 3. Kiểm tra model của các hãng
- Nguồn: danh sách `models` trong báo cáo (model thật trên máy người dùng) + trang chính thức của OpenAI (Codex CLI),
  Google (Gemini / Antigravity CLI), Anthropic (Claude Code). Chỉ nhận model có ở trang chính thức HOẶC thấy trên ít nhất 1 máy.
- Cập nhật nếu cần trong `src/app.py`: `CODEX_MODELS` (mới nhất trước, giữ 1–2 bản cũ làm dự phòng), `MODEL_PLAN`,
  `GEM_FALLBACK` (tên dòng model Gemini), và mô tả model trong mục "## Model" của `write_claude_md`. Claude dùng bí danh
  `opus` (tự theo bản mới) — chỉ đổi khi bí danh đó bị bỏ.
- Ghi tóm tắt vào mục "Model đã thấy trên máy người dùng" của `src/learnings.md`.

## 4. Phát hành (chỉ khi có thay đổi)
- Không có gì thay đổi -> không phát hành, chỉ cập nhật `learning/state.json` nếu đã đọc báo cáo mới.
- Kiểm tra: `python -c "import ast;ast.parse(open('src/app.py').read())"`; tách `<script>` trong `src/ui.html` rồi `node --check`.
- `python src/release.py` (tự tăng +0.1, tự chặn nếu app không khởi động được). Lỗi -> hoàn tác thay đổi, không push, báo lỗi.
- Cập nhật `learning/state.json` → `last_at` = thời điểm dòng mới nhất đã đọc.
- `git add -A && git commit -m "vX.Y: cập nhật hàng tuần — <tóm tắt>"` rồi `git push origin HEAD:main`.
  Máy người dùng tự cập nhật khi đang rảnh.
