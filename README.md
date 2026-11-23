# AI Orchestrator

Claude làm PM, giao việc cho ChatGPT (Codex CLI) và Gemini (Antigravity CLI) trên máy Windows.

- **Cài:** tải file `AI-Orchestrator_v<số mới nhất>.bat`, bấm đúp. Sau đó mở http://ai-orchestrator.localhost/ (app tự chạy nền khi bật máy).
- **Cập nhật:** app tự lấy `app.py` mới từ repo này (kiểm tra `latest.json` mỗi 3 giờ và khi bật máy), áp dụng khi không có task đang chạy.
- Repo chỉ chứa code. Task, file, đăng nhập nằm trên máy người dùng (`%LOCALAPPDATA%\AIOrchestrator`).

`src/` là mã nguồn; `python src/build.py` (đặt `AIO_REPO=owner/repo`) tạo `app.py`, `latest.json`, `AI-Orchestrator.bat`.
