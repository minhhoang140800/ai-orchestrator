"""Benchmark Maestro (chạy trên MÁY PHÁT TRIỂN, không chạy trên máy người dùng).

Hai cách dùng:
1) Đo lại từ lịch sử (không tốn hạn mức AI): đọc nhật ký giờ (timeline.log) + task.json của các task đã chạy.
     python src/bench.py logs "D:/<thư mục nhận kết quả>/_AI-Orchestrator/tasks"
2) Chạy bộ việc mẫu cố định trên 1 Maestro đang mở (TỐN hạn mức AI thật; chỉ chạy khi có thay đổi lớn):
     python src/bench.py run --port 8827 [--suite quick|full] [--mode balanced]
   Ghi kết quả vào bench_results/<thời điểm>.csv để so trước/sau khi sửa.

Chỉ số: thời gian tới khi AI bắt đầu làm (bước đầu tiên), tổng thời gian, số lượt, số việc giao worker, trạng thái cuối.
"""
import csv
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

SUITES = {
    "quick": [
        ("hoi-dap", "Giải thích ngắn gọn sự khác nhau giữa PNG và JPG khi làm banner quảng cáo?"),
        ("viec-nho", "Viết 3 tiêu đề quảng cáo ngắn cho game xếp hình, mỗi tiêu đề dưới 40 ký tự."),
        ("csv", "Tạo file CSV 20 dòng dữ liệu mẫu doanh thu theo ngày (ngày, doanh thu, số người chơi) và tính tổng ở dòng cuối."),
    ],
    "full": [
        ("hoi-dap", "Giải thích ngắn gọn sự khác nhau giữa PNG và JPG khi làm banner quảng cáo?"),
        ("viec-nho", "Viết 3 tiêu đề quảng cáo ngắn cho game xếp hình, mỗi tiêu đề dưới 40 ký tự."),
        ("csv", "Tạo file CSV 20 dòng dữ liệu mẫu doanh thu theo ngày (ngày, doanh thu, số người chơi) và tính tổng ở dòng cuối."),
        ("research", "Research và so sánh 3 công cụ nén ảnh PNG miễn phí phổ biến, kết luận nên dùng cái nào."),
        ("storyboard", "Viết storyboard dạng bảng chữ cho video quảng cáo 15 giây game xếp hình, 5 cảnh."),
        ("anh", "Tạo 1 ảnh icon 512x512 nền trong suốt hình viên kẹo màu đỏ."),
    ],
}


def api(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-AIO": "1", "Content-Type": "application/json", "Host": f"127.0.0.1:{port}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def measure(t):
    """Số đo của 1 task từ task.json (+ timeline.log nếu có)."""
    works = [m for m in t.get("messages", []) if m.get("role") == "work"]
    if not works:
        return None
    t0 = t.get("created_ts") or works[0].get("started_ts")
    first = works[0].get("started_ts")
    end = max((m.get("ended_ts") or 0) for m in works) or None
    subs = sum(len(m.get("subs") or m.get("subtasks") or []) for m in works)
    return {"id": t.get("id"), "title": t.get("title", "")[:60], "mode": t.get("mode", ""), "kind": t.get("kind") or "task",
            "status": t.get("status"), "turns": len(works), "subtasks": subs,
            "start_delay_s": round(first - t0, 1) if first and t0 else "",
            "work_s": round(sum((m.get("ended_ts") or m.get("started_ts", 0)) - m.get("started_ts", 0) for m in works), 1),
            "total_s": round(end - t0, 1) if end and t0 else ""}


def write_csv(rows, name):
    out = Path(__file__).resolve().parent.parent / "bench_results"
    out.mkdir(exist_ok=True)
    f = out / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    if rows:
        with open(f, "w", newline="", encoding="utf-8-sig") as fo:
            w = csv.DictWriter(fo, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return f


def summary(rows):
    for key in ("start_delay_s", "work_s", "total_s"):
        v = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if v:
            print(f"  {key:15} trung vị {statistics.median(v):8.1f}s   lớn nhất {max(v):8.1f}s   ({len(v)} task)")


def cmd_logs(folder):
    rows = []
    for f in sorted(Path(folder).glob("*/task.json")):
        try:
            m = measure(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            m = None
        if m:
            rows.append(m)
    print(f"{len(rows)} task có số đo")
    summary(rows)
    print("CSV:", write_csv(rows, "logs"))


def cmd_run(port, suite, mode):
    rows = []
    for name, text in SUITES[suite]:
        r = api(port, "/api/task/new", {"text": text, "mode": mode})
        if r.get("error"):
            print(name, "lỗi:", r["error"])
            continue
        tid = r.get("id") or (r.get("task") or {}).get("id")
        api(port, "/api/task/submit", {"id": tid, "files": []})
        t0 = time.time()
        print(f"[{name}] {tid} ...", end="", flush=True)
        while time.time() - t0 < 3600:
            time.sleep(5)
            t = api(port, f"/api/task?id={tid}")
            if t.get("status") in ("done", "failed", "cancelled", "waiting"):
                break
        m = measure(t) or {"id": tid, "status": t.get("status")}
        m["case"] = name
        rows.append(m)
        print(f" {m.get('status')} {m.get('total_s')}s")
    summary(rows)
    print("CSV:", write_csv(rows, f"run-{suite}-{mode}"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "logs" and len(a) > 1:
        cmd_logs(a[1])
    elif a and a[0] == "run":
        port = int(a[a.index("--port") + 1]) if "--port" in a else 80
        suite = a[a.index("--suite") + 1] if "--suite" in a else "quick"
        mode = a[a.index("--mode") + 1] if "--mode" in a else "balanced"
        cmd_run(port, suite, mode)
    else:
        print(__doc__)
