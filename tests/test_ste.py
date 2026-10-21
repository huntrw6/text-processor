"""Tests for ste.py. Run: python3 -m unittest -v test_ste"""
import io, json, os, re, sys, tempfile, threading, unittest, contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "process-text", "scripts"))
import ste

CFG = ste.Config()


def rules(text, cfg=CFG, dic=None):
    return [f.rule for f in ste.lint_text(text, cfg, dic)]


class WordCount(unittest.TestCase):
    """Examples printed in ASD-STE100 Issue 9, Rule 8.5 to 8.7."""

    CASES = {
        "Make sure that the temperature in the room is 10 °C.": 10,
        "The unit weighs 20 kg.": 4,
        "The unit weighs 20 kilograms.": 4,
        "Touch the \u201cService Overview\u201d arrow to select the function page.": 9,
        "For remote access, use the VPN.": 6,
        "Clean the surface with a soap-and-water solution.": 7,
        "Do steps 13 thru 16 a minimum of three times.": 10,
        "Tag circuit breaker 36L7.": 4,
        "The spar box has twenty-one ribs.": 6,
        "Examine the No. 1 bearing installation.": 5,
        "The resistance must be 10 ohms.": 5,
        "The maintenance team does a test of this system each day at 10 a.m.": 13,
    }

    def test_standard_examples(self):
        for text, n in self.CASES.items():
            self.assertEqual(ste.count_words(ste.mask(text)), n, text)

    def test_parentheses_code_and_links(self):
        self.assertEqual(ste.count_words(ste.mask("Use `foo --bar baz` (see the manual for more details) now.")), 4)
        self.assertEqual(ste.count_words(ste.mask("Read [the guide](https://example.com/a/b/c) now.")), 4)

    def test_proper_nouns_with_names(self):
        cfg = ste.Config(names=["United States of America", "George Washington"])
        b = ste.Block("paragraph", [(0, "", "x " * 14 + "The United States of America and George Washington.")])
        self.assertEqual([f.rule for f in ste.analyze_block(b, cfg)], [])


class Sentences(unittest.TestCase):
    def split(self, t):
        s = ste.mask(t)
        return [s[a:b] for a, b in ste.split_sentences(s)]

    def test_abbreviations_do_not_split(self):
        self.assertEqual(len(self.split("Use a tool, e.g. a wrench, to turn it. Then stop.")), 2)
        self.assertEqual(len(self.split("See README.md and v1.2.3 for details. Done.")), 2)

    def test_code_starts_sentence(self):
        self.assertEqual(len(self.split("Run it. `foo` returns a value.")), 2)


class Rules(unittest.TestCase):
    def test_semicolon_fix(self):
        new, n = ste.apply_safe_fixes("Examine the removed parts; replace the damaged ones.\n", CFG, None)
        self.assertEqual(new, "Examine the removed parts. Replace the damaged ones.\n")

    def test_contractions(self):
        new, _ = ste.apply_safe_fixes("If your hands are wet, don't touch it. It's fine, and you can't stop it.\n", CFG, None)
        self.assertEqual(new, "If your hands are wet, do not touch it. It is fine, and you cannot stop it.\n")

    def test_tenses(self):
        self.assertIn("3.2", rules("The operator has adjusted the linkage."))
        self.assertIn("3.2", rules("When you are doing this procedure, obey all the safety precautions."))
        self.assertNotIn("3.2", rules("The operator adjusted the linkage and it will work."))
        self.assertNotIn("3.2", rules("The boxes have built-in padding."))

    def test_passive(self):
        fs = ste.lint_text("The safety procedures are given by the manufacturer.", CFG, None)
        self.assertEqual([(f.rule, f.severity) for f in fs], [("3.6", "warning")])
        self.assertIn("3.6", rules("The volume control can be adjusted."))

    def test_length_limits(self):
        long_proc = "1. " + " ".join(["Open"] + ["the"] * 20 + ["file."])
        self.assertIn("5.1", rules(long_proc))
        desc = "The system " + " ".join(["is"] * 24) + " slow."
        self.assertIn("6.3", rules(desc))
        self.assertNotIn("6.3", rules("The system is slow."))

    def test_procedural_vs_descriptive_is_per_sentence(self):
        # 24 words: too long for an instruction (20), allowed in descriptive text (25).
        desc = "The tool " + " ".join(["is"] * 21) + " slow."
        proc = "Open " + " ".join(["the"] * 22) + " file."
        self.assertEqual(rules("Run the tool now. " + desc), [])
        self.assertEqual(rules("Run the tool now. " + proc), ["5.1"])

    def test_paragraph_length(self):
        self.assertIn("6.6", rules("One. Two. Three. Four. Five. Six. Seven."))
        self.assertNotIn("6.6", rules("One. Two. Three. Four. Five. Six."))

    def test_vocabulary_and_spelling(self):
        new, _ = ste.apply_safe_fixes("Colour themes help us utilize the cache prior to launch. Ensure that it works.\n", CFG, None)
        self.assertEqual(new, "Color themes help us use the cache before launch. Make sure that it works.\n")
        self.assertIn("1.2", rules("You should restart it."))
        self.assertNotIn("1.2", rules("May 2026 was a busy month."))

    def test_latin_and_gender(self):
        self.assertIn("GR-6", rules("Use tools (e.g. a wrench)."))
        self.assertIn("GR-7", rules("The user can change his settings."))

    def test_disable_and_allow_words(self):
        cfg = ste.Config(disable={"4.2"})
        self.assertNotIn("4.2", rules("Don't stop.", cfg))
        cfg = ste.Config(disable={"3"})            # whole section
        self.assertNotIn("3.2", rules("It has changed.", cfg))
        cfg = ste.Config(allow_words={"logging"})
        self.assertEqual([f.rule for f in ste.lint_text("The logging system works.", cfg, None, "x")], [])

    def test_procedure_hints(self):
        self.assertIn("5.3", rules("1. You need to restart the shell."))
        self.assertIn("5.2", rules("Open the file and close the editor."))
        self.assertIn("5.4", [f.rule for f in ste.lint_text("Set the switch to NORMAL when the light comes on.", CFG, None)])


class Markdown(unittest.TestCase):
    def test_skips_non_prose(self):
        md = "\n".join([
            "---", "title: It's a test; really", "---", "",
            "# Don't flag headings; ever", "",
            "```python", "x = 'don't'; y = 2", "```", "",
            "    indented code; don't flag", "",
            "| a | b |", "| - | - |", "| don't; stop | x |", "",
            "<div>don't</div>", "", "<!-- don't -->", "",
            "Use `don't; stop` here.", ""])
        self.assertEqual(rules(md), [])

    def test_directives(self):
        md = "Don't one.\n\n<!-- ste-disable -->\n\nDon't two.\n\n<!-- ste-enable -->\n\nDon't three.\n"
        lines = [f.line for f in ste.lint_text(md, CFG, None)]
        self.assertEqual(lines, [1, 9])
        md = "<!-- ste: procedural -->\n\nThe tool " + " ".join(["is"] * 18) + " slow.\n"
        self.assertIn("5.1", rules(md))

    def test_positions_in_lists_and_quotes(self):
        md = "- item one\n- It's here\n\n> Don't go\n"
        got = [(f.line, f.col) for f in ste.lint_text(md, CFG, None)]
        self.assertEqual(got, [(2, 3), (4, 3)])

    def test_multiline_paragraph_positions(self):
        md = "This is fine.\nBut it's not.\n"
        f = ste.lint_text(md, CFG, None)[0]
        self.assertEqual((f.line, f.col), (2, 5))
        self.assertEqual(md[f.start:f.end], "it's")

    def test_adjacent_fixes_all_apply(self):
        new, _ = ste.apply_safe_fixes("Don't stop; it's fine.\n", CFG, None)
        self.assertEqual(new, "Do not stop. It is fine.\n")

    def test_fixes_keep_untouched_text(self):
        md = "```\ndon't; keep\n```\n\nIt's a `don't; keep` test.\n"
        new, _ = ste.apply_safe_fixes(md, CFG, None)
        self.assertEqual(new, "```\ndon't; keep\n```\n\nIt is a `don't; keep` test.\n")


class Dictionary(unittest.TestCase):
    TEXT = (
        "A (art)            Function word:               A FUEL PUMP IS\n"
        "                   indefinite article           INSTALLED IN\n"
        "abaft (prep)       AFT OF (prep)                THE CONTROL UNIT IS The control unit is\n"
        "ensure (v)         MAKE SURE (v)                MAKE SURE THAT      Ensure that\n"
        "                   VERIFY (v)                   DO IT               x\n"
        "INTERCHANGE-       That you can           RELAYS 2YG AND 8YG\n"
        "ABLE (adj)         interchange            ARE FULLY\n"
        "however (adv)      BUT (conj)                   IT IS OLD, BUT      It is old, however\n"
        "REMOVE (v)         To take away                 REMOVE THE PUMP.\n"
    )

    def test_parse(self):
        d = ste.parse_dictionary(self.TEXT)
        self.assertEqual(d["non_approved"]["ensure"]["alts"][0], "MAKE SURE (v)")
        self.assertIn("interchangeable", d["approved"])
        self.assertNotIn("able", d["approved"])
        self.assertIn("remove", d["approved"])

    def test_lookup_and_noun_filter(self):
        dic = ste.Dictionary({"approved": ["remove"], "non_approved": {"however": {"pos": "adv", "alts": ["BUT (conj)"]},
                                                                      "file": {"pos": "v", "alts": ["REMOVE (v)"]}}})
        cfg = ste.Config()
        fs = ste.lint_text("However, it works.", cfg, dic)
        self.assertEqual([(f.rule, f.severity) for f in fs], [("1.2", "info")])
        self.assertEqual(ste.lint_text("Open the file now.", cfg, dic), [])   # noun use after 'the'

    def test_computer_verbs_are_allowed(self):
        dic = ste.Dictionary({"approved": [], "non_approved": {"click": {"pos": "v", "alts": ["CLICK (n)"]}}})
        self.assertEqual(ste.lint_text("Click the button.", ste.Config(), dic), [])

    @unittest.skipUnless(os.environ.get("STE_PDF") and os.path.exists(os.environ["STE_PDF"]), "set STE_PDF to the standard's PDF")
    def test_real_pdf(self):
        d = ste.build_dictionary_from_pdf(os.environ["STE_PDF"])
        self.assertGreater(len(d["non_approved"]), 1000)
        self.assertEqual(d["non_approved"]["utilize"]["alts"], ["USE (v)"])


class Rewrite(unittest.TestCase):
    def llm_factory(self, fn):
        return lambda system, user: "<rewrite>" + fn(re.search(r"<original>\n(.*?)\n</original>", user, re.S).group(1)) + "</rewrite>"

    def test_placeholders_round_trip(self):
        t, store = ste.protect("Run `a b` and see [docs](http://x.y/z) or http://q.r.")
        self.assertNotIn("`", t)
        self.assertEqual(ste.unprotect(t, store), "Run `a b` and see [docs](http://x.y/z) or http://q.r.")

    def test_accepts_improvement_and_rejects_bad_replies(self):
        text = "The server was configured by the administrator for `prod`.\n"
        b = ste.parse_markdown(text)[0]
        fs = ste.analyze_block(b, CFG)
        good = self.llm_factory(lambda o: "The administrator configured the server for ⟦0⟧.")
        new, why = ste.rewrite_block(b, fs, CFG, None, good)
        self.assertEqual(new, "The administrator configured the server for `prod`.")
        lost = self.llm_factory(lambda o: "The administrator configured the server.")
        self.assertIsNone(ste.rewrite_block(b, fs, CFG, None, lost)[0])
        worse = self.llm_factory(lambda o: o + " It's done; it's fine.")
        self.assertIsNone(ste.rewrite_block(b, fs, CFG, None, worse)[0])
        structure = self.llm_factory(lambda o: "- " + o)
        self.assertIsNone(ste.rewrite_block(b, fs, CFG, None, structure)[0])

    def test_wrapping_never_starts_a_line_like_markdown_structure(self):
        text = "aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk lll mmm nnn ooo ppp qqq rrr sss ttt uuu vvv www xxx\nyyy zzz\n"
        b = ste.parse_markdown(text)[0]
        new = "aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk lll mmm nnn ooo ppp qqq rrr sss ttt uuu vvv www xxx - yyy 1. zzz > end"
        _, _, rep = ste.place_block_text(b, new, ste.line_starts_of(text), text)
        for line in rep.split("\n")[1:]:
            self.assertFalse(re.match(r"([-*+>#]|\d+[.)])(\s|$)", line), line)

    def test_rewrite_text_keeps_markdown_structure(self):
        text = "# Title\n\n1. The file was moved by the script.\n2. Run `go`.\n\n> It was written by the team\n> and then it was sent by the bot.\n"
        llm = self.llm_factory(lambda o: o.replace("The file was moved by the script.", "The script moved the file.")
                               .replace("It was written by the team and then it was sent by the bot.", "The team wrote it. The bot sent it."))
        new, st = ste.rewrite_text(text, CFG, None, llm, "warning", 1, False)
        self.assertEqual(new, "# Title\n\n1. The script moved the file.\n2. Run `go`.\n\n> The team wrote it. The bot sent it.\n")
        self.assertEqual(st["rewritten"], 2)


class ApiPath(unittest.TestCase):
    def test_anthropic_request(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.update(path=self.path, key=self.headers["x-api-key"], ver=self.headers["anthropic-version"], body=body)
                data = json.dumps({"content": [{"type": "text", "text": "<rewrite>ok</rewrite>"}]}).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = ste.call_anthropic("sys", "usr", "model-x", "k", f"http://127.0.0.1:{srv.server_address[1]}")
        finally:
            srv.shutdown()
        self.assertEqual(out, "<rewrite>ok</rewrite>")
        self.assertEqual((seen["path"], seen["key"], seen["ver"]), ("/v1/messages", "k", "2023-06-01"))
        self.assertEqual(seen["body"]["messages"], [{"role": "user", "content": "usr"}])
        self.assertEqual(seen["body"]["system"], "sys")


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ste.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_check_exit_codes_and_formats(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.md"
            f.write_text("Don't stop.\n", encoding="utf-8")
            rc, out, _ = self.run_cli("check", str(f))
            self.assertEqual(rc, 1)
            self.assertIn("[4.2]", out)
            rc, out, _ = self.run_cli("check", str(f), "--format", "json")
            self.assertEqual(json.loads(out)[0]["rule"], "4.2")
            rc, out, _ = self.run_cli("check", str(f), "--format", "github")
            self.assertTrue(out.startswith("::error file="))
            rc, _, _ = self.run_cli("check", str(f), "--disable", "4.2")
            self.assertEqual(rc, 0)
            f.write_text("It is fine.\n", encoding="utf-8")
            self.assertEqual(self.run_cli("check", str(f))[0], 0)

    def test_fix_dry_run_and_write(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.md"
            f.write_text("Don't stop.\n", encoding="utf-8")
            rc, out, _ = self.run_cli("fix", str(f))
            self.assertIn("+Do not stop.", out)
            self.assertEqual(f.read_text(encoding="utf-8"), "Don't stop.\n")
            self.run_cli("fix", str(f), "--write")
            self.assertEqual(f.read_text(encoding="utf-8"), "Do not stop.\n")

    def test_process_two_files_keeps_original_and_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "text.md", Path(d) / "new-text.md"
            src.write_bytes(b"# T\r\n\r\nDon't stop; it's fine.\r\n")
            rc, _, _ = self.run_cli("process", str(src), str(out), "--no-llm")
            self.assertEqual(rc, 0)
            self.assertEqual(out.read_bytes(), b"# T\r\n\r\nDo not stop. It is fine.\r\n")
            self.assertEqual(src.read_bytes(), b"# T\r\n\r\nDon't stop; it's fine.\r\n")
            self.assertEqual(self.run_cli("process", str(src), str(out), "--no-llm")[0], 2)   # no silent overwrite
            self.assertEqual(self.run_cli("process", str(src), str(out), "--no-llm", "--force")[0], 0)

    def test_process_in_place_makes_backup(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "text.md"
            src.write_text("Don't stop.\n", encoding="utf-8")
            self.run_cli("process", str(src), "--no-llm", "--in-place")
            self.assertEqual(src.read_text(encoding="utf-8"), "Do not stop.\n")
            self.assertEqual((Path(d) / "text.md.bak").read_text(encoding="utf-8"), "Don't stop.\n")
            src.write_text("Don't go.\n", encoding="utf-8")
            self.run_cli("process", str(src), "--no-llm", "--in-place")          # second run must not overwrite the first backup
            self.assertEqual((Path(d) / "text.md.bak").read_text(encoding="utf-8"), "Don't stop.\n")
            self.assertEqual((Path(d) / "text.md.bak1").read_text(encoding="utf-8"), "Don't go.\n")
            src.write_text("Don't run.\n", encoding="utf-8")
            self.run_cli("process", str(src), "--no-llm", "--in-place", "--no-backup")
            self.assertFalse((Path(d) / "text.md.bak2").exists())

    def test_process_default_writes_updated_file_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "text.md"
            src.write_text("Don't stop.\n", encoding="utf-8")
            self.assertEqual(self.run_cli("process", str(src), "--no-llm")[0], 0)
            self.assertEqual(src.read_text(encoding="utf-8"), "Don't stop.\n")
            self.assertEqual((Path(d) / "updated-text.md").read_text(encoding="utf-8"), "Do not stop.\n")
            self.assertFalse((Path(d) / "text.md.bak").exists())
            self.assertEqual(self.run_cli("process", str(src), "--no-llm")[0], 2)          # updated-text.md exists
            self.assertEqual(self.run_cli("process", str(src), "--no-llm", "--force")[0], 0)
            self.assertEqual(self.run_cli("process", str(src), str(src.with_name("o.md")), "--in-place")[0], 2)

    def test_process_refuses_code_and_config_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("code.py", "conf.json", "page.html", "x.yaml"):
                f = Path(d) / name
                f.write_text("# Don't touch; it's code\n", encoding="utf-8")
                rc, _, err = self.run_cli("process", str(f), "--no-llm")
                self.assertEqual(rc, 2, name)
                self.assertIn("not supported", err)
                self.assertFalse((Path(d) / ("updated-" + name)).exists())
            f = Path(d) / "notes.rst"
            f.write_text("Don't stop.\n", encoding="utf-8")
            self.assertEqual(self.run_cli("process", str(f), "--no-llm", "--as", "text")[0], 0)
            self.assertEqual((Path(d) / "updated-notes.rst").read_text(encoding="utf-8"), "Do not stop.\n")

    def test_plain_text_mode(self):
        txt = "---\ntitle: It's here\n---\n\n    Indented, and it's prose.\n\n| a | b; c |\n| - | - |\n"
        md_rules = [f.rule for f in ste.lint_text(txt, CFG, None)]
        plain = ste.Config(plain=True)
        txt_rules = [f.rule for f in ste.lint_text(txt, plain, None)]
        self.assertEqual(md_rules, [])     # Markdown: front matter, indented code, and tables are skipped
        # plain text: the indented paragraph (4.2) and the line with pipes (8.1) are prose, so they are checked
        self.assertEqual(txt_rules, ["4.2", "8.1"])

    def test_file_format_detection(self):
        ff = ste.file_format
        self.assertEqual(ff(Path("a.MD")), "markdown")
        self.assertEqual(ff(Path("a.txt")), "text")
        self.assertEqual(ff(Path("README")), "text")
        self.assertIsNone(ff(Path("a.py")))
        self.assertEqual(ff(Path("a.py"), "text"), "text")

    def test_check_handles_txt_files_and_skips_code(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("Don't stop.\n", encoding="utf-8")
            (Path(d) / "b.py").write_text("# Don't stop.\n", encoding="utf-8")
            rc, out, err = self.run_cli("check", str(Path(d) / "a.txt"), str(Path(d) / "b.py"))
            self.assertEqual(rc, 1)
            self.assertIn("a.txt:1:1", out)
            self.assertNotIn("b.py:1", out)
            self.assertIn("unsupported file type", err)

    def test_process_dry_run_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "text.md"
            src.write_text("Don't stop.\n", encoding="utf-8")
            rc, out, _ = self.run_cli("process", str(src), "--no-llm", "--dry-run")
            self.assertIn("+Do not stop.", out)
            self.assertEqual(src.read_text(encoding="utf-8"), "Don't stop.\n")
            self.assertEqual(self.run_cli("process", str(Path(d) / "nope.md"))[0], 2)

    def test_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".ste.toml").write_text('[ste]\ndisable = ["4.2"]\nexclude = ["skip.md"]\n', encoding="utf-8")
            (Path(d) / "a.md").write_text("Don't stop.\n", encoding="utf-8")
            (Path(d) / "skip.md").write_text("Don't; stop.\n", encoding="utf-8")
            old = os.getcwd()
            os.chdir(d)
            try:
                rc, out, _ = self.run_cli("check", ".")
            finally:
                os.chdir(old)
            self.assertEqual(rc, 0)
            self.assertNotIn("skip.md", out)


if __name__ == "__main__":
    unittest.main()
