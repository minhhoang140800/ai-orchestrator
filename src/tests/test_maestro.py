"""Bộ test tự động của Maestro (chỉ Python standard library, không gọi AI thật, không tốn hạn mức).

    python -m unittest discover -s src/tests -v

Kiểm tra: ghép module đúng thứ tự (= 1 file như bản build), lễ tân, ghi JSON song song, hướng dẫn cho AI theo
mọi chế độ (kể cả đường dẫn Windows có dấu \\), bộ nhớ người dùng, lý do worker lỗi, và app khởi động trả API.
"""
import ast
import builtins
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import assemble  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="maestro-test-")
os.environ["AIO_HOME"] = _TMP
os.environ["AIO_NO_BROWSER"] = "1"


def load():
    """Nạp toàn bộ module vào 1 không gian tên (như app.py khi chạy), không chạy điểm vào."""
    ns = {"__name__": "maestro_test", "__file__": str(SRC / "app.py")}
    for p in assemble.PARTS:
        exec(compile((SRC / p).read_text(encoding="utf-8"), str(SRC / p), "exec"), ns)
    ns["CFG"] = ns["load_cfg"]()
    out = Path(_TMP) / "out"
    out.mkdir(exist_ok=True)
    ns["CFG"]["output"] = str(out)
    ns["set_store"]()
    return ns


NS = load()


def module_level_loads(tree):
    """(dòng, tên) được dùng NGAY lúc nạp nhưng chưa định nghĩa ở phía trên -> ghép sai thứ tự."""
    defined, bad = set(dir(builtins)) | {"__file__", "__name__"}, []

    class V(ast.NodeVisitor):
        def __init__(s):
            s.loads = []

        def visit_Name(s, n):
            if isinstance(n.ctx, ast.Load):
                s.loads.append(n.id)

        def visit_FunctionDef(s, n):
            for d in n.decorator_list + n.args.defaults + [x for x in n.args.kw_defaults if x]:
                s.visit(d)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(s, n):
            for d in n.args.defaults:
                s.visit(d)

        def _comp(s, n):
            v = V()
            for c in ast.iter_child_nodes(n):
                v.visit(c)
            tg = {x.id for g in n.generators for x in ast.walk(g.target) if isinstance(x, ast.Name)}
            s.loads += [x for x in v.loads if x not in tg]

        visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comp

    for n in tree.body:
        v = V()
        if isinstance(n, ast.ClassDef):
            for b in n.bases + n.decorator_list + n.body:
                v.visit(b)
        elif not isinstance(n, ast.If):
            v.visit(n)
        new = {n.name} if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else {
            x.id for x in ast.walk(n) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)} | {
            (a.asname or a.name).split(".")[0] for x in ast.walk(n) if isinstance(x, (ast.Import, ast.ImportFrom)) for a in x.names}
        bad += [(n.lineno, x) for x in v.loads if x not in defined and x not in new]
        defined |= new
    return bad


class Assembly(unittest.TestCase):
    def test_compiles_and_order(self):
        code = assemble.assemble()
        tree = ast.parse(code)
        self.assertEqual(module_level_loads(tree), [])

    def test_each_name_defined_once(self):
        seen = {}
        for p in assemble.PARTS:
            for n in ast.parse((SRC / p).read_text(encoding="utf-8")).body:
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                    self.assertNotIn(n.name, seen, f"{n.name} có ở cả {seen.get(n.name)} và {p}")
                    seen[n.name] = p

    def test_no_cross_imports(self):
        mods = {Path(p).stem for p in assemble.PARTS} | {"core", "providers", "workers", "features", "web"}
        for p in assemble.PARTS:
            for n in ast.walk(ast.parse((SRC / p).read_text(encoding="utf-8"))):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    names = [n.module or ""] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names]
                    self.assertFalse(any(x.split(".")[0] in mods for x in names), f"{p} import module nội bộ")

    def test_build_placeholders_once(self):
        code = assemble.assemble()
        for k in ('UI_B64 = ""', 'LEARN_MD = ""', 'LEARN_FORM = ""', 'FONTS_B64 = ""', 'ICO_B64 = ""',
                  'RELEASE = "dev"', 'VERSION = "dev"', 'UPDATE_BASE = ""'):
            self.assertEqual(code.count(k), 1, k)
        self.assertTrue(code.rstrip().endswith('serve(background="--background" in sys.argv)'))


class Router(unittest.TestCase):
    @staticmethod
    def r(q):
        return NS["route"]({"session_started": True}, {"kind": "followup", "text": q})

    def test_questions(self):
        for q in ("có thể làm cách 2 được không", "làm thế nào để dùng trên điện thoại", "tại sao task chậm?"):
            self.assertEqual(self.r(q), "ask", q)

    def test_requests(self):
        for q in ("làm đi", "sửa lỗi đăng nhập và phát hành bản mới", "tạo 5 ảnh banner 1080x1080"):
            self.assertNotEqual(self.r(q), "ask", q)

    def test_big_question_full(self):
        self.assertEqual(self.r("research và so sánh 3 công cụ nén ảnh?"), "full")


class Store(unittest.TestCase):
    def test_wjson_parallel(self):
        f = Path(_TMP) / "par.json"
        errs = []

        def w(i):
            try:
                for k in range(30):
                    NS["wjson"](f, {"i": i, "k": k})
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=w, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertIn("i", json.loads(f.read_text(encoding="utf-8")))


class Prompts(unittest.TestCase):
    def _md(self, mode, text="Viết 3 tiêu đề quảng cáo", **kw):
        r = NS["new_task"](text, mode=mode, **kw)
        t = NS["TASKS"][r["id"]]
        t.update(kw.get("extra", {}))
        NS["write_claude_md"](t)
        return t, (NS["wdir"](t) / NS["md_name"](t)).read_text(encoding="utf-8")

    def test_all_modes(self):
        for m in ("pro", "balanced", "turbo", "eco", "code"):
            t, md = self._md(m)
            self.assertIn("## Hướng dẫn của người dùng", md, m)
            self.assertIn("## Hỏi / nghiên cứu / đề xuất ≠ làm", md, m)
            self.assertLess(len(md), 14000, m)

    def test_research_and_board_rules(self):
        _, md = self._md("balanced")
        self.assertIn("Research: tối đa ~2 phút", md)
        self.assertIn("=w320", md)
        _, md = self._md("balanced", text="Cho tôi idea, mỗi loại vào 1 Concept Board")
        self.assertIn("VẼ CẢ BOARD TRONG 1 ẢNH", md)

    def test_windows_paths_in_code_mode(self):          # lỗi cũ: bad escape \\U khi đường dẫn Windows lọt vào re.sub
        r = NS["new_task"]("sửa code", mode="code")
        t = NS["TASKS"][r["id"]]
        t["pdir"] = r"C:\Users\Minh Hoang\Documents\proj"
        txt = NS["code_md"]("## Vai trò\nx\n## Chế độ: Balanced\ny\n## Khác\nz\n", t,
                            r"C:\Users\Minh Hoang\AppData\Local\Programs\Python\python.exe", r"C:\Users\Minh Hoang\app.py")
        self.assertIn(r"C:\Users\Minh Hoang\Documents\proj", txt)

    def test_memory_sent_in_prompt_not_in_md(self):
        """K3: bộ nhớ đi kèm tin nhắn (lần đầu + khi đổi), KHÔNG nằm trong CLAUDE.md -> hướng dẫn không đổi mỗi lượt."""
        NS["mem_save"]("## Cách làm việc\n- Xưng hô tôi – bạn", None, "## Lịch sử\n- Dự án Alien")
        try:
            t, md = self._md("balanced")
            self.assertNotIn("Maestro tự ghi nhớ", md)
            p = NS["build_prompt"](t, {"kind": "start"})
            self.assertIn("Maestro tự ghi nhớ", p)
            self.assertIn("Xưng hô tôi – bạn", p)
            self.assertIn("Dự án Alien", p)              # lịch sử: chỉ của đúng dự án của task (task không dự án -> "_chung")
            t["session_started"] = True
            p2 = NS["build_prompt"](t, {"kind": "followup", "text": "tiếp"})
            self.assertNotIn("Maestro tự ghi nhớ", p2)  # không đổi -> không gửi lại
            NS["mem_save"]("## Cách làm việc\n- Trả lời rất ngắn", None, None)
            p3 = NS["build_prompt"](t, {"kind": "followup", "text": "tiếp"})
            self.assertIn("Trả lời rất ngắn", p3)       # đổi -> gửi bản mới
            h = t.get("md_hash")
            t.pop("md_changed", None)
            NS["write_claude_md"](t)
            self.assertEqual(t.get("md_hash"), h)        # bộ nhớ đổi nhưng CLAUDE.md giữ nguyên -> không bắt đọc lại
            self.assertFalse(t.get("md_changed"))
        finally:
            NS["mem_save"]("", None, "")


class Memory(unittest.TestCase):
    def test_turn_excerpt(self):
        t = {"messages": [{"role": "user", "text": "câu 1"}, {"role": "claude", "text": "trả lời 1"},
                          {"role": "user", "text": "ngắn hơn"}, {"role": "work"}, {"role": "claude", "text": "trả lời 2"}]}
        x = NS["mem_turn"](t)
        self.assertIn("ngắn hơn", x)
        self.assertIn("trả lời 2", x)
        self.assertNotIn("câu 1", x)

    def test_no_claude_no_call(self):
        old = NS["find_bin"]
        NS["find_bin"] = lambda *a, **k: None
        try:
            NS["mem_update"]({"messages": [{"role": "user", "text": "a"}, {"role": "claude", "text": "b"}]})
        finally:
            NS["find_bin"] = old


class WorkerStats(unittest.TestCase):
    def setUp(self):
        (Path(NS["DATA"]) / NS["WSTATS_F"]).unlink(missing_ok=True)

    def test_default_without_data(self):
        self.assertEqual(NS["best_worker"]("image"), "codex")
        self.assertEqual(NS["best_worker"]("research"), "gemini")

    def test_learns_from_results(self):
        for _ in range(5):
            NS["record_stat"]("gemini", "image", False, 200)
            NS["record_stat"]("codex", "image", True, 240)
            NS["record_stat"]("gemini", "text", True, 60)
            NS["record_stat"]("codex", "text", True, 180)
        self.assertEqual(NS["best_worker"]("image"), "codex")          # GPT vẽ được nhiều hơn rõ
        self.assertEqual(NS["best_worker"]("text"), "gemini")          # ngang nhau -> bên nhanh hơn
        self.assertTrue(NS["stat_poor"]("gemini", "image"))            # -> app giao thẳng GPT
        self.assertFalse(NS["stat_poor"]("codex", "image"))
        d = json.loads((Path(NS["DATA"]) / NS["WSTATS_F"]).read_text(encoding="utf-8"))
        for x in d["gemini:image"]:
            x["t"] -= 25 * 3600                                        # đã hơn 24 giờ -> cho Gemini thử lại
        (Path(NS["DATA"]) / NS["WSTATS_F"]).write_text(json.dumps(d), encoding="utf-8")
        self.assertFalse(NS["stat_poor"]("gemini", "image"))
        self.assertEqual(NS["stat_cap"]("codex", "image", 900), 900)   # 4 x 240s = 960 > giới hạn gốc 900
        self.assertEqual(NS["stat_cap"]("gemini", "text", 900), 360)   # tối thiểu 6 phút
        md = NS["stats_md"](["codex", "gemini"])
        self.assertIn("Ảnh: giao GPT", md)
        self.assertIn("Viết / idea / phân tích: giao Gemini", md)


class Watchdog(unittest.TestCase):
    def test_stops_on_limit_text(self):
        code = "import time,sys;print('working',flush=True);time.sleep(1);print('Error: usage limit reached',file=sys.stderr,flush=True);time.sleep(60)"
        t0 = time.time()
        r = NS["run_watched"]([sys.executable, "-c", code], _TMP, 120, "gemini", "text", 120, "viết bài")
        self.assertEqual(r[4], "limit")
        self.assertLess(time.time() - t0, 20)

    def test_stops_when_slow(self):
        r = NS["run_watched"]([sys.executable, "-c", "import time;time.sleep(60)"], _TMP, 120, "gemini", "text", 4, "x")
        self.assertEqual(r[4], "slow")

    def test_normal_run(self):
        r = NS["run_watched"]([sys.executable, "-c", "print('ok')"], _TMP, 60, "codex", "text", 60, "x")
        self.assertEqual((r[0], r[4], r[1].strip()), (0, "", "ok"))

    def test_limit_words_in_task_not_misread(self):
        code = "import time,sys;print('rate limit exceeded la gi',flush=True)"
        r = NS["run_watched"]([sys.executable, "-c", code], _TMP, 60, "codex", "text", 60, "giải thích rate limit exceeded")
        self.assertEqual(r[4], "")


class V119(unittest.TestCase):
    def test_session_refresh(self):
        old = time.time() - 2 * 86400
        t = {"id": "SR1", "text": "làm asset ply36", "mode": "balanced", "session_started": True, "session": "abc",
             "messages": [{"role": "user", "text": "làm asset ply36"}, {"role": "work", "started_ts": old, "ended_ts": old},
                          {"role": "claude", "text": "Đã xong 23 asset"}],
             "decisions": [{"group": "ply36", "items": [{"type": "decision", "text": "chỉ 1 folder", "status": "done"}]}]}
        self.assertTrue(NS["session_stale"](t))                       # nghỉ hơn 1 ngày
        h = NS["handover_text"](t)
        self.assertIn("làm asset ply36", h)
        self.assertIn("chỉ 1 folder", h)
        self.assertIn("Đã xong 23 asset", h)
        t.update(handover=h, session_started=False)
        p = NS["build_prompt"](t, {"kind": "followup", "text": "làm tiếp ply37"})
        self.assertIn("Phiên mới cho task đang làm dở", p)
        self.assertIn("làm tiếp ply37", p)
        fresh = {"messages": [{"role": "work", "started_ts": time.time(), "ended_ts": time.time()}]}
        self.assertFalse(NS["session_stale"](fresh))
        self.assertTrue(NS["session_stale"]({"messages": [{"role": "work", "ended_ts": time.time()}] * 20}))

    def test_watch_off_only_delegates(self):
        t = {"id": "WO1", "text": "vẽ 3 ảnh banner", "mode": "balanced", "judge": False, "folder": "01 - x"}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Giám sát TẮT — chỉ giao việc", md)
        self.assertNotIn("viết tasks/criteria.md", md)
        self.assertNotIn("stages/", md)
        t2 = {"id": "WO2", "text": "vẽ 3 ảnh banner", "mode": "balanced", "judge": True, "folder": "01 - y"}
        NS["write_claude_md"](t2)
        self.assertIn("viết tasks/criteria.md", (NS["wdir"](t2) / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_good_briefs(self):
        (Path(NS["DATA"]) / NS["BRIEFS_F"]).unlink(missing_ok=True)
        NS["note_brief"]("codex", "image", 120, "Banner Mini Soccer\nVẽ 1 ảnh 1080x1080, nền trong suốt")
        md = NS["briefs_md"](["image"])
        self.assertIn("Mẫu đề bài từng làm tốt", md)
        self.assertIn("Banner Mini Soccer", md)
        self.assertEqual(NS["briefs_md"](["text"]), "")

    def test_memory_drops_ab_rule(self):
        NS["mem_save"]("## Cách làm việc\n- Xưng hô tôi – bạn\n- Hỏi lựa chọn: tối đa 3 câu, trình bày A/B/C.")
        try:
            m = NS["mem_text"]()
            self.assertIn("Xưng hô", m)
            self.assertNotIn("A/B/C", m)
        finally:
            NS["mem_save"]("")


class V123(unittest.TestCase):
    def _t(self, tid):
        t = {"id": tid, "text": "vẽ 3 icon", "mode": "balanced", "status": "running", "messages": [], "folder": "01 - " + tid}
        NS["TASKS"][tid] = t
        return t

    def test_message_while_running_is_queued(self):
        """K1: tin nhắn lúc đang chạy -> xếp hàng, KHÔNG ngắt; nút Gửi gấp mới ngắt."""
        t = self._t("K1a")
        killed = []
        old = NS["kill_tree"]
        NS["kill_tree"] = lambda p: killed.append(p)
        try:
            NS["act"]("K1a", "send", "thêm 1 icon nữa")
            NS["act"]("K1a", "send", "nền xanh")
            self.assertEqual(killed, [])
            self.assertEqual(t["_queued"]["text"], "thêm 1 icon nữa\n\nnền xanh")
            self.assertFalse(t.get("_redirect"))
            NS["act"]("K1a", "send", "dừng, đổi sang 5 icon", urgent=True)
            self.assertEqual(len(killed), 1)
            self.assertEqual(t["_redirect"]["kind"], "urgent")
            self.assertIn("nền xanh", t["_redirect"]["text"])          # tin đã xếp hàng được gộp vào tin gấp
            self.assertNotIn("_queued", t)
        finally:
            NS["kill_tree"] = old
            NS["TASKS"].pop("K1a", None)

    def test_memory_every_5_turns(self):
        """K6: bộ nhớ cập nhật ở lượt đầu, rồi sau mỗi 5 lượt."""
        calls = []
        old = NS["mem_update"]
        NS["mem_update"] = lambda t: calls.append(1)
        try:
            t = {"id": "K6", "messages": [{"role": "user", "text": "a"}, {"role": "work"}, {"role": "claude", "text": "b"}]}
            NS["mem_maybe"](t)
            self.assertEqual(len(calls), 1)
            for _ in range(4):
                t["messages"] += [{"role": "user", "text": "c"}, {"role": "work"}, {"role": "claude", "text": "d"}]
                NS["mem_maybe"](t)
            self.assertEqual(len(calls), 1)                            # mới 4 lượt
            t["messages"] += [{"role": "user", "text": "e"}, {"role": "work"}, {"role": "claude", "text": "f"}]
            NS["mem_maybe"](t)
            self.assertEqual(len(calls), 2)                            # đủ 5 lượt
        finally:
            NS["mem_update"] = old
        x = NS["mem_turn"]({"messages": [{"role": "user", "text": "u1"}, {"role": "claude", "text": "a1"},
                                         {"role": "user", "text": "u2"}, {"role": "claude", "text": "a2"}]}, 0)
        self.assertIn("u1", x)
        self.assertIn("a2", x)

    def test_judge_no_final_batch(self):
        """K5: file app đã chấm thì Claude không chấm lại cả lô ở cuối."""
        t = {"id": "K5", "text": "vẽ 3 icon", "mode": "balanced", "judge": True, "folder": "01 - K5"}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("KHÔNG chấm cả lô ở cuối", md)
        self.assertNotIn("cuối lô chỉ chấm nhanh", md)


class FailReason(unittest.TestCase):
    def test_no_image(self):
        v = Path(tempfile.mkdtemp())
        (v / "worker.log").write_text("--- stdout ---\nĐã vẽ xong\n--- stderr ---\nERROR: write blocked by sandbox\n", encoding="utf-8")
        r = NS["fail_reason"]({"status": "completed"}, v, True)
        self.assertIn("không tạo ra file ảnh", r)
        self.assertIn("write blocked by sandbox", r)

    def test_fallback_error_kept(self):
        v = Path(tempfile.mkdtemp())
        (v / "gpt").mkdir()
        (v / "gpt" / "worker.log").write_text("--- stdout ---\nok\n", encoding="utf-8")
        r = NS["fail_reason"]({"status": "completed", "codex_error": "Not logged in"}, v, False)
        self.assertIn("GPT: Not logged in", r)

    def test_ok_no_reason(self):
        v = Path(tempfile.mkdtemp())
        (v / "a.png").write_bytes(b"x")
        self.assertEqual(NS["fail_reason"]({"status": "completed"}, v, True), "")


class Folders(unittest.TestCase):
    """Thư mục kết quả: '<số thứ tự> - <tên đoạn chat>'; mỗi lần xuất: thư mục con YYYYMMDD-<tên>."""
    def test_numbering(self):
        out = Path(NS["CFG"]["output"]) / "fold-test"
        out.mkdir(exist_ok=True)
        NS["CFG"]["output"] = str(out)
        try:
            nf = NS["next_folder"]
            self.assertEqual(nf(Path(), "Gợi ý cải tiến game"), "01 - Gợi ý cải tiến game")
            (out / "01 - Gợi ý cải tiến game").mkdir()
            (out / "07 - Việc cũ").mkdir()
            (out / "_AI-Orchestrator").mkdir()
            self.assertEqual(nf(Path(), "Banner Tết"), "08 - Banner Tết")
            self.assertEqual(nf(Path("Idle Clicker"), "Icon"), str(Path("Idle Clicker") / "01 - Icon"))
            self.assertEqual(nf(Path(), 'Tên: có / ký tự "lạ"'), "08 - " + NS["safe_name"]('Tên: có / ký tự "lạ"', 70))
        finally:
            NS["CFG"]["output"] = str(out.parent)

    def test_export_subfolder_rule(self):
        t = {"id": "F1", "text": "x", "mode": "balanced", "folder": "01 - Thử", "watch": False}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("OUTPUT/<YYYYMMDD>-<nhóm>", md)
        self.assertIn("<YYYYMMDD>-icons", md)                      # không chèn ngày thật (hướng dẫn khỏi đổi mỗi ngày)


class Accounts(unittest.TestCase):
    """Gói Claude khi CLI không báo subscriptionType; email Gemini đọc từ log Antigravity."""
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="home-"))
        self.old = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
        os.environ["USERPROFILE"] = os.environ["HOME"] = str(self.home)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_claude_plan(self):
        cp = NS["claude_plan"]
        base = {"loggedIn": True, "authMethod": "claude.ai", "storedClaudeAiLogin": True, "email": "a@b.com"}
        self.assertEqual(cp({**base, "subscriptionType": "max"}), ("max", True))
        self.assertEqual(cp({**base, "planTier": "max_5x"}), ("max", True))           # thiếu subscriptionType -> planTier
        (self.home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "A@b.com",
                                                                             "organizationType": "claude_pro"}}), encoding="utf-8")
        self.assertEqual(cp(base), ("pro", True))                                        # -> ~/.claude.json
        self.assertEqual(NS["claude_cfg_plan"]("khac@x.com"), "")                        # email không khớp: không nhận
        (self.home / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationType": "claude_free"}}), encoding="utf-8")
        self.assertEqual(cp(base), ("free", False))
        self.assertEqual(cp({"loggedIn": False}), ("", None))

    def test_gemini_email_from_log(self):
        d = self.home / ".gemini" / "antigravity-cli" / "log"
        d.mkdir(parents=True)
        (d / "cli-20261126_100000.log").write_text("x\nINFO authenticated successfully as cu@gmail.com\n", encoding="utf-8")
        time.sleep(0.05)
        (d / "cli-20261126_110000.log").write_text("INFO authenticated successfully as moi@gmail.com\nok\n", encoding="utf-8")
        self.assertEqual(NS["agy_log_email"](), "moi@gmail.com")
        self.assertEqual(NS["ai_email"]("gemini"), "moi@gmail.com")


class ImageQuota(unittest.TestCase):
    """Gemini CLI hết lượt vẽ: nhớ đúng giờ mở lại; vẽ dở thì bên kia vẽ nốt phần thiếu."""
    MSG = ("Encountered error in tool execution: image generation quota exceeded, try again later: transport error "
           "(ResourceExhausted, HTTP 429): You have exhausted your capacity on this model. Your quota will reset after 3h20m14s.")

    def setUp(self):
        (Path(NS["DATA"]) / NS["IMG_FAIL_F"]).unlink(missing_ok=True)

    def test_parse_reset(self):
        u = NS["img_quota_until"](self.MSG)
        self.assertAlmostEqual(u - time.time(), 3 * 3600 + 20 * 60 + 14, delta=5)
        self.assertEqual(NS["img_quota_until"]("Done, 4 images saved."), 0)

    def test_block_until_reset(self):
        until = int(time.time() + 3 * 3600)
        NS["mark_img_fail"]("gemini", until)
        self.assertTrue(NS["img_failed_recent"]("gemini"))
        self.assertEqual(NS["img_block_until"]("gemini"), until)
        NS["mark_img_fail"]("gemini", int(time.time() - 5))           # đã qua giờ mở lại -> vẽ lại được
        self.assertFalse(NS["img_failed_recent"]("gemini"))
        NS["mark_img_fail"]("codex")                                   # không rõ giờ: nhớ 30 phút như cũ
        self.assertTrue(NS["img_failed_recent"]("codex"))
        self.assertEqual(NS["img_block_until"]("codex"), 0)

    def test_partial_then_other_draws_rest(self):
        v = Path(tempfile.mkdtemp()) / "v1"
        v.mkdir()
        (v / "TASK.md").write_text("Vẽ 4 icon game, mỗi icon 1 file png", encoding="utf-8")
        for i in (1, 2):
            (v / f"icon_{i}.png").write_bytes(b"x")
        st = {"status": "completed", "img_quota_until": NS["img_quota_until"](self.MSG)}
        seen = {}

        def fake_run(cmd, **kw):
            seen["task"] = Path(cmd[cmd.index("--task") + 1]).read_text(encoding="utf-8")
            gv = Path(cmd[cmd.index("--vdir") + 1])
            for i in (3, 4):
                (gv / f"icon_{i}.png").write_bytes(b"x")
            NS["wjson"](gv / "status.json", {"status": "completed", "model": "gpt-x", "seconds": 5})
        old = (NS["subprocess"].run, NS["find_bin"], NS["usage_block"])
        NS["subprocess"].run, NS["find_bin"], NS["usage_block"] = fake_run, (lambda *a: "x"), (lambda *a, **k: 0)
        try:
            a = type("A", (), {"worker": "gemini", "timeout": 60})()
            NS["worker_fallback"](st, v, a, None, True)
        finally:
            NS["subprocess"].run, NS["find_bin"], NS["usage_block"] = old
        self.assertIn("PHẦN CÒN THIẾU", seen["task"])
        self.assertIn("icon_1.png", seen["task"])
        self.assertEqual(st.get("via"), "gpt-fallback")
        self.assertIn("vẽ được 2 ảnh rồi hết lượt", st.get("gemini_error", ""))
        self.assertEqual(len(NS["_imgs"](v)), 4)
        self.assertTrue(NS["img_failed_recent"]("gemini"))


class CodeDrawn(unittest.TestCase):
    """Gemini CLI hết lượt vẽ rồi tự vẽ bằng code (vẫn báo xong): đọc log CLI -> chuyển ảnh đó sang _ve_bang_code."""
    def test_log_and_set_aside(self):
        home = Path(tempfile.mkdtemp(prefix="home-"))
        old = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
        os.environ["USERPROFILE"] = os.environ["HOME"] = str(home)
        try:
            t0 = time.time() - 60
            err_at = datetime_now = time.localtime(t0 + 20)
            line = time.strftime("E%m%d %H:%M:%S", err_at) + (".007222    1859 generate_image.go:171] error generating image: "
                                                              "transport error (ResourceExhausted, HTTP 429): You have exhausted "
                                                              "your capacity on this model. Your quota will reset after 2h5m0s.")
            d = home / ".gemini" / "antigravity-cli" / "log"
            d.mkdir(parents=True)
            (d / "cli-x.log").write_text("I1126 info\n" + line + "\n", encoding="utf-8")
            at, until = NS["agy_img_error"](t0)
            self.assertAlmostEqual(at, time.mktime(err_at), delta=1)
            self.assertAlmostEqual(until - time.time(), 2 * 3600 + 5 * 60, delta=5)
            v = Path(tempfile.mkdtemp()) / "v1"
            v.mkdir()
            ok_img, code_img = v / "ai.png", v / "code.png"
            ok_img.write_bytes(b"x")
            os.utime(ok_img, (at - 10, at - 10))                   # vẽ trước lúc hết lượt -> ảnh AI thật
            code_img.write_bytes(b"x")                              # tạo sau -> vẽ bằng code
            self.assertEqual(NS["set_aside_code_drawn"](v, at), ["code.png"])
            self.assertEqual([f.name for f in NS["_imgs"](v)], ["ai.png"])
            self.assertTrue((v / NS["CODE_DRAWN"] / "code.png").exists())
        finally:
            for k, val in old.items():
                if val is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = val


class Export(unittest.TestCase):
    """File worker làm ra chuyển thẳng vào <OUTPUT>/<YYYYMMDD>-<tên việc>; sổ sách ở lại nháp .work."""
    def test_move_products(self):
        out_root = Path(tempfile.mkdtemp()) / "01 - Thử"
        proj = out_root / ".work"
        v = proj / "work" / "icon_final" / "v1"
        (v / "gpt").mkdir(parents=True)
        (v / "_ve_bang_code").mkdir()
        for n in ("TASK.md", "worker.log", "result.md", "status.json"):
            (v / n).write_text("x", encoding="utf-8")
        (v / "icon_1.png").write_bytes(b"x")
        (v / "gpt" / "icon_2.png").write_bytes(b"x")
        (v / "_ve_bang_code" / "bad.png").write_bytes(b"x")
        st = {"status": "completed"}
        NS["export_products"](st, v, v.parent, proj)
        dst = out_root / (time.strftime("%Y%m%d") + "-icons")              # gom theo nhóm (đoán từ tên việc)
        self.assertEqual(st["export_dir"], str(dst))
        self.assertEqual(st["files"], ["icon_1.png", "icon_2.png"])          # ảnh bên làm thay đưa ra cùng cấp
        self.assertTrue((dst / "icon_2.png").exists() and not (v / "icon_1.png").exists())
        self.assertTrue((v / "result.md").exists() and not (dst / "result.md").exists())   # sổ sách ở lại nháp
        self.assertFalse((dst / "bad.png").exists())                         # ảnh vẽ bằng code không ra thư mục kết quả
        NS["wjson"](v / "status.json", st)
        v2 = v.parent / "v2"
        v2.mkdir()
        (v2 / "icon_1.png").write_bytes(b"y")
        st2 = {"status": "completed"}
        NS["export_products"](st2, v2, v.parent, proj)
        self.assertEqual(st2["export_dir"], str(dst))                        # lần sửa: cùng thư mục nhóm, giữ tên file
        self.assertEqual(st2["files"], ["icon_1.png"])
        self.assertEqual((dst / "icon_1.png").read_bytes(), b"y")
        self.assertEqual((dst / "_ban_cu" / "icon_1_v1.png").read_bytes(), b"x")   # bản trước không mất
        o = proj / "work" / "icon_menu" / "v1"                               # việc khác cùng nhóm, trùng tên file
        o.mkdir(parents=True)
        (o / "icon_1.png").write_bytes(b"z")
        st4 = {"status": "completed"}
        NS["export_products"](st4, o, o.parent, proj)
        self.assertEqual(st4["files"], ["icon_menu_icon_1.png"])             # giữ cả hai, không ghi đè
        self.assertEqual((dst / "icon_1.png").read_bytes(), b"y")
        g = proj / "work" / "S7" / "v1"                                      # Claude đặt nhóm bằng --group
        g.mkdir(parents=True)
        (g / "a.png").write_bytes(b"x")
        st5 = {"status": "completed"}
        NS["export_products"](st5, g, g.parent, proj, "screenshots")
        self.assertTrue(st5["export_dir"].endswith("-screenshots"))
        self.assertEqual(NS["export_group"](Path("SS1_blur_v3")), "screenshots")
        self.assertEqual(NS["export_group"](Path("concept_boards_v2")), "concept_boards")
        self.assertEqual(len([d for d in out_root.iterdir() if d.name != ".work"]), 2)   # chỉ 2 thư mục: icons + screenshots
        st3 = {"status": "failed"}
        v3 = v.parent / "v3"
        v3.mkdir()
        (v3 / "x.png").write_bytes(b"z")
        NS["export_products"](st3, v3, v.parent, proj)
        self.assertNotIn("export_dir", st3)                                  # lỗi: không chuyển

    def test_not_in_task_dir(self):
        v = Path(tempfile.mkdtemp()) / "work" / "S1" / "v1"
        v.mkdir(parents=True)
        (v / "a.png").write_bytes(b"x")
        st = {"status": "completed"}
        NS["export_products"](st, v, v.parent, v.parent.parent.parent)   # không phải thư mục .work của task
        self.assertNotIn("export_dir", st)


class AutoJudge(unittest.TestCase):
    """Giám sát bật (có criteria.md): việc vẽ xong là app chấm ngay việc đó (không đợi cả lô), không lặp vô hạn."""
    def _setup(self):
        proj = Path(tempfile.mkdtemp()) / "01 - Thử" / ".work"
        v = proj / "work" / "icon" / "v1"
        v.mkdir(parents=True)
        (v / "TASK.md").write_text("Vẽ 4 icon\n\nchi tiết", encoding="utf-8")
        exp = proj.parent / "20261126-icon"
        exp.mkdir()
        return proj, v, exp

    def test_judge_right_after_worker(self):
        proj, v, exp = self._setup()
        (proj / "tasks").mkdir()
        (proj / "tasks" / "criteria.md").write_text("- BẮT BUỘC: 4 icon", encoding="utf-8")
        seen = {}

        class P:
            stdout = json.dumps({"status": "rejected", "out_dir": str(proj / "work" / "icon_cham" / "v1"), "review": {
                "overall": "fail", "summary": "3/4 file đạt (app đã tự giao sửa 1 vòng)",
                "images": [{"file": str(exp / "icon_2.png"), "verdict": "fail", "issues": ["mờ"]}],
                "auto_fix": [{"round": 1, "files": {"a": [str(exp) + "_v2/icon_2.png"]}}]}})

        def fake_run(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
            return P()
        old = NS["subprocess"].run
        NS["subprocess"].run = fake_run
        try:
            st = {"status": "completed", "export_dir": str(exp)}
            NS["auto_judge"](st, v, v.parent, proj, True)
        finally:
            NS["subprocess"].run = old
        self.assertIn("judge", seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--files") + 1], str(exp))   # chấm đúng file của việc này
        self.assertEqual(seen["env"].get("AIO_NOJUDGE"), "1")                    # việc sửa do giám khảo giao không tự chấm lặp
        self.assertEqual(st["judge"]["overall"], "fail")
        self.assertEqual(st["judge"]["issues"], {"icon_2.png": ["mờ"]})
        self.assertTrue(st["judge"]["fixed"][0].endswith("_v2/icon_2.png"))

    def test_skip_without_criteria_or_not_image(self):
        proj, v, exp = self._setup()
        called = []
        old = NS["subprocess"].run
        NS["subprocess"].run = lambda *a, **k: called.append(1)
        try:
            st = {"status": "completed", "export_dir": str(exp)}
            NS["auto_judge"](st, v, v.parent, proj, True)              # chưa có criteria.md (Giám sát tắt)
            (proj / "tasks").mkdir()
            (proj / "tasks" / "criteria.md").write_text("x", encoding="utf-8")
            NS["auto_judge"](st, v, v.parent, proj, False)             # không phải việc vẽ
        finally:
            NS["subprocess"].run = old
        self.assertEqual(called, [])
        self.assertNotIn("judge", st)


class NoConsole(unittest.TestCase):
    """Gemini CLI tự cập nhật nền bằng tiến trình mở console riêng (Windows Terminal hiện cửa sổ đen) -> app tắt cơ chế đó."""
    def test_agy_autoupdate_off(self):
        self.assertEqual(os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE"), "true")
        self.assertTrue(callable(NS["agy_update_loop"]))


class AskUser(unittest.TestCase):
    """Câu trả lời kết thúc bằng câu hỏi -> task 'Chờ bạn trả lời'; luật hỏi trước khi bắt tay khi đề bài mơ hồ."""
    def test_trailing_question(self):
        au = NS["asks_user"]
        self.assertTrue(au("Đã xong phần 1.\n\nBạn chọn **A hay B**?"))
        self.assertTrue(au("Có 2 hướng:\n- A\n- B\n\nBạn muốn tôi làm B không?  "))
        self.assertFalse(au("Đã phát hành v11.5. Bạn bấm Cập nhật để dùng."))
        self.assertFalse(au("Câu hỏi của bạn: vì sao? Trả lời: do hết lượt.\n\nĐã sửa xong."))

    def test_open_sub_dir(self):
        t = {"id": "SD1", "text": "x", "status": "running", "folder": "02 - Thử", "messages": []}
        NS["TASKS"]["SD1"] = t
        opened = []
        old = NS["open_path"]
        NS["open_path"] = lambda p, *a: opened.append(Path(p))
        try:
            w = NS["wdir"](t)
            for v in ("v1", "v2"):
                (w / "work" / "SS8" / v).mkdir(parents=True, exist_ok=True)
            NS["wjson"](w / "work" / "SS8" / "v2" / "status.json", {"status": "running", "args": ["--group", "screenshots"]})
            self.assertEqual(NS["open_sub_dir"]("SD1", "SS8"), {"ok": True})
            self.assertEqual(opened[-1], w.parent)                            # đang chạy, chưa có nhóm: thư mục kết quả, không mở work/
            grp = w.parent / "20261126-screenshots"
            grp.mkdir()
            NS["open_sub_dir"]("SD1", "SS8")
            self.assertEqual(opened[-1], grp)                                 # đang chạy: mở thư mục nhóm sẽ nhận file
            exp = w.parent / "20261127-khac"
            exp.mkdir()
            NS["wjson"](w / "work" / "SS8" / "v1" / "status.json", {"status": "completed", "export_dir": str(exp)})
            NS["wjson"](w / "work" / "SS8" / "v2" / "status.json", {"status": "running"})
            NS["open_sub_dir"]("SD1", "SS8")
            self.assertEqual(opened[-1], exp)                                 # lần trước đã xuất: mở đúng nơi đó
            self.assertIn("error", NS["open_sub_dir"]("SD1", "../../x"))     # chặn đường dẫn lạ
            self.assertIn("error", NS["open_sub_dir"]("SD1", "S9"))
        finally:
            NS["open_path"] = old
            NS["TASKS"].pop("SD1", None)

    def test_prompt_rule(self):
        t = {"id": "Q1", "text": "x", "mode": "balanced", "folder": "01 - Hỏi", "watch": False}
        old_on, old_low = NS["workers_on"], NS["LOW_SPEC"]
        NS["workers_on"] = lambda t: ["codex", "gemini"]          # như máy có đủ GPT + Gemini, cấu hình không thấp
        NS["LOW_SPEC"] = False
        try:
            NS["write_claude_md"](t)
        finally:
            NS["workers_on"] = old_on
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("TRƯỚC khi bắt tay", md)
        self.assertIn("ĐANG làm mà vướng", md)
        self.assertIn('"A - <ngắn gọn>"', md)                     # câu hỏi dạng lựa chọn (nút bấm)
        self.assertIn("chuyển THẲNG vào thư mục nhóm", md)
        self.assertIn("--group <nhóm>", md)
        self.assertIn("Số worker song song (BẠN tự quyết, trần", md)       # Claude tự chọn số worker theo hạn mức/giờ reset
        self.assertIn("Hạn mức lúc bắt đầu lượt: app gửi kèm", md)
        old = NS["workers_on"]
        NS["workers_on"] = lambda t: ["codex", "gemini"]
        try:
            p = NS["build_prompt"](t, {"kind": "start"})
            self.assertIn("## Hạn mức lúc bắt đầu lượt", p)       # hạn mức đi kèm tin nhắn mỗi lượt
            self.assertIn("ChatGPT", p)
            self.assertIn("## Chia việc — số liệu máy này", p)
        finally:
            NS["workers_on"] = old
            NS["LOW_SPEC"] = old_low
        self.assertNotIn("Song song tối đa 3 worker", md)
        t2 = {"id": "Q2", "text": "x", "mode": "eco", "folder": "01 - Hỏi", "watch": False}
        NS["write_claude_md"](t2)
        self.assertIn("1 worker một lúc", (NS["wdir"](t2) / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(NS["MAX_PER_TASK"], NS["MAX_WORKERS"])
        self.assertNotIn("rồi chép bản đạt sang OUTPUT", md)


class App(unittest.TestCase):
    def test_starts_and_serves(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        home = tempfile.mkdtemp()
        p = subprocess.Popen([sys.executable, str(SRC / "app.py")], env=dict(os.environ, AIO_HOME=home, AIO_PORT=str(port),
                             AIO_NO_BROWSER="1"), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            ok = None
            for _ in range(60):
                time.sleep(0.25)
                try:
                    rq = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-AIO": "1", "Host": f"127.0.0.1:{port}"})
                    ok = json.loads(urllib.request.urlopen(rq, timeout=3).read())
                    break
                except Exception:
                    pass
            self.assertIsNotNone(ok, (p.stderr.read1() if p.poll() is not None else b"").decode(errors="replace"))
            self.assertIn("tasks", ok)
            self.assertIn("mem_auto", ok)
            ui = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read()
            self.assertIn(b"<html", ui[:500].lower())
        finally:
            p.kill()
            p.wait()
            p.stderr.close()


if __name__ == "__main__":
    unittest.main()
