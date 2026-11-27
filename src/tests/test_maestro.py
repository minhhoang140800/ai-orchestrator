"""Bộ test tự động của Maestro (chỉ Python standard library, không gọi AI thật, không tốn hạn mức).

    python -m unittest discover -s src/tests -v

Kiểm tra: ghép module đúng thứ tự (= 1 file như bản build), lễ tân, ghi JSON song song, hướng dẫn cho AI theo
mọi chế độ (kể cả đường dẫn Windows có dấu \\), bộ nhớ người dùng, giám khảo, và app khởi động trả API.
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
            for gone in ("## Hỏi / nghiên cứu", "## Tốc độ", "## Chia việc", "## Kết thúc mỗi lượt", "## Ghi lại", "## Ảnh & storyboard",
                         "## Hướng dẫn thêm"):
                self.assertNotIn(gone, md, m)          # v12.5: chỉ giữ các luật người dùng chọn
            self.assertLess(len(md), 14000, m)

    def test_kept_sections_only(self):
        _, md = self._md("balanced", text="Cho tôi idea, mỗi loại vào 1 Concept Board")
        for keep in ("## Hướng dẫn của người dùng", "## Vai trò", "## Thư mục", "## Worker", "## Nghiệm thu", "## Chế độ:",
                     "## Hỏi người dùng giữa chừng", "## Đọc thêm khi cần"):
            self.assertIn(keep, md)
        for gone in ("## Khi cần người dùng quyết định", "## Cấm", "## Đường dẫn"):    # v14: không còn luật cách làm việc trong mã chung
            self.assertNotIn(gone, md)

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

    def test_memory_keeps_user_ab_rule(self):                     # v14: sở thích riêng của người dùng nằm ở ghi nhớ, app không lọc bỏ
        NS["mem_save"]("## Cách làm việc\n- Xưng hô tôi – bạn\n- Hỏi lựa chọn: tối đa 3 câu, trình bày A/B/C.")
        try:
            m = NS["mem_text"]()
            self.assertIn("Xưng hô", m)
            self.assertIn("A/B/C", m)
        finally:
            NS["mem_save"]("")


class V123(unittest.TestCase):
    def _t(self, tid):
        t = {"id": tid, "text": "vẽ 3 icon", "mode": "balanced", "status": "running", "messages": [], "folder": "01 - " + tid}
        NS["TASKS"][tid] = t
        return t

    def test_message_while_running_is_queued(self):
        """v14: bỏ nút Gửi gấp. Tin nhắn lúc đang chạy (mọi chế độ, cả Code) -> Claude nhận ngay để làm cùng: ngắt lượt Claude 1 lần
        (worker vẫn chạy), tin tới tiếp trước khi lượt mới chạy thì gộp, không ngắt lại."""
        t = self._t("K1a")
        killed = []
        old = NS["kill_tree"]
        NS["kill_tree"] = lambda p: killed.append(p)
        try:
            NS["act"]("K1a", "send", "thêm 1 icon nữa")
            self.assertEqual(len(killed), 1)
            self.assertEqual(t["_redirect"]["kind"], "urgent")
            NS["act"]("K1a", "send", "nền xanh")
            self.assertEqual(len(killed), 1)                           # đã ngắt rồi: chỉ gộp
            self.assertEqual(t["_redirect"]["text"], "thêm 1 icon nữa\n\nnền xanh")
            self.assertNotIn("_queued", t)
            p = NS["_build_prompt"]({**t, "session_started": True}, t["_redirect"])
            self.assertIn("làm cùng với việc đang dở", p)
            self.assertIn("worker đã giao vẫn chạy tiếp", p)
            ui = (SRC / "web" / "ui.html").read_text(encoding="utf-8")
            self.assertNotIn("bUrgent", ui)                            # không còn nút Gửi gấp
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


class V124(unittest.TestCase):
    def test_watch_default_off_for_balanced(self):                 # H1
        self.assertFalse(NS["judge_on"]({"mode": "balanced"}))
        self.assertTrue(NS["judge_on"]({"mode": "pro"}))
        self.assertTrue(NS["judge_on"]({"mode": "code"}))
        self.assertTrue(NS["judge_on"]({"mode": "balanced", "judge": True}))   # người dùng bấm bật

    def test_no_criteria_when_watch_off(self):                     # H2
        d = Path(tempfile.mkdtemp())
        (d / "tasks").mkdir()
        (d / "tasks" / "criteria.md").write_text("- BẮT BUỘC: 1080x1080", encoding="utf-8")
        old = os.environ.get("AIO_WATCH")
        try:
            os.environ["AIO_WATCH"] = "0"
            self.assertNotIn("TIÊU CHÍ NGHIỆM THU", NS["with_criteria"]("vẽ banner", d))
            os.environ["AIO_WATCH"] = "1"
            self.assertIn("TIÊU CHÍ NGHIỆM THU", NS["with_criteria"]("vẽ banner", d))
        finally:
            os.environ.pop("AIO_WATCH", None) if old is None else os.environ.__setitem__("AIO_WATCH", old)

    def test_judge_prompt_lenient_and_fast(self):                  # G1, G3
        jp = NS["JUDGE_PROMPT"]
        self.assertIn('"fail" CHỈ KHI vi phạm tiêu chí BẮT BUỘC', jp)
        self.assertIn("vẫn \"pass\", ghi vào notes", jp)
        self.assertNotIn("phân vân", jp)
        self.assertEqual(NS["JUDGE_PX"], 768)
        self.assertFalse(NS["REF_NEED_RE"].search("- BẮT BUỘC: 1080x1920, không chữ"))
        self.assertTrue(NS["REF_NEED_RE"].search("- nhân vật giống mẫu đã duyệt"))

    def test_keep_previous_when_fix_not_better(self):              # G2
        d = Path(tempfile.mkdtemp())
        (d / "_ban_cu").mkdir()
        (d / "_ban_cu" / "shot_07_v1.png").write_bytes(b"BAN_TRUOC")
        (d / "shot_07.png").write_bytes(b"BAN_SUA")
        self.assertTrue(NS["keep_previous"](d / "shot_07.png", d / "shot_07.png"))
        self.assertEqual((d / "shot_07.png").read_bytes(), b"BAN_TRUOC")
        self.assertEqual((d / "_ban_cu" / "shot_07_ban_sua_truot.png").read_bytes(), b"BAN_SUA")
        self.assertFalse(NS["keep_previous"](d / "x.png", d / "y.png"))

    def test_end_of_turn_and_memory_rules(self):                   # A, B
        t = {"id": "AB1", "text": "x", "mode": "balanced", "folder": "01 - AB"}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("## Kết thúc mỗi lượt", md)
        NS["mem_save"]("## Cách làm việc\n- Gọi Hoàng\n- Trước khi báo xong: tách \"Đã sửa\" và \"Còn tồn tại\", ghi rõ đã thử và chưa thử.")
        try:
            m = NS["mem_text"]()
            self.assertIn("Gọi Hoàng", m)
            self.assertIn("Còn tồn tại", m)                          # v14: khuôn báo cáo người dùng muốn -> giữ (ghi nhớ của từng người)
        finally:
            NS["mem_save"]("")


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
    """Gemini CLI hết lượt vẽ: nhớ đúng giờ mở lại (hiện ở Cài đặt / mục hạn mức)."""
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
        NS["mark_img_quota"]("gemini", until)
        self.assertEqual(NS["img_block_until"]("gemini"), until)
        NS["mark_img_quota"]("gemini", int(time.time() - 5))          # đã qua giờ mở lại -> vẽ lại được
        self.assertEqual(NS["img_block_until"]("gemini"), 0)
        self.assertEqual(NS["img_block_until"]("codex"), 0)


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
        (v / "_ve_bang_code").mkdir(parents=True)
        for n in ("TASK.md", "worker.log", "result.md", "status.json"):
            (v / n).write_text("x", encoding="utf-8")
        (v / "icon_1.png").write_bytes(b"x")
        (v / "icon_2.png").write_bytes(b"x")
        (v / "_ve_bang_code" / "bad.png").write_bytes(b"x")
        st = {"status": "completed"}
        NS["export_products"](st, v, v.parent, proj)
        dst = out_root / (time.strftime("%Y%m%d") + "-icons")              # gom theo nhóm (đoán từ tên việc)
        self.assertEqual(st["export_dir"], str(dst))
        self.assertEqual(st["files"], ["icon_1.png", "icon_2.png"])
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


class V125(unittest.TestCase):
    """12.5: giám khảo Opus 5.5 low + ảnh mẫu; app không tự chọn/theo dõi/chuyển worker; đề bài worker 1 mẫu chung;
    chỉ dùng trong gói cố định trong code."""
    def test_removed_features(self):
        for n in ("worker_fallback", "fail_reason", "run_watched", "WATCH_IDLE", "IMG_QUICK", "stat_poor", "record_stat",
                  "best_worker", "note_brief", "briefs_md", "task_kind", "img_failed_recent", "in_plan_only"):
            self.assertNotIn(n, NS, n)

    def test_plan_only_hardcoded(self):
        self.assertIn("ANTHROPIC_API_KEY", NS["PLAN_ONLY_ENV"])
        self.assertIn("OPENAI_API_KEY", NS["PLAN_ONLY_ENV"])
        for k in NS["PLAN_ONLY_ENV"]:
            self.assertNotIn(k, os.environ)                 # claude/codex/agy luôn dùng tài khoản gói, không tính phí API
        f = Path(NS["DATA"]) / "usage.json"
        old = f.read_text(encoding="utf-8") if f.exists() else None
        try:
            f.write_text(json.dumps({"codex": {"known": True, "five_hour": {"left": 1, "resets_at": int(time.time() + 600)}}}),
                         encoding="utf-8")
            self.assertGreater(NS["usage_block"]("codex"), time.time())   # gần hết gói -> luôn chặn, không có công tắc
        finally:
            f.unlink() if old is None else f.write_text(old, encoding="utf-8")

    def test_judge_opus_low_with_refs(self):
        d = Path(tempfile.mkdtemp())
        img, ref = d / "out" / "ss4.png", d / "input" / "ss4.png"       # mẫu trùng tên file đang chấm
        img.parent.mkdir()
        ref.parent.mkdir()
        img.write_bytes(b"x")
        ref.write_bytes(b"y")
        crit = d / "criteria.md"
        crit.write_text("- BẮT BUỘC: Rabbit Run giống nhân vật trong game, tỉ lệ ~6.5 đầu", encoding="utf-8")
        v = d / "J" / "v1"
        v.mkdir(parents=True)
        seen = {}

        def fake_run(cmd, cwd, timeout, env=None):
            seen["cmd"], seen["env"] = cmd, env or {}
            return 0, json.dumps({"result": json.dumps({"overall": "pass", "images": [{"file": "ss4.png", "verdict": "pass"}]})}), "", False
        old = (NS["run_limited"], NS["find_bin"])
        NS["run_limited"], NS["find_bin"] = fake_run, (lambda *a: "claude")
        try:
            st, verdict = NS["judge_once"]([img], [ref], crit, "vẽ lại ss4", NS["JUDGE_MODEL"], 60, v)
        finally:
            NS["run_limited"], NS["find_bin"] = old
        self.assertEqual(NS["JUDGE_MODEL"], "claude-opus-5-5")
        self.assertEqual(seen["cmd"][seen["cmd"].index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(seen["env"].get("CLAUDE_CODE_EFFORT_LEVEL"), "low")
        prompt = seen["cmd"][seen["cmd"].index("-p") + 1]
        self.assertIn("MẪU ĐÃ DUYỆT", prompt)
        self.assertIn(str(ref), prompt)
        self.assertEqual(st["status"], "completed")

    def test_judge_refs_pick(self):
        root = Path(tempfile.mkdtemp()) / "01 - Rabbit"
        proj = root / ".work"
        v = proj / "work" / "ss4" / "v1"
        v.mkdir(parents=True)
        inp = root / "input"
        inp.mkdir()
        for i, n in enumerate(("old1.png", "old2.png", "sheet.png", "ss4_danhdau.png", "old3.png")):
            (inp / n).write_bytes(b"x")
            os.utime(inp / n, (time.time() - 100 + i, time.time() - 100 + i))
        exp = root / "20261127-screenshots"
        exp.mkdir()
        (exp / "ss1.png").write_bytes(b"x")                  # bản đã chốt, đề bài có nhắc tên
        (exp / "ss4.png").write_bytes(b"x")                  # file đang chấm
        (v / "TASK.md").write_text("SS4\nNhân vật như input/sheet.png, giữ phong cách ss1.png", encoding="utf-8")
        old = os.environ.get("AIO_INPUT")
        os.environ["AIO_INPUT"] = str(inp)
        try:
            refs = NS["judge_refs"](proj, v, [str(exp / "ss4.png")])
        finally:
            os.environ.pop("AIO_INPUT") if old is None else os.environ.__setitem__("AIO_INPUT", old)
        self.assertEqual([f.name for f in refs], ["sheet.png", "ss1.png", "old1.png"])

    def test_worker_single_brief_no_fallback(self):
        proj = Path(tempfile.mkdtemp()) / "01 - W" / ".work"
        (proj / "tasks").mkdir(parents=True)
        (proj / "tasks" / "S1.md").write_text("Banner\nVẽ 1 ảnh banner 1080x1080", encoding="utf-8")
        seen = []

        def fake_run(cmd, cwd, timeout, env=None):
            seen.append(cmd)
            return 1, "", "ERROR: something broke", False
        keys = ("run_limited", "find_bin", "usage_block", "worker_slot")
        old = {k: NS[k] for k in keys}
        NS.update(run_limited=fake_run, find_bin=lambda *a: "codex", usage_block=lambda *a, **k: 0, worker_slot=lambda: None)
        env_old = {k: os.environ.get(k) for k in ("AIO_WCHILD", "AIO_WATCH")}
        os.environ.update(AIO_WCHILD="1", AIO_WATCH="0")
        cwd = os.getcwd()
        os.chdir(proj)
        try:
            with self.assertRaises(SystemExit):
                NS["cmd_worker"](["codex", "--task", "tasks/S1.md", "--out", "work/S1"])
        finally:
            os.chdir(cwd)
            NS.update(old)
            for k, x in env_old.items():
                os.environ.pop(k, None) if x is None else os.environ.__setitem__(k, x)
        prompt = seen[0][-1]
        self.assertIn("-m", seen[0])                         # model theo chế độ, kể cả việc vẽ ảnh
        self.assertNotIn("KHÔNG tự đo", prompt)
        self.assertNotIn("cách đã kiểm tra", prompt)
        self.assertTrue(all(c[0] == "codex" for c in seen))  # lỗi -> không tự giao Gemini
        st = json.loads((proj / "work" / "S1" / "v1" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["status"], "failed")
        self.assertNotIn("reason", st)
        self.assertNotIn("via", st)


class V129(unittest.TestCase):
    """12.9: không còn luật ngược "mặc định tự quyết" / "báo kết quả ngắn"; bộ nhớ không học thói quen đưa phương án để chọn."""
    def test_no_conflicting_rules(self):
        for mode in ("turbo", "balanced", "pro"):
            t = {"id": "C9" + mode, "text": "x", "mode": mode, "folder": "09 - " + mode}
            NS["write_claude_md"](t)
            md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("chưa kiểm tra kỹ", md)
            self.assertNotIn("không đảm bảo chất lượng thì hỏi", md)
            self.assertIn("app không tự chuyển worker", md)
            self.assertEqual("xhigh vào tasks/effort.txt" in md, mode == "pro")

    def test_memory_prompt_user_only(self):                       # v14: bộ nhớ đọc Cài đặt (không chép trùng), chỉ học điều người dùng nói
        p = NS["MEM_PROMPT"]
        self.assertIn("{inst}", p)
        self.assertIn("KHÔNG ghi thói quen của AI", p)
        self.assertNotIn("mặc định AI tự", p)
        self.assertNotIn("ASK_FMT_RE", NS)


class NoConsole(unittest.TestCase):
    """Gemini CLI tự cập nhật nền bằng tiến trình mở console riêng (Windows Terminal hiện cửa sổ đen) -> app tắt cơ chế đó."""
    def test_agy_autoupdate_off(self):
        self.assertEqual(os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE"), "true")
        self.assertTrue(callable(NS["agy_update_loop"]))


class ResumeTimer(unittest.TestCase):
    """Worker hết lượt: Claude tự quyết chuyển hay chờ; chờ thì hẹn giờ qua tasks/resume_at.txt (app giữ hẹn, không treo)."""
    def test_resume_at_file(self):
        t = {"id": "RT1", "text": "x", "status": "done", "folder": "03 - Hẹn", "messages": []}
        d = NS["wdir"](t) / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(NS["resume_at"](t))                                  # không hẹn
        at = NS["datetime"].datetime.now() + NS["datetime"].timedelta(minutes=20)
        (d / "resume_at.txt").write_text(at.strftime("%H:%M") + chr(10) + "Vẽ lại icon 3, icon 5, logo C bằng ChatGPT", encoding="utf-8")
        r = NS["resume_at"](t)
        self.assertTrue(abs(r[0] - at.replace(second=0, microsecond=0).timestamp()) < 2)
        self.assertEqual(r[1], "Vẽ lại icon 3, icon 5, logo C bằng ChatGPT")
        self.assertFalse((d / "resume_at.txt").exists())                       # đọc xong xoá (không hẹn lặp)
        past = NS["datetime"].datetime.now() - NS["datetime"].timedelta(minutes=5)
        (d / "resume_at.txt").write_text(past.strftime("%H:%M"), encoding="utf-8")
        self.assertLessEqual(NS["resume_at"](t)[0], NS["time"].time() + 1)    # giờ vừa qua: chạy ngay
        (d / "resume_at.txt").write_text("khi nào rảnh", encoding="utf-8")
        self.assertIsNone(NS["resume_at"](t))                                  # sai định dạng: bỏ qua

    def test_user_message_cancels_timer_hold(self):
        t = {"id": "RT2", "text": "x", "status": "queued", "folder": "03 - Hẹn", "messages": [], "created": "x",
             "hold_until": NS["time"].time() + 900, "hold_timer": True, "next": {"kind": "timer", "text": "vẽ lại 3 ảnh"}}
        NS["TASKS"]["RT2"] = t
        try:
            NS["act"]("RT2", "send", "làm bằng Gemini luôn")
            self.assertNotIn("hold_until", t)                                  # tin người dùng chạy ngay, không chờ giờ hẹn
            self.assertEqual(t["next"]["kind"], "urgent")
            self.assertIn("vẽ lại 3 ảnh", t["next"]["text"])
            self.assertIn("làm bằng Gemini luôn", t["next"]["text"])
            t2 = {**t, "id": "RT3", "hold_until": NS["time"].time() + 900, "next": {"kind": "resume"}}
            t2.pop("hold_timer", None)
            NS["TASKS"]["RT3"] = t2
            NS["act"]("RT3", "send", "tiếp đi")
            self.assertIn("hold_until", t2)                                    # Claude hết lượt: vẫn phải chờ reset
        finally:
            NS["TASKS"].pop("RT2", None)
            NS["TASKS"].pop("RT3", None)

    def test_prompt_rules(self):
        t = {"id": "RT4", "text": "x", "mode": "balanced", "folder": "03 - Hẹn", "watch": False}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Hết lượt: chuyển bên kia, hoặc ghi", md)
        self.assertIn("Không viết vòng lặp sleep", md)
        self.assertIn("tasks/resume_at.txt", md)
        t.update(session_started=True)
        p = NS["_build_prompt"](t, {"kind": "timer", "text": "vẽ lại 3 ảnh"})
        self.assertIn("Tới giờ hẹn", p)
        self.assertIn("vẽ lại 3 ảnh", p)
        self.assertIn("quota", p)


class AskOnlyMidway(unittest.TestCase):
    """Nút A/B chỉ cho câu hỏi ngắt giữa chừng (question.md); câu trả lời không lặp lại lựa chọn; mặc định Claude tự quyết."""
    def test_strip_duplicate_options(self):
        rt = ("Bản 1.0.13 đã build và test xong.\n\nFile: D:\\x\\a.zip\n\nBạn chọn:\n\n- A - Phát hành ngay (đề xuất)\n"
              "- B - Chưa phát hành, tôi tự cài thử\n\nPhát hành lên kênh cập nhật không?")
        out = NS["strip_ask"](rt)
        self.assertIn("Bản 1.0.13 đã build và test xong.", out)
        self.assertIn("a.zip", out)
        self.assertNotIn("Phát hành ngay", out)                     # lựa chọn đã có trong question.md: không lặp
        self.assertNotIn("Bạn chọn", out)
        self.assertNotIn("không?", out)
        self.assertEqual(NS["strip_ask"]("Đã sửa xong 3 lỗi."), "Đã sửa xong 3 lỗi.")

    def test_strip_options_with_sub_bullets(self):                 # 12.8: lựa chọn có dòng con + câu mời chọn không kết thúc bằng "?"
        rt = ("**Đã kiểm tra:**\n- Plugin đang bật: GitHub, Notion.\n\n**Phương án đưa PWA lên mạng:**\n"
              "- A - **GitHub Pages** (đề xuất):\n  - Không cần cài thêm gì.\n  - Codex tự đẩy lên nhánh `gh-pages`.\n"
              "- B - **Cài plugin Vercel:**\n  - Bạn cần đăng nhập 1 lần.\n\n"
              "Bạn muốn làm phương án nào? Tôi đề xuất A cho việc PWA. Nếu cần thêm công cụ thì làm B.")
        out = NS["strip_ask"](rt)
        self.assertIn("Plugin đang bật: GitHub, Notion.", out)
        for x in ("GitHub Pages", "Không cần cài thêm gì", "đăng nhập 1 lần", "Phương án đưa PWA", "phương án nào"):
            self.assertNotIn(x, out, x)                                # không để dòng con / tiêu đề / câu mời chọn mồ côi
        self.assertEqual(NS["strip_ask"]("Kết quả:\n- Vì sao lỗi: do hết lượt.\n- File: a.png"),
                         "Kết quả:\n- Vì sao lỗi: do hết lượt.\n- File: a.png")

    def test_prompt_only_mechanism(self):
        """v14: mã chung chỉ dạy cách dùng Maestro (nút hỏi giữa chừng); khi nào hỏi, cách báo cáo, git: theo người dùng."""
        t = {"id": "AQ1", "text": "x", "mode": "balanced", "folder": "04 - Hỏi", "watch": False}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("app hiện thành nút bấm", md)
        self.assertIn("không chép lại câu hỏi", md)
        for gone in ("MẶC ĐỊNH TỰ QUYẾT", "KHÔNG kết thúc câu trả lời bằng câu hỏi", "Còn tồn tại", "Đã kiểm tra", "commit", "push",
                     "Không dùng API key"):
            self.assertNotIn(gone, md, gone)
        self.assertLess(md.index("Thứ tự ưu tiên"), md.index("## Vai trò"))        # 1 dòng thứ tự ưu tiên ở đầu file


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
        old_on, old_low, old_free = NS["workers_on"], NS["LOW_SPEC"], NS["free_setup"]
        NS["workers_on"] = lambda t: ["codex", "gemini"]          # như máy có đủ GPT + Gemini, cấu hình không thấp, có gói trả phí
        NS["LOW_SPEC"] = False
        NS["free_setup"] = lambda: False
        try:
            NS["write_claude_md"](t)
        finally:
            NS["workers_on"] = old_on
            NS["free_setup"] = old_free
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn('"A - <ngắn gọn>"', md)                     # câu hỏi dạng lựa chọn (nút bấm)
        self.assertIn("app chuyển THẲNG file", md)
        self.assertIn("--group <nhóm>", md)
        self.assertIn("Số worker song song: bạn tự quyết", md)    # Claude tự chọn số worker theo hạn mức/giờ reset
        self.assertIn("app gửi kèm tin nhắn mỗi lượt", md)
        old = NS["workers_on"]
        NS["workers_on"] = lambda t: ["codex", "gemini"]
        try:
            p = NS["build_prompt"](t, {"kind": "start"})
            self.assertIn("## Hạn mức lúc bắt đầu lượt", p)       # hạn mức đi kèm tin nhắn mỗi lượt
            self.assertIn("ChatGPT", p)
            self.assertNotIn("## Chia việc", p)
        finally:
            NS["workers_on"] = old
            NS["LOW_SPEC"] = old_low
        self.assertNotIn("Song song tối đa 3 worker", md)
        t2 = {"id": "Q2", "text": "x", "mode": "eco", "folder": "01 - Hỏi", "watch": False}
        NS["write_claude_md"](t2)
        self.assertIn("1 worker một lúc", (NS["wdir"](t2) / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(NS["MAX_PER_TASK"], NS["MAX_WORKERS"])
        self.assertNotIn("rồi chép bản đạt sang OUTPUT", md)


class V140(unittest.TestCase):
    """14.0: luật lõi ngắn + file chuyên biệt; kiểu trả lời; bỏ ghi chú phân loại; suy nghĩ ít cho câu hỏi nhanh; đặt tên task;
    nhóm thư mục; giới hạn cứng bằng mã (1 worker, chặn xoá ngoài thư mục); tóm tắt phiên mới; đánh số trong khung chat."""
    def _md(self, tid, text="vẽ 3 icon", mode="balanced", **kw):
        t = {"id": tid, "text": text, "mode": mode, "folder": "14 - " + tid, **kw}
        NS["write_claude_md"](t)
        return t, (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")

    def test_core_short_rules_split(self):
        old_on, old_free = NS["workers_on"], NS["free_setup"]
        NS["workers_on"], NS["free_setup"] = (lambda t: ["codex", "gemini"]), (lambda: False)
        try:
            for mode in ("pro", "balanced", "turbo", "eco"):
                t, md = self._md("CS" + mode, text="Viết 3 tiêu đề", mode=mode)
                core = md.split("## Vai trò", 1)[1]                    # phần của app (không tính hướng dẫn người dùng)
                self.assertLessEqual(len(core.splitlines()), 52, mode)
                self.assertEqual(len([x for x in md.splitlines() if x.startswith("## Chế độ")]), 1)
                self.assertLessEqual(len(md.split("## Chế độ", 1)[1].split("\n## ", 1)[0].strip().splitlines()), 3, mode)   # chế độ: 1 dòng
        finally:
            NS["workers_on"], NS["free_setup"] = old_on, old_free
        t, md = self._md("CS1", text="vẽ 3 icon")
        rules = NS["wdir"](t) / "rules"
        for k in ("image", "markup", "drive", "edit"):
            self.assertTrue((rules / f"{k}.md").is_file(), k)
            self.assertIn(f"rules/{k}.md", md)
        self.assertNotIn("Storyboard: mặc định", md)                   # nội dung chuyên biệt không nằm trong file lõi
        self.assertIn("rules/image.md — tạo ảnh, storyboard  <- task này có vẻ cần", md)
        self.assertNotIn("rules/drive.md — Google Drive/Sheets/Docs/Slides (đọc, sửa trực tiếp)  <-", md)
        self.assertNotIn("lỗi thì app tự giao bên kia", (rules / "image.md").read_text(encoding="utf-8"))   # luật cũ sai (app không tự chuyển)
        p = NS["_build_prompt"](t, {"kind": "start"})
        self.assertIn("./rules/image.md", p)                           # nhắc đọc đúng file 1 lần
        t["session_started"] = True
        self.assertNotIn("./rules/image.md", NS["_build_prompt"](t, {"kind": "followup", "text": "tiếp"}))

    def test_worker_brief_four_parts(self):
        _, md = self._md("WB1")
        self.assertIn("Mục tiêu / Bối cảnh", md)
        self.assertIn("Ràng buộc", md)
        self.assertIn("Xong khi", md)

    def test_reply_style(self):
        old = NS["CFG"].get("reply_style")
        try:
            NS["CFG"]["reply_style"] = "short"
            _, md = self._md("RS1")
            self.assertIn("NGẮN GỌN, DỄ HIỂU", md)
            self.assertIn('"## Chi tiết"', md)
            self.assertLess(md.index("NGẮN GỌN"), md.index("## Vai trò"))     # thuộc lớp của người dùng, không phải luật app
            NS["CFG"]["reply_style"] = "detail"
            _, md2 = self._md("RS2")
            self.assertIn("Kiểu trả lời: CHI TIẾT", md2)
            self.assertNotIn("NGẮN GỌN", md2)
        finally:
            NS["CFG"]["reply_style"] = old or "short"
        self.assertEqual(NS["load_cfg"]().get("reply_style") in ("short", "detail"), True)

    def test_no_route_note(self):
        t = {"id": "RN1", "text": "tại sao task chậm?", "mode": "balanced", "folder": "14 - RN1", "session_started": True}
        for nxt in ({"kind": "followup", "text": "tại sao task chậm?"}, {"kind": "followup", "text": "đổi màu nút"}):
            self.assertNotIn("Đường xử lý", NS["_build_prompt"](t, nxt))
        self.assertNotIn("ROUTE_NOTE", NS)

    def test_ask_effort(self):
        t = {"session_started": True}
        ae = lambda q: NS["ask_effort"](t, {"kind": "followup", "text": q})
        self.assertEqual(ae("file kết quả ở đâu?"), "low")
        self.assertIsNone(ae("tại sao task chậm?"))                    # cần lập luận: giữ mức của chế độ
        self.assertIsNone(ae("nên dùng PNG hay JPG cho banner?"))
        self.assertIsNone(ae("sửa lỗi đăng nhập"))                     # không phải câu hỏi

    def test_first_line_title(self):
        f = NS["first_line_title"]
        self.assertEqual(f("NoteTasker\nnâng cấp hướng bản mới, thêm đồng bộ 2 máy"), "NoteTasker")
        self.assertEqual(f("**MusicPlus 1.0.13**\nđóng gói bản nhanh"), "MusicPlus 1.0.13")
        self.assertEqual(f("Gợi ý cải tiến game Idle Clicker"), "")          # 1 dòng: để model đặt tên
        self.assertEqual(f("Chào bạn\ngiúp tôi làm banner"), "")
        self.assertEqual(f("Yêu cầu:\nvẽ banner"), "")
        self.assertEqual(f("Đây là một dòng đầu rất dài vượt quá bốn mươi ký tự cho phép\nx"), "")
        r = NS["new_task"]("NoteTasker\nnâng cấp hướng bản mới")
        old = NS["threading"].Thread
        started = []
        NS["threading"].Thread = lambda *a, **k: type("T", (), {"start": lambda s: started.append(1)})()
        try:
            NS["submit_task"](r["id"], [])
        finally:
            NS["threading"].Thread = old
        t = NS["TASKS"][r["id"]]
        self.assertEqual(t["title"], "NoteTasker")
        self.assertFalse(t.get("titling"))
        self.assertEqual(started, [])                                  # không gọi model đặt tên
        NS["TASKS"].pop(r["id"], None)

    def test_group_for_short_job_names(self):
        eg = NS["export_group"]
        self.assertEqual(eg(Path("R1"), "", ["research.md"]), "docs")     # lỗi cũ: việc R1 -> thư mục "R"
        self.assertEqual(eg(Path("S2"), "", ["a.png", "b.png"]), "images")
        self.assertEqual(eg(Path("X1"), "", []), "ket_qua")
        self.assertEqual(eg(Path("research_v2"), "", ["a.md"]), "research")
        self.assertEqual(eg(Path("R1"), "research", ["a.md"]), "research")

    def test_solo_worker_lock(self):
        d = Path(tempfile.mkdtemp())
        f = NS["_take_lock"](d, 1)
        self.assertTrue(f and f.exists())
        f.write_text("99999999")                                        # khoá của tiến trình đã chết -> thu hồi được
        f2 = NS["_take_lock"](d, 1)
        self.assertEqual(f2.read_text(), str(os.getpid()))

    def test_guard_blocks_delete_outside(self):
        root = Path(tempfile.mkdtemp()) / "01 - G"
        t = {"id": "GD1", "text": "x", "mode": "balanced", "folder": "14 - GD1"}
        NS["wdir"](t).mkdir(parents=True, exist_ok=True)
        args = NS["guard_settings"](t)
        self.assertEqual(args[0], "--settings")
        hook = json.loads(args[1])["hooks"]["PreToolUse"][0]
        self.assertIn("Bash", hook["matcher"])
        g = NS["wdir"](t) / ".guard.py"
        od = NS["out_dir"](t)

        def run(cmd):
            p = subprocess.run([sys.executable, str(g)], input=json.dumps({"tool_input": {"command": cmd}}), capture_output=True,
                               text=True, encoding="utf-8")
            return p.returncode
        self.assertEqual(run("ls -la"), 0)
        self.assertEqual(run(f'rm -rf "{od / "tmp"}"'), 0)                 # trong OUTPUT: cho phép
        self.assertEqual(run("rm -f work/S1/v1/x.png && echo ok 2>/dev/null"), 0)   # đường dẫn tương đối (thư mục task)
        self.assertEqual(run(f'cp "{root}" x && rm -rf "C:/Users/Someone/Documents/important"'), 2)
        self.assertEqual(run('Remove-Item -Recurse "D:\\Projects\\game"'), 2)
        self.assertEqual(run("rm -rf ~/Documents"), 2)
        self.assertEqual(run(f'"{sys.executable}" -c "print(1)" && rm -f a.txt'), 0)  # python của app không bị coi là đường dẫn lạ
        self.assertEqual(run("cat /c/Windows/win.ini"), 0)               # chỉ đọc: không chặn

    def test_open_user_export_folder(self):
        """Task xuất ra ổ chung (nơi người dùng chỉ định) mà thư mục mặc định cũng có bản sao -> nút Mở thư mục mở ổ chung."""
        t = {"id": "EX1", "text": "x", "status": "done", "folder": "14 - EX1", "messages": []}
        od = NS["out_dir"](t)
        (od / "20261127-images").mkdir(parents=True, exist_ok=True)
        share = Path(tempfile.mkdtemp()) / "O chung" / "Idle Clicker"
        share.mkdir(parents=True)
        since = time.time() - 5
        (od / "20261127-images" / "a.png").write_bytes(b"x")        # bản sao trong thư mục mặc định
        (share / "a.png").write_bytes(b"x")
        txt = f"Đã xuất 1 ảnh vào `{share}` (bản nháp ở `{od}`)."
        fs, d = NS["new_files"](t, since, txt)
        self.assertEqual(Path(d), share)
        self.assertEqual(fs, ["a.png"])
        fs2, d2 = NS["new_files"](t, since, "Xong, file ở thư mục kết quả.")
        self.assertEqual(d2, "")                                        # không nêu nơi khác: thư mục mặc định như cũ
        t["messages"] = [{"role": "claude", "text": txt, "files": fs, "dir": str(share)}]
        NS["TASKS"]["EX1"] = t
        opened = []
        old = NS["open_path"]
        NS["open_path"] = lambda p, *a: opened.append(Path(p))
        try:
            NS["act"]("EX1", "open")
        finally:
            NS["open_path"] = old
            NS["TASKS"].pop("EX1", None)
        self.assertEqual(opened, [share])

    def test_popups_close_and_viewer(self):
        ui = (SRC / "web" / "ui.html").read_text(encoding="utf-8")
        self.assertIn('document.addEventListener("pointerdown"', ui)     # chạm ra ngoài đóng popup (iPhone không có click)
        self.assertIn('tg === $("ov")) closeSettings()', ui)
        self.assertIn('id="edClose"', ui)                               # nút X luôn hiện trên trình xem ảnh
        self.assertIn('edTool("hand")', ui)                             # mở ảnh: chế độ xem, không tự bật đánh dấu
        self.assertIn("edPinchStart", ui)                               # chụm 2 ngón phóng to

    def test_handover_keeps_last_answer(self):
        long_plan = "Kế hoạch v12.9: " + "mục rất dài " * 300
        t = {"id": "HO1", "text": "x", "folder": "14 - HO1",
             "messages": [{"role": "user", "text": "u" * 400}, {"role": "claude", "text": "a" * 900}] * 6
             + [{"role": "user", "text": "kết hợp các đề xuất"}, {"role": "claude", "text": long_plan}]}
        h = NS["handover_text"](t)
        self.assertIn(long_plan[:2500].replace("\n", " "), h)            # câu trả lời gần nhất không bị cắt ở 700 ký tự
        self.assertLessEqual(len(h), 9000)

    def test_chat_numbering_and_details(self):
        ui = (SRC / "web" / "ui.html").read_text(encoding="utf-8")
        start = ui.index("function mdInline(")
        end = ui.index("function dur(")
        js = ("const esc=(s)=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');\n" + ui[start:end] +
              "\nconst r=md(process.argv[2]); console.log(JSON.stringify(r));")
        f = Path(tempfile.mkdtemp()) / "md.js"
        f.write_text(js, encoding="utf-8")
        src = ("**Kế hoạch**\n\n1. Sửa lỗi đánh số\n   - Nguyên nhân: x\n   - Cách sửa: y\n\n2. Phân lớp\n- con trực tiếp\n"
               "3. Kiểu trả lời\n   Dòng giải thích thụt vào\n\nKết luận.\n\n## Chi tiết\n- file a.py\n- lệnh b")
        try:
            out = json.loads(subprocess.run(["node", str(f), src], capture_output=True, text=True, encoding="utf-8", timeout=30).stdout)
        except FileNotFoundError:
            self.skipTest("không có node")
        self.assertEqual(out.count("<ol"), 1, out)                      # 1 danh sách số duy nhất: 1, 2, 3 (không 1, 1, 1)
        self.assertEqual(out.count("<ul>"), 3, out)                     # dòng con nằm TRONG mục số
        self.assertLess(out.index("<ol"), out.index("<ul>"))
        self.assertIn("<br>Dòng giải thích thụt vào", out)
        self.assertIn('<details class="mdd"><summary>Xem chi tiết</summary>', out)
        self.assertTrue(out.endswith("</details>"))
        self.assertIn("file a.py", out.split("<details")[1])
        out2 = json.loads(subprocess.run(["node", str(f), "Phần 1:\n\n1. a\n\nGiải thích\n\n2. b"], capture_output=True, text=True,
                                         encoding="utf-8", timeout=30).stdout)
        self.assertIn('<ol start="2">', out2)                           # danh sách bị ngắt bởi đoạn văn: đếm tiếp đúng số đang viết


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
