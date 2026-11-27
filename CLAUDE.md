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
Chỉ dùng trong gói: cố định trong code (`PLAN_ONLY_ENV` xoá biến API key, `usage_block`), không công tắc; ngoại lệ duy nhất: Gemini API key.
Code mode (`kind=code`, `CODE_MD`, `code_checkpoint`/`code_restore`, cài phần mềm thiếu: `install_request.json` → `run_install`).
Learning: task xong → `lessons.json` → `learn_queue` (lọc dữ liệu riêng: `scrub`) → Google Form (`src/learn_form.json`);
tác vụ hẹn giờ Chủ nhật làm theo `learning/WEEKLY.md`, gộp vào `src/learnings.md` (mục "Đang áp dụng" nhúng vào app khi build).

## Mã nguồn (`src/`) — kiến trúc từ v11
Backend chia module theo trách nhiệm, chỉ Python stdlib (tuỳ chọn pillow/openpyxl/pywinpty). Mọi module chạy CHUNG 1 không gian
tên (không import chéo): khi phát triển `app.py` nạp lần lượt theo `assemble.PARTS`; khi build `assemble()` ghép thành 1 file
`dist/app.py` (cơ chế tự cập nhật/bộ cài vẫn dựa trên 1 file). Thêm module mới: ghi vào `PARTS` đúng thứ tự (tên dùng ngay lúc nạp
— hằng số, lớp cha — phải có ở module trước). `tests/` kiểm tra thứ tự + mỗi tên chỉ định nghĩa 1 lần.
- `app.py` — điểm vào: nạp module, phân lệnh (serve / `worker codex|gemini` / `wait` / `check` / `judge`). `assemble.py` — thứ tự ghép.
- `core/`: `config` (CFG, đường dẫn, máy), `procs` (tiến trình, debug log), `store` (JSON, task.json), `tasks` (vòng đời task, `act`,
  dọn nháp), `router` (lễ tân hỏi đáp/việc nhỏ/đầy đủ), `prompts` (`write_claude_md`, `CODE_MD`, `build_prompt`), `models`
  (`MODEL_PLAN`, `pick_model`), `usage` (hạn mức, `is_limit`, `usage_block`), `scheduler` (`sched_tick`, `start_run`, `watch`).
- `providers/`: `accounts` (đăng nhập, trạng thái, `pick_pm`), `claude`, `codex`, `gemini` (agy + Gemini API key).
- `workers/`: `worker` (`cmd_worker`, `cmd_wait`; worker do Claude chọn, lỗi/hết lượt thì status báo lại — app không tự
  chọn/chuyển worker), `check`, `judge` (Opus 5.5 low `JUDGE_MODEL`, ảnh mẫu `judge_refs`).
- `features/`: `brain` (dự án, template), `codemode`, `updater` (`fetch_update`, `apply_update`, `boot`), `diagnostics`
  (Export log, Clear cache), `system` (tự mở khi bật máy), `memory` (bộ nhớ người dùng tự ghi sau mỗi lượt), `remote` (điện thoại).
- `web/`: `server.py` (class `H`, API, `task_detail`, `serve`), `ui.html` (toàn bộ giao diện, Porcelain 2.0, icon Lucide).
  `fonts/` — Geist + Geist Mono (OFL), nhúng khi build, phục vụ ở /fonts/.
- `install.tpl.ps1` — bộ cài Windows (nhúng trong .bat tự giải nén; thông báo ASCII không dấu).
- `install_mac.tpl.sh` — bộ cài Mac (`Maestro_setup.command`, đóng zip để giữ quyền chạy).
- `build.py`, `release.py`, `bench.py` (benchmark trên máy phát triển), icon (`icon.ico`, `icon1024.png`), `RELEASE` (số bản).

## Dữ liệu trên máy người dùng (không nằm trong repo)
- App: `%LOCALAPPDATA%\AIOrchestrator` (Mac: `~/Library/Application Support/Maestro`): `app.py` (bộ khởi động),
  `versions/`, `data/config.json`, `data/debug.log`, `limits.json`, `boot.json`.
- Task/lịch sử: `<thư mục nhận kết quả>/_AI-Orchestrator/` (tasks, projects.json, templates.json, `memory_auto.md` = bộ nhớ người dùng).
- Kết quả: `<thư mục nhận kết quả>/<Dự án>/<YYYY-MM-DD>/<HH_MM> <tên task>/`, nháp ở `.work/` (xoá khi task xong).

## Chạy thử trên máy dev
```
cd src
AIO_HOME=/tmp/h AIO_PORT=8827 AIO_NO_BROWSER=1 python app.py      # mở http://127.0.0.1:8827/
```
Test (bắt buộc trước khi phát hành): `python -m unittest discover -s src/tests`. JS trong `web/ui.html`: tách thẻ <script> rồi `node --check`.
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
