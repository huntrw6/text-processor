"""Tests for verify.py. Run: python3 -m unittest discover -s tests"""
import contextlib, io, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "process-text", "scripts"))
import verify

ORIG = """---
title: Guide
---

# Setup

Do not edit `config.toml` while the server runs. The default port is 8080, see https://example.com/docs for MAX_SIZE.

```bash
npm install
```

Never delete the cache directory in the project, because it takes 30 seconds to rebuild.
"""


class Verify(unittest.TestCase):
    def check(self, new, **kw):
        return verify.verify(ORIG, new, **kw)

    def test_good_rewrite_passes(self):
        new = ORIG.replace("Do not edit `config.toml` while the server runs.", "Do not edit `config.toml` during server run.") \
                  .replace("Never delete the cache directory in the project, because it takes 30 seconds to rebuild.",
                           "Never delete cache dir: 30 seconds to rebuild.")
        problems, notes = self.check(new)
        self.assertEqual(problems, [])

    def test_dropped_negation(self):
        problems, _ = self.check(ORIG.replace("Do not edit", "Edit"))
        self.assertTrue(any("negation" in p and "line 7" in p for p in problems), problems)

    def test_dropped_number_and_identifier(self):
        problems, _ = self.check(ORIG.replace("8080", "the default").replace("MAX_SIZE", "the size"))
        self.assertTrue(any("numbers missing: 8080" in p for p in problems), problems)
        self.assertTrue(any("MAX_SIZE" in p for p in problems), problems)

    def test_changed_code_inline_and_block(self):
        problems, _ = self.check(ORIG.replace("`config.toml`", "`config.yaml`"))
        self.assertTrue(any("missing code" in p for p in problems), problems)
        problems, _ = self.check(ORIG.replace("npm install", "npm ci"))
        self.assertTrue(any("code blocks changed" in p for p in problems), problems)

    def test_changed_front_matter_heading_and_url(self):
        self.assertIn("front matter changed", self.check(ORIG.replace("title: Guide", "title: Other"))[0])
        self.assertIn("headings changed", self.check(ORIG.replace("# Setup", "# Install"))[0])
        problems, _ = self.check(ORIG.replace("https://example.com/docs", "the docs"))
        self.assertTrue(any("missing code" in p for p in problems), problems)

    def test_merged_paragraphs_skip_the_paragraph_checks(self):
        two = "First paragraph is fine here.\n\nSecond paragraph is also fine here.\n"
        problems, notes = verify.verify(two, "First paragraph is fine here. Second paragraph is also fine here.\n")
        self.assertTrue(notes and "paragraph count changed" in notes[0])
        self.assertEqual(problems, [])
        # merging a paragraph into a closing code fence is a real problem and is reported
        problems, _ = self.check(ORIG.replace("\n\nNever delete", " Never delete"))
        self.assertTrue(any("code blocks changed" in p for p in problems), problems)

    def test_require_shorter(self):
        longer = ORIG.replace("Never delete the cache", "Never ever delete the cache")
        self.assertEqual(self.check(longer)[0], [])
        self.assertTrue(any("not shorter" in p for p in self.check(longer, require_shorter=True)[0]))

    def test_cli_exit_codes_and_plain_text(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.txt", Path(d) / "b.txt"
            a.write_text("Do not run it 3 times in a row today.\n", encoding="utf-8")
            b.write_text("Run it 3 times.\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(verify.main([str(a), str(b)]), 1)
            self.assertIn("negation", out.getvalue())
            b.write_text("Never run it 3 times.\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify.main([str(a), str(b)]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(verify.main([str(a), str(Path(d) / "nope.txt")]), 2)
                py = Path(d) / "x.py"
                py.write_text("x=1\n", encoding="utf-8")
                self.assertEqual(verify.main([str(py), str(py)]), 2)


if __name__ == "__main__":
    unittest.main()
