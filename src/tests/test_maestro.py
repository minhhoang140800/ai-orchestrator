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
            for v in ("v1", "v2"):
                (NS["wdir"](t) / "work" / "S1" / v).mkdir(parents=True, exist_ok=True)
            self.assertEqual(NS["open_sub_dir"]("SD1", "S1"), {"ok": True})
            self.assertEqual(opened[-1].name, "v2")                          # bản mới nhất
            self.assertIn("error", NS["open_sub_dir"]("SD1", "../../x"))     # chặn đường dẫn lạ
            self.assertIn("error", NS["open_sub_dir"]("SD1", "S9"))
        finally:
            NS["open_path"] = old
            NS["TASKS"].pop("SD1", None)

    def test_prompt_rule(self):
        t = {"id": "Q1", "text": "x", "mode": "balanced", "folder": "01 - Hỏi", "watch": False}
        NS["write_claude_md"](t)
        md = (NS["wdir"](t) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("TRƯỚC khi bắt tay", md)
        self.assertIn("ĐANG làm mà vướng", md)
        self.assertIn('"A - <ngắn gọn>"', md)                     # câu hỏi dạng lựa chọn (nút bấm)
        self.assertIn("chép NGAY file của việc đó sang OUTPUT", md)


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
