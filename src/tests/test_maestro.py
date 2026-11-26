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

    def test_memory_injected(self):
        NS["mem_save"]("## Cách làm việc\n- Xưng hô tôi – bạn\n## Lịch sử\n- Dự án Alien")
        try:
            _, md = self._md("balanced")
            self.assertIn("Maestro tự ghi nhớ", md)
            self.assertIn("Dự án Alien", md)
        finally:
            NS["mem_save"]("")
        _, md = self._md("balanced")
        self.assertNotIn("Maestro tự ghi nhớ", md)


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
        self.assertIn("YYYYMMDD-<tên ngắn>", md)
        self.assertIn(time.strftime("%Y%m%d") + "-concept_boards", md)


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
