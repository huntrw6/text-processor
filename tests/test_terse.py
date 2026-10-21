"""Tests for terse.py. Run: python3 -m unittest -v test_terse"""
import contextlib, io, os, re, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "process-text", "scripts"))
import terse


def shrink(text, level="full"):
    p, store = terse.protect(text)
    import ste
    return ste.unprotect(terse.det_compress(p, level), store)


class Rules(unittest.TestCase):
    def test_levels(self):
        t = "Sure, it is really important to note that the application stores its configuration in order to start."
        self.assertEqual(shrink(t, "lite"), "The application stores its configuration to start.")
        self.assertEqual(shrink(t, "full"), "Application stores its configuration to start.")
        self.assertEqual(shrink(t, "ultra"), "App stores its config to start.")

    def test_filler_and_wordy_phrases(self):
        self.assertEqual(shrink("Please just run it. We basically utilize a cache prior to launch.", "lite"),
                         "Run it. We use a cache before launch.")

    def test_pleasantry_sentences(self):
        self.assertEqual(shrink("It works. Hope this helps! Let me know if you have questions.", "lite"), "It works.")

    def test_meaning_words_survive(self):
        self.assertEqual(shrink("It is not very fast, rather than slow. Use it just in case.", "lite"),
                         "It is not very fast, rather than slow. Use it just in case.")
        self.assertEqual(shrink("This is very fast.", "lite"), "This is fast.")

    def test_protected_spans_never_change(self):
        t = ('Run `the quick` fox with --the-flag in src/the/app.py or ./the_file.md and see "the best of the world" '
             'at https://example.com/the/path, set API_KEY_NAME for v1.2.3 now.')
        out = shrink(t, "ultra")
        for keep in ["`the quick`", "--the-flag", "src/the/app.py", "./the_file.md", '"the best of the world"',
                     "https://example.com/the/path", "API_KEY_NAME", "v1.2.3"]:
            self.assertIn(keep, out)

    def test_article_edge_cases(self):
        self.assertEqual(shrink("Use a tool, e.g. a wrench, to turn the nut.", "full"), "Use tool, e.g. wrench, to turn nut.")
        self.assertEqual(shrink("The file is open. A lock exists.", "full"), "File is open. Lock exists.")
        self.assertEqual(shrink("Plan A is the default.", "full"), "Plan A is default.")

    def test_ultra_abbreviations(self):
        self.assertEqual(shrink("The database takes approximately 30 seconds. Documentation: see info.", "ultra"),
                         "DB takes ~30 seconds. Docs: see info.")


class Validate(unittest.TestCase):
    def test_accepts_good(self):
        self.assertIsNone(terse.validate("The server must not restart before 10 minutes pass.", "Server must not restart before 10 minutes pass."))

    def test_rejections(self):
        v = terse.validate
        self.assertEqual(v("Do not run it twice now.", "Run it twice."), "a negation was dropped")
        self.assertEqual(v("Wait for 30 seconds before you retry.", "Wait before retry."), "a number was dropped")
        self.assertEqual(v("Set \u27e60\u27e7 now please.", "Set now."), "placeholders for code or links were changed")
        self.assertEqual(v("Set the MAX_SIZE value to the limit.", "Set max size to limit."), "an identifier was changed")
        self.assertEqual(v("Set it.", "Set it. And more words."), "not shorter")
        self.assertEqual(v("You can run it now and then stop it.", "- Run it."), "reply added Markdown structure")
        self.assertEqual(v("You can run it now and then stop it.", ""), "empty reply")


class Files(unittest.TestCase):
    def go(self, text, level="full", include=False, min_words=6):
        return terse.terse_text(text, level, include, min_words, False)

    def test_only_prose_changes(self):
        md = "\n".join([
            "---", "title: the guide really", "---", "",
            "# The really big heading about the thing", "",
            "```", "the really quick brown fox", "```", "",
            "| the key | really the value |", "| - | - |", "| the a | the b |", "",
            "<!-- terse-disable -->", "", "This is really the paragraph that stays as it is today.", "", "<!-- terse-enable -->", "",
            "This is really the paragraph that gets shorter today.", ""])
        new, st = self.go(md)
        self.assertEqual(new, md.replace("This is really the paragraph that gets shorter today.", "This is paragraph that gets shorter today."))

    def test_safety_text_is_skipped_unless_asked(self):
        md = "Warning: this will really delete the whole database for the user.\n"
        self.assertEqual(self.go(md)[0], md)
        self.assertNotEqual(self.go(md, include=True)[0], md)

    def test_short_paragraphs_skipped(self):
        md = "Just the really big one.\n"
        self.assertEqual(self.go(md, min_words=6)[0], md)

    def test_pleasantry_paragraph_removed_cleanly(self):
        md = "First paragraph here is quite long enough to stay.\n\nHope this helps! Let me know if you have any questions.\n\nLast paragraph here is long enough as well.\n"
        new, _ = self.go(md, "lite")
        self.assertEqual(new, "First paragraph here is long enough to stay.\n\nLast paragraph here is long enough as well.\n")

    def test_list_and_quote_structure_kept(self):
        md = "1. Please just run `go` in the project directory now.\n\n> It is really important that the tool works for the user.\n"
        new, _ = self.go(md)
        self.assertEqual(new, "1. Run `go` in project directory now.\n\n> It is important that tool works for user.\n")

    def test_idempotent(self):
        md = "Sure, it is really important to note that the application stores the data in the cache for the user.\n"
        once, _ = self.go(md)
        twice, _ = self.go(once)
        self.assertEqual(once, twice)


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = terse.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_two_files_in_place_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "text.md", Path(d) / "new-text.md"
            src.write_bytes(b"# T\r\n\r\nIt is really important to note that the cache stores the data.\r\n")
            self.assertEqual(self.run_cli(str(src), str(out), "--no-llm")[0], 0)
            self.assertEqual(out.read_bytes(), b"# T\r\n\r\nCache stores data.\r\n")
            self.assertTrue(src.read_bytes().endswith(b"stores the data.\r\n"))   # original untouched
            self.assertEqual(self.run_cli(str(src), str(out), "--no-llm")[0], 2)  # no silent overwrite
            self.assertEqual(self.run_cli(str(src), "--no-llm", "--in-place")[0], 0)
            self.assertTrue((Path(d) / "text.md.bak").exists())
            self.assertEqual(src.read_bytes(), out.read_bytes())

    def test_default_is_a_new_updated_file(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "text.md"
            src.write_text("It is really important to note that the cache stores the data.\n", encoding="utf-8")
            self.assertEqual(self.run_cli(str(src), "--no-llm")[0], 0)
            self.assertIn("really", src.read_text(encoding="utf-8"))                       # original untouched
            self.assertEqual((Path(d) / "updated-text.md").read_text(encoding="utf-8"), "Cache stores data.\n")
            self.assertEqual(self.run_cli(str(src), "--no-llm")[0], 2)                     # exists

    def test_plain_text_and_unsupported_types(self):
        with tempfile.TemporaryDirectory() as d:
            txt = Path(d) / "n.txt"
            txt.write_text("Intro line.\n\n    It is really important to note that the cache stores the data.\n", encoding="utf-8")
            self.assertEqual(self.run_cli(str(txt), "--no-llm")[0], 0)
            self.assertIn("    Cache stores data.", (Path(d) / "updated-n.txt").read_text(encoding="utf-8"))   # indented prose is processed
            py = Path(d) / "c.py"
            py.write_text("def f():\n    return 1\n", encoding="utf-8")
            self.assertEqual(self.run_cli(str(py), "--no-llm")[0], 2)
            self.assertFalse((Path(d) / "updated-c.py").exists())

    def test_dry_run_level_env_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "text.md"
            src.write_text("It is really important to note that the cache stores the data.\n", encoding="utf-8")
            rc, out, _ = self.run_cli(str(src), "--no-llm", "--dry-run", "--level", "lite")
            self.assertIn("+The cache stores the data.", out)
            self.assertEqual(src.read_text(encoding="utf-8").count("really"), 1)
            os.environ["TERSE_LEVEL"] = "ultra"
            try:
                rc, out, _ = self.run_cli(str(src), "--no-llm", "--dry-run")
            finally:
                del os.environ["TERSE_LEVEL"]
            self.assertIn("+Cache stores data.", out)
            self.assertEqual(self.run_cli(str(Path(d) / "nope.md"))[0], 2)


if __name__ == "__main__":
    unittest.main()
