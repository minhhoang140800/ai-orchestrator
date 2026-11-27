"""Thử bộ luật cho Claude TRƯỚC KHI phát hành (chạy trên máy phát triển). So bản luật mới (src/ hiện tại) với bản cũ (thư mục src
của bản trước, vd lấy bằng: git archive <tag> src | tar -x -C /tmp/old).

1) static (không tốn hạn mức): dựng CLAUDE.md + tin nhắn lượt đầu cho từng tình huống (lấy từ task thật, đã viết lại cho gọn,
   bỏ link/đường dẫn riêng), đo độ dài, liệt kê câu luật mâu thuẫn còn sót.
     python src/eval_rules.py static --old /tmp/old/src
2) live (TỐN hạn mức Claude, ~1-2% khung 5 giờ cho bộ hỏi đáp): chạy `claude -p` thật cho các tình huống hỏi đáp, chỉ được trả
   lời (không chạy lệnh/sửa file), đo thời gian, độ dài, có kết thúc bằng câu hỏi không, có khối "## Chi tiết" không.
     python src/eval_rules.py live --old /tmp/old/src [--only hoi-png,hoi-vo-anh] [--inst file_huong_dan.txt]
Kết quả: bảng in ra màn hình + eval_results/<thời điểm>.md (không có trong repo). Chỉ thêm luật mới khi bảng cho thấy tốt hơn.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent

# Tình huống từ task thật (viết lại gọn). kind: ask = hỏi đáp (chạy được live), do = việc làm (chỉ chạy static).
CASES = [
    ("hoi-dien-thoai", "ask", "balanced", "làm thế nào để sử dụng được Maestro trên điện thoại"),
    ("hoi-png", "ask", "balanced", "PNG và JPG khác nhau thế nào khi làm banner quảng cáo game?"),
    ("hoi-vo-anh", "ask", "balanced", "tại sao ảnh asset game bị vỡ khi phóng to lên 2048px, có cách nào tránh không?"),
    ("hoi-harness", "ask", "balanced", "Agent harness là gì? Mình có thể học được gì cho app giao việc AI của mình không?"),
    ("hoi-goi-nhanh", "ask", "code", "đóng gói bản cài nhanh (self-contained) cho app .NET là sao, có nặng hơn không?"),
    ("asset-ply", "do", "balanced", "tôi cần asset cho ver 33 theo bảng trong file Google Sheet đính kèm, base nhân vật như ver trước"),
    ("resize-ss", "do", "balanced", "tôi cần resize các screenshot này qua các size mới (1080x1920, 1179x2556, 2048x2732), chia folder theo size"),
    ("ads-idea", "do", "pro", "Phân tích gameplay và creative quảng cáo của đối thủ, cho tôi 5 idea video và 5 idea playable ads kèm storyboard"),
    ("christmas", "do", "turbo", "rework asset png các enemy theo style Christmas, chỉ cần PNG, giữ nguyên size"),
    ("sheet-clear", "do", "balanced", "clear toàn bộ content trong file Google Sheet này, chỉ giữ lại bộ khung. sửa trực tiếp trên file"),
    ("code-upgrade", "do", "code", "NoteTasker\nđây là dự án tôi đang dùng, còn vài lỗi đồng bộ; nghiên cứu và nâng cấp bản mới"),
    ("code-publish", "do", "code", "fix nốt các lỗi còn lại và publish bản mới"),
    ("chuong-la", "do", "code", "check giúp tôi máy liên tục có tiếng chuông thông báo lạ mặc dù đã thoát app"),
]
CONFLICT = ["KHÔNG commit", "MẶC ĐỊNH TỰ QUYẾT", "Còn tồn tại", "Đường xử lý", "VIỆC NHỎ", "bỏ giám khảo", "Không dùng API key"]


def load(src, home, inst=""):
    import importlib.util
    spec = importlib.util.spec_from_file_location("assemble_" + str(abs(hash(str(src)))), src / "assemble.py")
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    os.environ["AIO_HOME"] = str(home)
    os.environ["AIO_NO_BROWSER"] = "1"
    ns = {"__name__": "eval", "__file__": str(src / "app.py")}
    for p in asm.PARTS:
        exec(compile((src / p).read_text(encoding="utf-8"), str(src / p), "exec"), ns)
    ns["CFG"] = ns["load_cfg"]()
    out = Path(home) / "out"
    out.mkdir(parents=True, exist_ok=True)
    ns["CFG"]["output"] = str(out)
    ns["CFG"]["instructions"] = inst
    ns["set_store"]()
    ns["workers_on"] = lambda t: ["codex", "gemini"]       # như máy có đủ GPT + Gemini, có gói trả phí
    ns["free_setup"] = lambda: False
    ns["LOW_SPEC"] = False
    ns["quota_md"] = lambda *a, **k: "ChatGPT: 5 giờ còn 60% · Gemini: không đọc được mức dùng"
    return ns


def render(ns, cid, mode, text):
    r = ns["new_task"](text, mode=mode, kind="code" if mode == "code" else "")
    t = ns["TASKS"][r["id"]]
    t["folder"] = "01 - " + cid
    if mode == "code":
        t["pdir"] = str(ns["out_dir"](t) / "code")
    ns["write_claude_md"](t)
    md = (ns["wdir"](t) / ns["md_name"](t)).read_text(encoding="utf-8")
    nxt = {"kind": "start"}
    prompt = ns["build_prompt"](t, nxt)
    eff = None
    if "ask_effort" in ns:
        eff = ns["ask_effort"](t, nxt)
    elif ns["route"](t, nxt) == "ask":
        eff = "low"
    return t, md, prompt, eff or ns["MODEL_PLAN"]["claude"]["code" if mode == "code" else ns["norm_mode"](mode)][1]


def words(s):
    return len(s.split())


def static(old, inst):
    rows = []
    for tag, src in (("cũ", old), ("mới", SRC)):
        if not src:
            continue
        ns = load(Path(src), tempfile.mkdtemp(prefix="eval-"), inst)
        for cid, kind, mode, text in CASES:
            t, md, prompt, eff = render(ns, cid, mode, text)
            rows.append((cid, tag, len(md.splitlines()), words(md), words(prompt), eff,
                         ", ".join(c for c in CONFLICT if c in md + prompt) or "-"))
    head = "| tình huống | bản | CLAUDE.md dòng | CLAUDE.md từ | tin nhắn lượt đầu (từ) | mức suy nghĩ | câu luật cũ còn sót |"
    return [head, "|" + "---|" * 7] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


def live(old, inst, only):
    rows = []
    cases = [c for c in CASES if c[1] == "ask" and (not only or c[0] in only)]
    for tag, src in (("cũ", old), ("mới", SRC)):
        if not src:
            continue
        ns = load(Path(src), tempfile.mkdtemp(prefix="eval-"), inst)
        for cid, kind, mode, text in cases:
            t, md, prompt, eff = render(ns, cid, mode, text)
            d = ns["wdir"](t)
            t0 = time.time()
            p = subprocess.run([ns["find_bin"]("claude"), "-p", prompt, "--model", "opus", "--output-format", "json",
                                "--disallowedTools", "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task", "Agent",
                                "WebSearch", "WebFetch"], cwd=str(d), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=600, env=dict(os.environ, CLAUDE_CODE_EFFORT_LEVEL=eff),
                               stdin=subprocess.DEVNULL)
            sec = round(time.time() - t0)
            try:
                j = json.loads(p.stdout)
                ans = j.get("result") or ""
            except Exception:
                ans = (p.stdout or p.stderr or "")[-2000:]
            body = ans.split("## Chi tiết")[0]
            last = [x for x in ans.strip().splitlines() if x.strip()][-1:] or [""]
            rows.append((cid, tag, eff, sec, len(ans), len(body.strip().splitlines()), "có" if "## Chi tiết" in ans else "-",
                         "có" if re.search(r"\?\s*\**\s*$", last[0]) else "-"))
            (d.parent / f"answer_{tag}.md").write_text(ans, encoding="utf-8")
            print(f"--- {cid} ({tag}, {eff}, {sec}s) ---\n{ans}\n", flush=True)
    head = "| tình huống | bản | mức suy nghĩ | giây | ký tự | dòng phần chính | khối Chi tiết | kết bằng câu hỏi |"
    return [head, "|" + "---|" * 8] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]


def main(argv):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] not in ("static", "live"):
        return print(__doc__)
    opt = lambda k: argv[argv.index(k) + 1] if k in argv else ""
    inst = Path(opt("--inst")).read_text(encoding="utf-8") if opt("--inst") else ""
    only = [x for x in opt("--only").split(",") if x]
    lines = static(opt("--old"), inst) if argv[0] == "static" else live(opt("--old"), inst, only)
    print("\n".join(lines))
    out = SRC.parent / "eval_results"
    out.mkdir(exist_ok=True)
    (out / time.strftime(f"%Y%m%d-%H%M-{argv[0]}.md")).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
