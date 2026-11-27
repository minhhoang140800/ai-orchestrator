# Maestro — Quy trình làm việc, vấn đề đã gặp và cách giải quyết

Cập nhật đến bản v12.4 (2026-11-26). Source code ở nhánh `main` — đọc kèm `CLAUDE.md` ở đó (kiến trúc, đường dẫn, lệnh build).
Nguồn: lịch sử commit (v1 → v10.2) và các phiên làm việc với Claude Code. Phần chưa kiểm chứng ghi ở mục 6.

---

## 1. Quy trình phát triển (người dùng ↔ AI lập trình)

1. **Nhận yêu cầu** từ người dùng (không phải dev, tiếng Việt, ảnh chụp màn hình + log). Làm đúng phạm vi; phần chỉ "note" thì ghi lại, chưa sửa.
2. **Đọc log nếu có**: file `Maestro-log-*.zip` (nút Export log) → `summary.md`, `debug.log`, `install.log` (UTF-16 lẫn UTF-8, bỏ byte 0), thư mục từng task.
3. **Sửa code** trong `src/app.py` (backend 1 file) và/hoặc `src/ui.html` (giao diện 1 file).
4. **Kiểm tra**:
   - Cú pháp: `python -c "import ast;ast.parse(open('src/app.py').read())"`, `pyflakes`; JS: tách thẻ `<script>` → `node --check`.
   - Chạy app thật với **AI giả**: script `claude`/`codex`/`agy` giả đặt trước trong PATH, biến `FAKE_STATE`, server giả Gemini API + `/api/oauth/usage` + Google Form; `AIO_HOME`, `AIO_PORT`, `AIO_NO_BROWSER=1`.
   - Giao diện: Playwright (bấm thử, chụp màn hình, xem bằng mắt).
   - Code mode: có thể chạy `claude` thật để đo tốc độ/độ đúng (bộ test ẩn).
5. **Phát hành**: `python src/release.py` (+0.1) → build tự chặn nếu lỗi cú pháp/không khởi động được/thiếu địa chỉ cập nhật → commit `vX.Y: <tóm tắt>` → push nhánh làm việc **và** `main` (app tự cập nhật từ `main`).
6. **Báo người dùng** bằng lời dễ hiểu: đã đổi gì, bấm gì để có (nút **Cập nhật**), phần nào chưa kiểm chứng.

Nguyên tắc: không lưu secret trong repo; không tự cấp quyền rộng cho AI (Gmail/Calendar…); phần giao tiếp chỉ soạn nháp; quyết định thuộc người dùng.

---

## 2. Quy trình chạy task trong app

- **Quản lý (PM)**: Claude Code headless (`claude -p`). Không có Claude trả phí → ChatGPT (Codex) làm PM (`pick_pm`, hướng dẫn ghi vào AGENTS.md). Không có gói trả phí nào → chế độ Free + Gemini API key tuỳ chọn.
- **Worker**: ChatGPT (Codex CLI) và Gemini (Antigravity `agy`). Lệnh con: `worker codex|gemini` (`--edit` sửa file thẳng, `--drive` Google Sheets/Docs/Slides, `--bg` chạy nền, `--write`, `--hard`), `wait`, `check`, `judge`.
- **Chia việc**: Claude giữ việc quan trọng/cần tư duy và viết lách; việc khối lượng giao worker. Worker trả đủ nội dung, Claude không viết lại.
- **Chế độ**: Pro / Balanced / Turbo / Eco / Code. Model theo chế độ (`MODEL_PLAN`, `pick_model`), tự hạ model khi sắp hết hạn mức.
- **Giám sát (giám khảo)**: phiên Claude độc lập chấm theo `tasks/criteria.md`. Pro/Balanced/Code tự bật khi đổi chế độ.
  - Không phải code: model nhỏ (Pro: sonnet), ảnh lớn thu nhỏ trước, tối đa 5 phút.
  - File trượt do worker làm → **app tự giao lại đúng worker đó** kèm lỗi, chấm lại riêng file mới (tối đa 2 vòng, không qua Claude).
  - Code: test tự động đã qua hết → **bỏ qua giám khảo**.
- **Tiêu chí gửi worker ngay từ đầu** (`with_criteria`).
- **Hạn mức**: đo theo từng lượt + tổng task; còn ≤2% thì chặn/chờ; "chỉ dùng trong gói" luôn bật, không tắt được.
- **Kết quả**: `<thư mục kết quả>/<Dự án>/<YYYY-MM-DD>/<HH_MM> <tên task>/`, nháp `.work/` xoá khi task xong.
- **Learning**: task xong → bài học (lọc dữ liệu riêng: `scrub`) → Google Form; tác vụ hẹn giờ Chủ nhật gộp vào `src/learnings.md`.
- **Tự cập nhật**: tải từ GitHub API / raw / jsDelivr song song; bộ khởi động chạy bản mới có health-check, lỗi thì quay về bản cũ. Có task đang chạy → hỏi trước (OK: cập nhật ngay, task tự chạy tiếp; Hủy: tự cập nhật khi các task xong).
- **Cài/gỡ**: `Maestro_m_setup.bat` luôn tải bộ cài mới nhất (`Maestro_setup_latest.bat`) từ GitHub; `Maestro_m_uninstall.bat` [1] gỡ giữ data, [2] gỡ và xoá data (hỏi riêng về file kết quả).

---

## 3. Vấn đề đã gặp và cách giải quyết

### 3.1 Cài đặt (Windows/Mac)
| Vấn đề | Giải quyết |
|---|---|
| Máy không có winget | Bộ cài tự tải trực tiếp (v1) |
| Chữ có dấu trong cửa sổ cài bị lỗi | Thông báo trong bộ cài dùng ASCII không dấu |
| Ổ C còn dưới 2 GB | Chuyển Claude Code/npm/Antigravity/.local\bin sang ổ khác bằng junction, báo rõ lỗi hết dung lượng (v5.9, v6.0) |
| Codex lỗi vì không thấy Node.js | Tìm Node ở mọi nơi hay cài, thêm vào PATH, tự tìm lại và thử lại (v5.7, v7.5) |
| PATH phình to sau nhiều lần tự cập nhật → Codex mất node | Sửa cách ghép PATH (v7.6) |
| Phải chạy file cài 2 lần mới cài được ChatGPT | Tự tìm `npm.cmd` cạnh node, thử cài tối đa 2 lần trong cùng 1 lần chạy, thêm `%APPDATA%\npm` vào PATH (v9.9) |
| Mỗi bản mới phải gửi lại file cài | `Maestro_m_setup.bat` tự tải bộ cài mới nhất từ GitHub (v9.9) |
| Bản Mac | `Maestro_setup.command` đóng zip để giữ quyền chạy (v6.9) |

### 3.2 Đăng nhập AI
| Vấn đề | Giải quyết |
|---|---|
| Không copy được link đăng nhập | Mở trong Windows Terminal (v1.7), sau đó đăng nhập ngay trên trang web, dán mã trên web (v2.0, v3.6) |
| Gemini mở trang mã nhưng không có chỗ nhập | Dán mã xác nhận ngay trên trang web (v2.7, v2.8) |
| Antigravity ẩn lỗi khi đường dẫn có dấu cách | Sửa cách gọi (v2.11), ghi lý do khi lỗi (v2.10) |
| Đăng nhập ChatGPT lỗi do biến BROWSER | Bỏ BROWSER, mở cửa sổ nhỏ (v3.4, v5.6) |
| Người dùng không rành công nghệ | Đăng nhập Claude không cần dán mã (v7.8); tự nhận gói trả phí/miễn phí |
| Claude Pro bị coi là chưa rõ gói / không làm quản lý được (có máy `claude auth status --json` không trả `subscriptionType`) | Dự phòng: `planTier` (vd max_5x), rồi `oauthAccount.organizationType` trong ~/.claude.json (chỉ nhận khi email khớp); không đọc được thì ghi debug.log (v11.4) |
| Gemini không hiện email (gọi `agy status` - lệnh không tồn tại) | Đọc email từ dòng 'authenticated successfully as' trong log Antigravity mới nhất; làm mới trạng thái cả Gemini (v11.4) |

### 3.3 Tốc độ
Phân tích log cho thấy khoảng 91% thời gian là **chờ worker**, và giám khảo chấm trượt gây làm lại.
| Vấn đề | Giải quyết |
|---|---|
| Hướng dẫn CLAUDE.md của task quá dài (12.5k ký tự) | Chia mô-đun, chỉ nạp phần cần (~5.8k) (v8.2) |
| Claude viết lại kết quả worker | Worker trả đủ nội dung, Claude không viết lại (v7.1) |
| Gemini không ra ảnh → kẹt | Tự chuyển GPT trong cùng lệnh; chuyển 2 chiều, thử 1 lần, nhớ 30 phút (v9.3, v9.4) |
| Gemini CLI (Antigravity) hết lượt vẽ ảnh (HTTP 429 "exhausted your capacity", mở lại sau 3-5h; tách khỏi Gemini web/app) nhưng vẫn báo SUCCESS; có khi tự vẽ bằng code Python/PIL mà không nói | Đọc log CLI: lấy giờ mở lại, nhớ tới giờ đó (thay vì 30 phút), ảnh tạo sau lúc lỗi chuyển vào `_ve_bang_code`; vẽ dở thì GPT vẽ nốt phần thiếu (kèm danh sách ảnh đã có); Cài đặt hiện "Gemini CLI vẽ ảnh: hết lượt, mở lại lúc HH:MM" (v11.5) |
| Claude gần như không bao giờ hỏi lại (luật quá chặt; hỏi ở cuối câu trả lời nên app báo Xong) | Câu trả lời kết thúc bằng câu hỏi -> task "Chờ bạn trả lời" + thông báo; luật mới: hỏi NGAY trước khi làm khi đề bài mơ hồ/thiếu dữ kiện/có nghi vấn dễ làm sai hướng (tối đa 3 câu, kèm phương án mặc định); hỏi giữa chừng khi vướng (v11.5) |
| Câu hỏi tô vàng, phải gõ tay câu trả lời | Câu hỏi viết dạng "A - ...", "B - ..." (có "(đề xuất)"); giao diện hiện nút lựa chọn như ChatGPT + "gõ câu trả lời khác", không tô vàng (v11.6) |
| Task chạy lâu: tìm trong thư mục kết quả không thấy file đã đạt (file nằm trong nháp ẩn `.work/work/<ID>/vN`, chỉ chép sang khi xong) | Nút "Mở thư mục" từng việc trong Activity; luật: việc đạt chép ngay sang thư mục kết quả (v11.6) |
| File worker làm ra nằm trong nháp `.work`, Claude chép ra sau -> mất công, người dùng không xem được giữa chừng | Worker xong việc nào, app chuyển THẲNG file vào `<thư mục task>/<YYYYMMDD>-<tên việc>` (lần sửa `_v2`), sổ sách ở lại nháp và tự dọn; giám khảo vẫn tự giao sửa được (tìm việc qua export_dir) (v11.7) |
| Giám khảo chấm cả lô ở cuối -> file lỗi phải chờ cả lô xong mới sửa | Có criteria.md: app chấm từng việc vẽ ngay khi xong, trượt thì tự giao sửa (tối đa 2 vòng); trạng thái giữ "đang chấm" tới khi xong; cuối lô chỉ chấm nhanh tính đồng bộ (v11.7) |
| Ô Memory bị kéo lên khi cuộn; task gần đây xếp theo ngày tạo | Giữ vị trí cuộn khi bảng Cài đặt vẽ lại; xếp theo tin nhắn mới nhất (v11.7) |
| Cửa sổ console đen thỉnh thoảng hiện rồi tắt | Nguyên nhân: Gemini CLI tự cập nhật nền ~15-40 phút/lần bằng tiến trình `agy --bg-updater` mở console riêng (máy dùng Windows Terminal làm console mặc định nên hiện cửa sổ); khớp giờ 15:43:29, 16:01:15. Sửa: đặt `AGY_CLI_DISABLE_AUTO_UPDATE=true` (giá trị `1` không có tác dụng) cho mọi lệnh agy; Maestro tự chạy `agy update` ngầm 1 lần/ngày khi không có task (v11.8) |
| Chờ từng worker một | Giao cả lô `--bg`, chờ 1 lần bằng `wait` (v9.3) |
| Turbo vẫn tự xem/làm thêm (vd gen keyframe trước storyboard) | Bớt bước tự kiểm ở Turbo; Turbo không gộp ảnh thành lưới |
| Giám khảo chậm | Model nhỏ cho việc không phải code, thu nhỏ ảnh, timeout 5 phút; app tự giao lại file trượt (v9.9) |
| Code mode chậm hơn Claude Code (80s vs 47s) do giám khảo sau khi test đã qua | Test đã qua hết thì bỏ giám khảo → ~50s (v10.1) |
| Đo mức dùng tốn thời gian | Đo chạy nền sau mỗi lượt (v9.6) |
| Đã thử rồi bỏ: cân tải Claude/ChatGPT, tự mở phiên mới cho task dài | Bỏ theo quyết định người dùng (v9.5) |

### 3.4 Ổn định / dữ liệu
| Vấn đề | Giải quyết |
|---|---|
| Cập nhật giữa chừng làm mất worker | Worker chạy tách rời + lệnh `wait` (v6.4) |
| Bản mới lỗi làm hỏng app | Bộ khởi động + thư mục `versions/`, health-check, tự quay về bản cũ (v4.0) |
| Cập nhật dính bộ đệm GitHub | Tải theo commit mới nhất; song song GitHub API / raw / jsDelivr (v2.9, v5.4) |
| Bộ xếp lịch chết/kẹt | Tự chạy lại, ghi log, hiện lý do chờ (v5.8) |
| Hết hạn mức giữa task | Nhận biết + giờ reset, tự chạy tiếp (v6.5); chặn khi còn ≤2% |
| Codex bị chặn ghi file trên Windows | Sửa cờ chạy (v1.8) |
| Worker không sửa được file của người dùng | `--edit` sửa thẳng (v7.7) |
| Giám khảo chấm cả file nội bộ của worker (status.json…) → tưởng là code, không tự sửa | Bỏ qua file sổ sách khi chấm thư mục (v9.9) |
| Cập nhật khi đang có task chạy | Hỏi trước (v9.9) |
| Code mode không khởi động được trên Windows (`bad escape \U`: đường dẫn `C:\Users…` bị hiểu là ký tự đặc biệt) | Chèn đường dẫn bằng hàm thay vì chuỗi; đã rà, không còn chỗ nào cùng kiểu (v10.2) |

### 3.5 Giao diện
| Vấn đề | Giải quyết |
|---|---|
| Bôi đen chữ trong chat bị mất do vẽ lại | Hoãn vẽ lại khi đang chọn chữ (v1.12) |
| Nút "Task mới" luôn tối như đang chọn | Chỉ tô khi ở trang Task mới (v8.3) |
| Bấm Code mode bị nhận nhầm là nút đính kèm | Sửa bắt sự kiện; Code là 1 chế độ trong menu (v8.1) |
| Menu chế độ bị đầu trang che | Kiểm tra vị trí + z-index |
| Thu thanh bên làm vỡ lưới | `grid-template-columns:minmax(0,1fr)` |
| Mốc "x phút trước" đứng yên | Tự cập nhật mỗi 50 giây (v9.7, v9.8) |
| Mở task lần đầu, nút Giám sát/Project hiện theo trang chủ | Truyền đúng task khi vẽ đầu trang (v9.9) |
| Kết quả 2 lần vẫn chưa đúng | Gợi ý bật Giám sát, nút ngay cuối đoạn chat (v9.9) |
| Phong cách | Porcelain 2.0: font Geist, icon Lucide, chat 820px (v8.6) |
| Thư mục kết quả khó tìm (<dự án>/<ngày>/<giờ tên task>, trùng tên thì "(2)") | `<số thứ tự> - <tên đoạn chat>` (có dự án thì nằm trong thư mục dự án); mỗi lần xuất vào thư mục con `YYYYMMDD-<tên>`, sửa lại thì tạo thư mục mới. Lượt đầu chờ đặt tên tối đa 10 giây để thư mục đúng tên đoạn chat (v11.1) |
| Kết quả kém dần, chậm (so với ply36 Tug of War 23/11: GPT vẽ trực tiếp, hướng dẫn ngắn, phiên mới): Gemini trước cho ảnh, hướng dẫn 21,9k ký tự, nhiều bước (tiêu chí, chấm, A/B), phiên nối tiếp từ 23/11 | v11.9: bỏ luật Gemini trước, app tự học chọn worker theo kết quả + thời gian (worker_stats.json; Gemini vẽ kém -> giao thẳng GPT, 24h cho thử lại); theo dõi worker đang chạy, dừng sớm khi hết lượt / GPT im lặng quá 5-8 phút / chạy quá 4 lần thường thấy; worker ảnh không tự kiểm tra kỹ; nhãn bên đang làm thật; Giám sát tắt = chỉ giao việc (không tiêu chí/giám khảo/tự xem lại); câu hỏi A/B là tuỳ chọn; đề bài tự học (mẫu đề bài làm tốt); bộ nhớ gọn (chung <=700 + lịch sử theo dự án), bộ nhớ dự án dài chỉ nhúng 1.000 ký tự đầu; phiên Claude >=20 lượt hoặc nghỉ >1 ngày -> tin nhắn mới mở phiên mới kèm tóm tắt. Hướng dẫn Mini Soccer: 21,9k -> ~12-13k |
| Tin nhắn gửi lúc task chạy ngắt lượt đang làm (12/105 lượt bị ngắt); có question.md thì mất câu trả lời (16:59 26/11: chỉ hiện A/B); hướng dẫn đổi mỗi lượt (số liệu worker, bộ nhớ, hạn mức, ngày) -> Claude bị bắt đọc lại ~12k ký tự mỗi lượt; giám khảo chấm thêm cả lô ở cuối; bộ nhớ cập nhật mỗi lượt | v12.3: K1 tin nhắn lúc đang chạy -> xếp hàng, chạy ngay sau lượt (nút ⚡ Gửi gấp mới ngắt); K2 hiện cả câu trả lời + câu hỏi; K3 phần hay đổi (bộ nhớ, số liệu chọn worker, mẫu đề bài, hạn mức) gửi kèm tin nhắn khi đổi, CLAUDE.md ổn định; K5 file app đã chấm không chấm lại cả lô; K6 bộ nhớ cập nhật ở lượt đầu + sau mỗi 5 lượt |
| Giám khảo chấm trượt ~85% vì lỗi thẩm mỹ, mỗi ảnh vẽ 3 lần + chấm 3 lần (~1 phút/lần), bản sửa có khi kém hơn; mục "Còn tồn tại" xuất hiện mọi lúc (bộ nhớ ghi khuôn báo cáo của task phát triển); Balanced bật Giám sát mặc định; đề bài/ model vẽ ảnh khác v1.7 | v12.4: A chỉ nêu tồn tại khi có vấn đề thật; B bộ nhớ không ghi/lọc khuôn báo cáo; G1 giám khảo chỉ đánh trượt lỗi BẮT BUỘC (thẩm mỹ -> pass + notes); G2 tối đa 1 vòng vẽ lại, vẫn trượt và không ít lỗi hơn -> giữ bản trước (bản sửa vào _ban_cu/..._ban_sua_truot); G3 ảnh chấm 768px, chỉ công cụ Read, giới hạn số bước, chỉ mở ảnh mẫu khi tiêu chí cần so mẫu; H1 Giám sát mặc định chỉ Pro/Code; H2 Giám sát tắt: đề bài vẽ ảnh ngắn như v1.7, không gắn tiêu chí; H3 ChatGPT vẽ ảnh dùng model + mức suy nghĩ mặc định tài khoản |
| Muốn dùng Maestro trên điện thoại, mọi mạng, không cài app | Cloudflare Quick Tunnel (cloudflared tự tải, chạy tách khỏi app) + mã bí mật (cookie). QR cố định: trang GitHub Pages (nhánh `gh-pages`) đọc link hiện tại từ kênh ntfy.sh rồi chuyển sang; mã nằm sau `#` (v10.4) |
| Giao diện điện thoại tràn ngang, composer lơ lửng | Thanh bên thành ngăn kéo, composer sát đáy trang chủ (v10.4) |
| Dòng toạ độ đánh dấu hiện trong bong bóng chat; ảnh nằm trong bong bóng | Ẩn dòng toạ độ khi hiển thị (vẫn gửi cho AI); ảnh/file đưa ra ngoài bong bóng (v10.4) |
| Bấm thông báo không mở đúng task | Thông báo gắn id task, bấm là mở đoạn chat đó (v10.4) |
| Gemini API key kiểu mới (auth key, có `.` và `-`, dài hơn) bị báo "không giống API key" | Nhận 20-300 ký tự chữ/số/`-_.`; key đúng hay sai do bước thử gọi Google quyết định (v10.5) |
| Đọc ảnh đính kèm chậm | App tạo sẵn bản thu nhỏ (cạnh dài ≤1568px) cho ảnh lớn, dặn Claude đọc tất cả ảnh song song trong 1 lần (v10.5) |
| Code mode dùng Opus cả cho câu hỏi đơn giản | Tin nhắn là câu hỏi/tra cứu (không bảo làm, không kèm file) -> Sonnet; câu hỏi cần nhiều nguồn web -> giao Gemini research (v10.5) |
| Audit v10.5 (2 bản + Gemini phản biện): mọi việc đều đi qua quản lý; chờ đặt tên tới 30s; giao diện 30s/lần; % tiến độ đoán | v10.6: lễ tân phân loại bằng quy tắc (hỏi đáp / việc nhỏ / đầy đủ, không chắc -> đầy đủ); câu hỏi dùng Opus low; bỏ chờ đặt tên; giao diện 2,5 giây/lần; hiện "x/y việc xong" |
| Dọn nháp chạy nền xoá nhầm nháp lượt mới; tiêu chí/quyết định mất giữa các lượt | Kiểm tra lượt trước khi xoá; lượt mới ghi lại decisions.json + criteria.md từ lịch sử (v10.6) |
| Hủy task không dừng worker; task treo mãi; đọc stderr sau stdout có thể treo | Worker ghi pid, Hủy dừng cả cây tiến trình; giới hạn 60 phút không hoạt động / 4 giờ mỗi lượt; đọc stderr song song (v10.6) |
| Số worker song song chỉ nằm trong hướng dẫn | App giữ chỗ bằng file khoá data/slots (giới hạn cứng theo cấu hình máy) (v10.6) |
| Gemini tạo file không giới hạn quyền | Thêm --sandbox (v10.6) |
| Lệnh worker --bg lỗi in tiếng Việt (cp1252); đề bài ChatGPT-quản lý qua cmd.exe bị cắt | Lệnh con in UTF-8; đề bài ChatGPT ghi ra file .pm_prompt.md (v10.6) |
| Gỡ cài có thể xoá cả Desktop/Documents; gỡ cả Claude/Codex/Gemini người dùng cài sẵn | Không xoá thư mục chung của Windows; bộ cài ghi installed_by_maestro.txt, gỡ cài chỉ gỡ những gì Maestro cài (v10.6) |
| Learning tự động tốn 90s Haiku/task, có thể lọt dữ liệu riêng | Tắt (v10.6) |
| Save Template hiện ở mọi task | Chỉ gợi ý khi đã có từ 2 task cùng loại (so khớp từ khoá tên/yêu cầu), không với Code mode / task dùng template (v10.6) |
| Hướng dẫn cho Claude 8,2-9,1k ký tự | Rút gọn còn 6,7-8k; lệnh app chỉ ghi 1 lần (APP); luật số worker/thời gian/quyền do app ép (v10.6) |
| app.py 1 file 4.800 dòng khó đọc, khó cùng phát triển | v11.0: chia module core/providers/workers/features/web (theo tài liệu kiến trúc mới, bước 0–5); mã nguyên văn, chạy chung 1 không gian tên; build ghép lại 1 file. Đã chứng minh bản tách khớp 100% v10.6 (329/329 câu lệnh). Bộ test tự động src/tests, release.py chặn nếu test lỗi. Chưa làm bước 6 (gộp CSS) |
| Không biết vì sao Gemini không ra ảnh / GPT lỗi (nhật ký bị xoá cùng nháp) | v11.0: worker lưu lý do (lỗi gốc, "báo xong nhưng không tạo ra file ảnh", 12 dòng cuối nhật ký) vào lịch sử; xem ở Activity (Lý do) và Export log |
| Concept board: Claude vẽ từng phương án (12 ảnh) rồi ghép, 25 phút; research xem ảnh đối thủ tải ảnh gốc lần lượt (~6 phút) | v11.2: concept board/moodboard mặc định vẽ cả board trong 1 ảnh; research tối đa ~2 phút, xem ảnh: tải bản thu nhỏ (=w320) song song, tối đa 8 app x 5 ảnh, ghép 1 ảnh tổng hợp |

---

## Chờ duyệt — đã note (27/11): v12.3 làm K1 K2 K3 K5 K6; v12.4 làm A B G1 G2 G3 H1 H2 H3; CÒN CHỜ: K4
- A. Luật cuối lượt: chỉ nêu phần còn tồn tại khi có vấn đề thật ảnh hưởng kết quả / việc người dùng phải làm tiếp; không có thì bỏ hẳn mục đó, không viết "không có", không liệt kê việc chưa kiểm tra nhỏ nhặt.
- B. Bộ nhớ tự ghi: không ghi khuôn mẫu báo cáo ("Đã sửa", "Còn tồn tại", "đã thử/chưa thử"); lọc bỏ dòng đó nếu đang có.
- G1. Chỉ tự vẽ lại khi ảnh trượt tiêu chí BẮT BUỘC (sai nhân vật, sai nội dung, sai kích thước, có chữ khi yêu cầu không chữ...); lỗi thẩm mỹ -> đạt kèm ghi chú.
- G2. Tối đa 1 vòng tự vẽ lại (thay vì 2); bản vẽ lại vẫn trượt -> giữ bản tốt hơn, không vẽ tiếp.
- G3. Giám khảo nhanh hơn, mục tiêu 10-20 giây/lần: chấm bằng ảnh thu nhỏ, giới hạn số bước phiên chấm, chỉ đưa ảnh mẫu khi tiêu chí cần so mẫu.
- H1. Giám sát mặc định TẮT cho Balanced; chỉ bật cho Pro/Code hoặc khi người dùng bấm (tắt = Claude chỉ giao việc + xem 1 lần, như v1.7).
- H2. Đề bài gửi ChatGPT vẽ ảnh: kiểu ngắn như v1.7, không gắn tiêu chí khi Giám sát tắt.
- H3. ChatGPT vẽ ảnh dùng model + mức suy nghĩ mặc định của tài khoản (như v1.7), không ép medium.
- K4 (chưa làm): gom phát hành 1-2 lần/ngày; phát triển Maestro trên bản thử riêng; chỉ lên bản mới khi không task nào chạy.
Bằng chứng (log 27/11): lượt 09:28 thêm 2 screenshot mất 10m27s — mỗi ảnh 3 lần vẽ (~2 phút) + 3 lần chấm (~1 phút), đều trượt vì lý do thẩm mỹ, cuối cùng dùng lại bản 1/bản 2. Lượt 17:15 ngày 26/11: 6/8 việc chấm trượt, chạy tới lần 3, ChatGPT +39% phiên.

## 4. Quyết định đã chốt (giữ nguyên trừ khi người dùng đổi)

- Claude làm việc quan trọng/cần tư duy và viết lách; việc khối lượng giao worker.
- Không cân tải. Phiên Claude: đã qua 20 lượt hoặc nghỉ hơn 1 ngày thì tin nhắn mới mở phiên mới kèm tóm tắt (v11.9, thay quyết định cũ "không tự mở phiên mới").
- Gemini ở Balanced chạy medium, lên high khi task khó hoặc làm lại.
- Giám sát: Pro/Code tự bật (Balanced tắt mặc định từ v12.4); Code bỏ giám khảo khi test đã qua hết. Giám sát tắt = Claude chỉ giao việc (không tiêu chí, không giám khảo, không tự xem lại). Câu hỏi dạng A/B chỉ khi có lựa chọn rõ (v11.9).
- "Chỉ dùng trong gói" luôn bật, không có công tắc; không tự mua thêm credit.
- Chọn worker: app tự học theo kết quả + thời gian (bỏ luật "Gemini trước", v11.9); bên không vẽ được thì nhớ 30 phút / tới giờ mở lại. Số ảnh (1 ảnh hay nhiều ảnh, có gộp lưới hay không) do yêu cầu của người dùng quyết định; bỏ luật "Turbo không gộp ảnh thành lưới" (v11.3).
- Bảng Mức dùng: "% đã dùng" kiểu Claude, không đếm số lượt.
- Đo mức dùng mỗi lượt (chạy nền) + tổng task.
- Learning: không có công tắc, tự gửi bài học, không gửi dữ liệu riêng; quy tắc chung chỉ áp dụng khi hợp >90% loại task.
- Code mode: Opus; tự tạo thư mục dự án (không chọn thư mục); thiếu phần mềm → nút cài, bấm là tự chạy.
- Mức ưu tiên tiến trình: Bình thường, trừ máy ≤8 GB RAM.
- Không phát hành/push lên `main` khi Hoàng chưa duyệt.
- Bỏ chữ ký số cho bản cập nhật; giữ link điện thoại như hiện tại (không thắt chặt).
- Câu hỏi ngắn/đơn giản: Opus low. Learning tự động: tắt. Không giới hạn file tải lên, Export log không lọc.
- Kiến trúc mới (tách module, SQLite...) làm riêng SAU khi v10.6 ổn định.
- Benchmark: `src/bench.py` (logs: đo từ lịch sử, không tốn hạn mức; run: bộ việc mẫu, tốn hạn mức) chỉ chạy trên máy phát triển.
- Dùng trên điện thoại: icon điện thoại ở đầu Cài đặt, 1 dòng hướng dẫn; QR không đổi cho tới khi bấm Đổi mã; icon thông báo cũng ở đầu Cài đặt.
- Bộ nhớ người dùng (v11.0): Maestro tự ghi cách làm việc + lịch sử sau MỖI lượt xong (nền, haiku, không làm chậm task), dùng cho mọi task sau; lưu `_AI-Orchestrator/memory_auto.md` trên máy; xem/sửa/xoá trong Cài đặt > Memory. Chỉ chạy khi có Claude trả phí.
- Research mặc định tối đa ~2 phút (trừ khi yêu cầu nghiên cứu sâu); xem ảnh bằng bản thu nhỏ, không tải ảnh gốc (v11.2).

---

## 5. Đo thử Code Mode (2026-11-26)

Bài: sửa 3 lỗi giỏ hàng + mã FREESHIP + xuất CSV + tự viết test; chấm bằng 8 test ẩn. Máy thử chỉ có Claude (không có ChatGPT/Gemini).

| Lần chạy | Thời gian | Test ẩn |
|---|---|---|
| Claude Code chạy thẳng | 47s | 8/8 |
| Code Mode bản cũ (giám khảo sau khi test qua) | 80s | 8/8 |
| Code Mode v10.1, bật Giám sát | 51s | 8/8 |
| Code Mode v10.1, tắt Giám sát | 50s | 8/8 |

Mới chạy 1 lần, 1 bài nhỏ — chưa đủ để kết luận chung.

---

## 6. Chưa kiểm chứng / còn treo

**Đã kiểm chứng trên máy thật (Windows, 2026-11-26)**
- Code mode chạy được với đường dẫn có dấu tiếng Việt và dấu `\` (v10.2; v10.1 lỗi `bad escape \U`).
- Cài/cập nhật nhận đủ Node, Claude, ChatGPT, Gemini ngay lần chạy đầu.
- Đo mức dùng Claude và ChatGPT (Codex) hoạt động (số 5 giờ của Codex đôi khi bị đánh dấu cũ — `stale`).

**Chưa kiểm chứng trên máy thật**
- `Maestro_m_setup.bat`, `Maestro_m_uninstall.bat`: chưa chạy trên Windows thật.
- Hỏi trước khi cập nhật: mới kiểm tra qua code, chưa thử với bản cập nhật thật.
- Địa chỉ đo hạn mức Claude là địa chỉ nội bộ, có thể đổi.
- Font Geist hiển thị trên Windows; hành vi đăng nhập Claude trên Windows.
- Code mode khi có worker ChatGPT/Gemini thật.

**Chờ người dùng quyết**
- Link Google Form/Sheet cho Learning (`src/learn_form.json` đang trống).
- Thu thêm dữ liệu người dùng và nút 👍/👎.
- Model cho Turbo (Gemini Flash / Sonnet).
- Giới hạn thời gian nghiên cứu.
- Đóng gói 1 file .exe: cần chứng chỉ ký số để Windows không cảnh báo (chưa quyết).
