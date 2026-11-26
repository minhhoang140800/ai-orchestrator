# Maestro (repo ai-orchestrator) — hướng dẫn cho Claude Code

## Người dùng & cách làm việc
- Xưng hô tôi – bạn, tiếng Việt, trả lời ngắn, nêu kết luận trực tiếp, không bịa; phần chưa chắc ghi rõ và tách riêng.
- Làm đúng yêu cầu, không mở rộng phạm vi. Chỉ soạn nháp/đề xuất cho phần giao tiếp; quyết định thuộc người dùng.
- Người dùng không phải dev: báo kết quả bằng lời dễ hiểu (đã đổi gì, bấm gì để có), không dán code dài.
- Mỗi lần sửa xong: tăng bản +0.1 và phát hành (mục Phát hành). Người dùng cập nhật bằng nút **Cập nhật** trong app.
- Bảo mật: không lưu secret trong repo; không tự cấp quyền rộng cho AI (vd Gmail/Calendar) khi chưa được yêu cầu.
- Lịch sử yêu cầu, vấn đề đã gặp, quyết định đã chốt: nhánh `lich-su` (`QUY_TRINH_VA_LICH_SU.md`) — đọc trước khi đổi hành vi app, cập nhật sau mỗi bản.

## Sản phẩm
Maestro = web app chạy trên máy người dùng (Windows chính, có bản Mac) tại http://ai-orchestrator.localhost/
(cổng 80, lỗi thì :8765). Claude Code (headless `claude -p`) làm PM, giao việc cho worker ChatGPT (Codex CLI)
và Gemini (Antigravity CLI `agy`), dùng gói Claude Max / ChatGPT Plus / Google AI Pro sẵn có, không API key.
Không có Claude trả phí: ChatGPT (Codex) làm PM (`pick_pm`, hướng dẫn ghi vào AGENTS.md). Không có gói trả phí nào: chế độ Free
(`free_setup`, `FREE_RULES`) + Gemini API key tuỳ chọn (`api_worker`, key chỉ lưu trong `data/config.json`). Worker sửa file thẳng: `--edit`.
Code mode (`kind=code`, `CODE_MD`, `code_checkpoint`/`code_restore`, cài phần mềm thiếu: `install_request.json` → `run_install`).
Learning: task xong → `lessons.json` → `learn_queue` (lọc dữ liệu riêng: `scrub`) → Google Form (`src/learn_form.json`);
tác vụ hẹn giờ Chủ nhật làm theo `learning/WEEKLY.md`, gộp vào `src/learnings.md` (mục "Đang áp dụng" nhúng vào app khi build).

## Mã nguồn (`src/`)
- `app.py` — toàn bộ backend 1 file (Python stdlib; tuỳ chọn pillow/openpyxl/pywinpty). Gồm: HTTP server + API,
  quản lý task (luồng `scheduler`/`watch`), sinh `CLAUDE.md` cho từng task (`write_claude_md`: luật chia việc, chấm,
  chế độ Pro/Balanced/Turbo/Eco, model), lệnh con cho Claude gọi: `worker codex|gemini`, `wait`, `check`, `judge`.
  Model theo chế độ: `MODEL_PLAN`, `pick_model` (tự hạ khi sắp hết hạn mức: `limits.json`, `pressure`).
  Hết hạn mức: `is_limit`, `parse_reset`, `note_limit`. Tự cập nhật: `fetch_update` (GitHub API/raw/jsDelivr),
  `apply_update` → `boot()` chạy bản trong `versions/` có health-check, lỗi thì quay về bản cũ.
- `ui.html` — toàn bộ giao diện (1 file, nhúng vào app.py khi build). Phong cách Porcelain 2.0 (sáng; chat kiểu ChatGPT,
  task kiểu Linear; icon Lucide; lớp CSS "Porcelain 2.0" ở cuối thẻ style). `fonts/` — Geist + Geist Mono (OFL), nhúng khi build, phục vụ ở /fonts/.
- `install.tpl.ps1` — bộ cài Windows (nhúng trong .bat tự giải nén; thông báo ASCII không dấu).
- `install_mac.tpl.sh` — bộ cài Mac (`Maestro_setup.command`, đóng zip để giữ quyền chạy).
- `build.py`, `release.py`, `bench.py` (benchmark trên máy phát triển), icon (`icon.ico`, `icon1024.png`), `RELEASE` (số bản).

## Dữ liệu trên máy người dùng (không nằm trong repo)
- App: `%LOCALAPPDATA%\AIOrchestrator` (Mac: `~/Library/Application Support/Maestro`): `app.py` (bộ khởi động),
  `versions/`, `data/config.json`, `data/debug.log`, `limits.json`, `boot.json`.
- Task/lịch sử: `<thư mục nhận kết quả>/_AI-Orchestrator/` (tasks, projects.json, templates.json).
- Kết quả: `<thư mục nhận kết quả>/<Dự án>/<YYYY-MM-DD>/<HH_MM> <tên task>/`, nháp ở `.work/` (xoá khi task xong).

## Chạy thử trên máy dev
```
cd src
AIO_HOME=/tmp/h AIO_PORT=8827 AIO_NO_BROWSER=1 python app.py      # mở http://127.0.0.1:8827/
```
Kiểm tra cú pháp: `python -c "import ast;ast.parse(open('src/app.py').read())"`; JS trong ui.html: tách thẻ <script> rồi `node --check`.
Có thể giả lập claude/codex/agy bằng script giả đặt trước trong PATH (biến `FAKE_STATE` được app hỗ trợ).

## Phát hành
```
python src/release.py            # +0.1 (hoặc: python src/release.py 8.0)
git add -A && git commit -m "vX.Y: <tóm tắt>" && git push
```
`build.py` tự chặn nếu thiếu địa chỉ cập nhật, lỗi cú pháp hoặc app không khởi động được. File gửi người dùng khi cần cài
lại: `src/dist/Maestro_setup.bat` (Windows), `Maestro_mac.zip` (Mac). Gốc repo giữ: `app.py`, `latest.json`,
`Maestro_setup_latest.bat` (bản cài mới nhất, `Maestro_m_setup.bat` tải từ GitHub), `Maestro_mac.zip`,
`Maestro_m_setup.bat` (src/m_setup.bat), `Maestro_m_uninstall.bat` (src/m_uninstall.bat: [1] giữ data, [2] xoá data).

## Đọc log người dùng gửi
Nút Export log tạo `Maestro-log-*.zip`: `summary.md` (task, lượt làm, sub-task), `debug.log`, `install.log`
(có thể UTF-16 lẫn UTF-8: bỏ byte 0 khi đọc), thư mục từng task (`task.json`, CLAUDE.md, timeline nếu còn).
