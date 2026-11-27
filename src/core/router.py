# Maestro — core/router.py: Lễ tân: phân loại tin nhắn (hỏi đáp / việc nhỏ / đầy đủ) — CHỈ để app chọn model, mức suy nghĩ;
# không gửi chữ phân loại cho Claude (v14: đoán theo từ khoá dễ sai, đá nhau với luật khác).
# Mọi module dùng chung 1 không gian tên (dev: app.py nạp lần lượt; build: ghép thành 1 file),
# nên KHÔNG import chéo giữa các module. Thứ tự nạp: PARTS trong assemble.py.

BIG_RE = re.compile(r"storyboard|concept|ý tưởng|hàng loạt|\bbatch\b|audit|research|nghiên cứu|so sánh|báo cáo|kế hoạch|"
                    r"chiến lược|toàn bộ|tất cả|nhiều (?:ảnh|file|bản|phương án|option)|\b\d{2,}\s*(?:ảnh|file|banner|bản|cái|slide)|"
                    r"slide|powerpoint|excel|bảng tính|website|landing|app\b", re.I)


def route(t, nxt):
    """Lễ tân: 'ask' (hỏi đáp: trả lời thẳng) / 'small' (việc nhỏ: làm 1 mạch) / 'full' (quy trình đầy đủ).
    Chỉ dựa trên quy tắc (không tốn thêm lần gọi AI); không chắc -> 'full'."""
    text = nxt.get("text") or (t.get("text", "") if nxt.get("kind") == "start" or not t.get("session_started") else "")
    if is_ask(t, nxt):
        return "full" if BIG_RE.search(text) else "ask"     # hỏi nhưng việc lớn (research, so sánh, báo cáo) -> đủ quy trình
    if nxt.get("kind") not in ("start", "followup", None) or len(nxt.get("files") or []) > 2:
        return "full"
    if 0 < len(text) <= 250 and not BIG_RE.search(text):
        return "small"
    return "full"


ASK_RE = re.compile(r"\?\s*$|có thể\b.{0,80}\bkhông\b|làm (?:thế|sao) nào|có cách nào|tại sao|vì sao|là gì|ở đâu|bao nhiêu|"
                    r"nên\b.{0,60}\bkhông\b|so sánh|tìm hiểu|tra cứu|research|giải thích|nghĩa là", re.I | re.M)
DO_RE = re.compile(r"\b(?:làm đi|làm luôn|fix|sửa|tạo|viết|triển khai|áp dụng|update|cập nhật|thêm|xoá|xóa|đổi|build|chạy|"
                   r"cài|deploy|push|commit|refactor|implement)\b", re.I)


def is_ask(t, nxt):
    """Tin nhắn là câu hỏi / tra cứu đơn giản (không bảo làm, không kèm file) -> Code mode dùng model nhẹ hơn."""
    if nxt.get("files") or nxt.get("kind") not in ("start", "followup", None):
        return False
    text = nxt.get("text") or (t.get("text", "") if nxt.get("kind") == "start" or not t.get("session_started") else "")
    return 0 < len(text) <= 600 and bool(ASK_RE.search(text)) and not DO_RE.search(text)


DEEP_RE = re.compile(r"tại sao|vì sao|nguyên nhân|giải thích|phân tích|đánh giá|nên\b.{0,40}\bhay\b|ưu nhược|rủi ro", re.I)


def ask_effort(t, nxt):
    """Mức suy nghĩ cho câu hỏi: hỏi nhanh -> low (trả lời nhanh, ngắn); hỏi cần lập luận (tại sao, nguyên nhân, so sánh...) ->
    giữ mức của chế độ. None = không phải câu hỏi (theo chế độ)."""
    if route(t, nxt) != "ask":
        return None
    text = nxt.get("text") or (t.get("text", "") if nxt.get("kind") == "start" or not t.get("session_started") else "")
    return None if DEEP_RE.search(text) else "low"
