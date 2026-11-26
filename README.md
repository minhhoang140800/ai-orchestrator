# Maestro

Claude điều phối, giao việc cho ChatGPT (Codex CLI) và Gemini (Antigravity CLI) ngay trên máy (Windows, Mac).

- **Cài Windows:** tải `Maestro_m_setup.bat` (luôn tải bản mới nhất), bấm đúp. Mở http://ai-orchestrator.localhost/
- **Cài Mac:** tải `Maestro_mac.zip`, giải nén, chuột phải `Maestro_setup.command` → Open.
- **Cập nhật:** nút Cập nhật trong Cài đặt (app lấy `app.py` mới theo `latest.json`).
- Repo chỉ chứa code. Task, file, đăng nhập nằm trên máy người dùng.

## Cùng phát triển

- **Mã nguồn** nằm trong `src/` (`app.py` backend, `ui.html` giao diện). Kiến trúc, cách chạy thử: xem `CLAUDE.md`.
- File ở gốc repo (`app.py`, `latest.json`, `Maestro_*.bat`, `Maestro_mac.zip`) là **bản đã build** — không sửa tay.
- **Không push thẳng lên `main`:** mọi app đã cài tự cập nhật từ `main`. Hãy tạo nhánh riêng → mở Pull Request;
  chủ repo duyệt, rồi chạy `python src/release.py` để build và phát hành.
- Lịch sử yêu cầu, quy trình, vấn đề đã gặp và quyết định đã chốt: nhánh
  [`lich-su`](https://github.com/minhhoang140800/ai-orchestrator/tree/lich-su).
