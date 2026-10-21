"""Tests for the unified process command (proc.py). Run: python3 -m unittest -v test_proc"""
import contextlib, io, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "process-text", "scripts"))
import proc

SRC = ("Sure, it's really important to note that the application stores the data; "
       "you should basically never edit it in order to change the settings.\n")


class Process(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = proc.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def result(self, *flags):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "t.md", Path(d) / "o.md"
            src.write_text(SRC, encoding="utf-8")
            rc, _, err = self.run_cli(str(src), str(out), *flags, "--no-llm")
            return rc, (out.read_text(encoding="utf-8") if out.exists() else None), err

    def test_technical_only(self):
        rc, text, err = self.result("-technical")
        self.assertEqual(rc, 0)
        self.assertEqual(text, "Sure, it is really important to note that the application stores the data. "
                               "You should basically never edit it in order to change the settings.\n")

    def test_default_is_technical(self):
        self.assertEqual(self.result()[1], self.result("-technical")[1])
        self.assertIn("technical", self.result()[2])

    def test_caveman_only_levels(self):
        _, lite, _ = self.result("-caveman", "--level", "lite")
        _, full, _ = self.result("-caveman")
        _, full2, _ = self.result("--caveman", "--level", "full")
        self.assertEqual(lite, "It's important to note that the application stores the data; you should never edit it to change the settings.\n")
        self.assertEqual(full, full2)
        self.assertNotIn(" the ", full)

    def test_both_runs_technical_then_lite(self):
        rc, text, err = self.result("-technical", "-caveman")
        self.assertEqual(rc, 0)
        self.assertEqual(text, "The application stores the data. You should never edit it to change the settings.\n")
        self.assertIn("caveman (lite)", err)
        self.assertIn("STE findings after both passes", err)

    def test_both_refuses_full_and_ultra(self):
        for lv in ("full", "ultra"):
            rc, text, err = self.result("-technical", "-caveman", "--level", lv)
            self.assertEqual(rc, 2)
            self.assertIsNone(text)
            self.assertIn("Rule 4.2", err)

    def test_flags_and_files_in_any_order(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "t.md", Path(d) / "o.md"
            src.write_text(SRC, encoding="utf-8")
            self.assertEqual(self.run_cli(str(src), "-caveman", str(out), "--no-llm")[0], 0)
            self.assertTrue(out.exists())

    def test_in_place_backup_dry_run_force_and_errors(self):
        # --in-place updates the input and keeps a .bak copy; the default writes a separate file (tested below)
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "t.md", Path(d) / "o.md"
            src.write_bytes(SRC.replace("\n", "\r\n").encode())
            rc, diff, _ = self.run_cli(str(src), "-technical", "--no-llm", "--dry-run")
            self.assertIn("it is really", diff)
            self.assertEqual(src.read_bytes(), SRC.replace("\n", "\r\n").encode())       # dry run writes nothing
            self.assertEqual(self.run_cli(str(src), "-technical", "--no-llm", "--in-place")[0], 0)
            self.assertTrue((Path(d) / "t.md.bak").exists())
            self.assertIn(b"\r\n", src.read_bytes())                                       # CRLF kept
            out.write_text("x", encoding="utf-8")
            self.assertEqual(self.run_cli(str(src), str(out), "-caveman", "--no-llm")[0], 2)
            self.assertEqual(self.run_cli(str(src), str(out), "-caveman", "--no-llm", "--force")[0], 0)
            self.assertEqual(self.run_cli(str(Path(d) / "nope.md"), "-caveman")[0], 2)

    def test_default_output_is_updated_prefix_next_to_input(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "docs"
            sub.mkdir()
            src = sub / "guide.md"
            src.write_text(SRC, encoding="utf-8")
            self.assertEqual(self.run_cli(str(src), "-caveman", "--no-llm")[0], 0)
            self.assertEqual(src.read_text(encoding="utf-8"), SRC)
            self.assertTrue((sub / "updated-guide.md").exists())
            self.assertFalse((sub / "guide.md.bak").exists())
            self.assertEqual(self.run_cli(str(src), "-caveman", "--no-llm")[0], 2)
            self.assertEqual(self.run_cli(str(src), "-caveman", "--no-llm", "--force")[0], 0)
            self.assertEqual(self.run_cli(str(src), str(sub / "x.md"), "--in-place", "--no-llm")[0], 2)

    def test_text_files_work_and_code_files_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            txt, py = Path(d) / "notes.txt", Path(d) / "tool.py"
            txt.write_text("Sure, it's really important to note that the cache stores the data; never clear it.\n\n"
                           "    Please just run it now, and it is really fast for the user today.\n", encoding="utf-8")
            py.write_text("def f():\n    # Sure, it's really important\n    return 1\n", encoding="utf-8")
            self.assertEqual(self.run_cli(str(txt), "-technical", "-caveman", "--no-llm")[0], 0)
            out = (Path(d) / "updated-notes.txt").read_text(encoding="utf-8")
            self.assertIn("The cache stores the data. Never clear it.", out)
            self.assertIn("    Run it now, and it is fast for the user today.", out)         # indented text is prose here
            rc, _, err = self.run_cli(str(py), "-caveman", "--no-llm")
            self.assertEqual(rc, 2)
            self.assertIn("not supported", err)
            self.assertFalse((Path(d) / "updated-tool.py").exists())
            self.assertEqual(py.read_text(encoding="utf-8").count("\n"), 3)                  # untouched

    def test_llm_flags_are_gone(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "t.md"
            src.write_text(SRC, encoding="utf-8")
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    proc.main([str(src), "--cmd", "claude -p"])
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    proc.main([str(src), "--model", "x"])


class Menus(unittest.TestCase):
    def test_install_writes_working_scripts_and_uninstall_removes_them(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            home.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(proc.install_scripts("all", home=home), 0)
            naut = home / ".local/share/nautilus/scripts"
            names = sorted(p.name for p in naut.iterdir())
            self.assertEqual(names, ["Process - caveman", "Process - technical", "Process - technical + caveman"])
            for p in naut.iterdir():
                self.assertTrue(os.access(p, os.X_OK))
                self.assertEqual(subprocess.run(["bash", "-n", str(p)]).returncode, 0)
            desk = home / ".local/share/kio/servicemenus/process.desktop"
            self.assertTrue(os.access(desk, os.X_OK))
            self.assertIn("Actions=process-technical;process-caveman;process-both;", desk.read_text())
            self.assertFalse((home / ".config/process").exists())                # no env file: nothing reads secrets
            for q in naut.iterdir():
                self.assertNotIn("config/process/env", q.read_text())

            # run a generated script the way a file manager does (path with spaces, a non-Markdown file)
            docs = Path(d) / "my docs"
            docs.mkdir()
            md, txt, py = docs / "a b.md", docs / "x.txt", docs / "y.py"
            md.write_text(SRC, encoding="utf-8")
            txt.write_text("Don't stop.\n", encoding="utf-8")
            py.write_text("# Don't\n", encoding="utf-8")
            # a stub notify-send (found first through ~/.local/bin) makes the output the same on every host
            stub = home / ".local" / "bin" / "notify-send"
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text('#!/bin/sh\necho "$3: $4"\n', encoding="utf-8")
            stub.chmod(0o755)
            env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"}
            r = subprocess.run([str(naut / "Process - technical"), str(md), str(txt), str(py)], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(md.read_text(encoding="utf-8"), SRC)                                   # original untouched
            self.assertIn("it is really", (docs / "updated-a b.md").read_text(encoding="utf-8"))   # new file, path with spaces
            self.assertEqual((docs / "updated-x.txt").read_text(encoding="utf-8"), "Do not stop.\n")
            self.assertFalse((docs / "updated-y.py").exists())
            self.assertIn("only .md and .txt files are supported", r.stdout)

            with contextlib.redirect_stdout(io.StringIO()):
                proc.uninstall_scripts(home=home)
            self.assertEqual(list(naut.iterdir()), [])
            self.assertFalse(desk.exists())
            self.assertFalse((home / ".config/process").exists())

    def test_install_needs_a_target_when_no_file_manager_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ["PATH"]
            os.environ["PATH"] = "/nonexistent"
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(proc.install_scripts("auto", home=Path(d)), 1)
            finally:
                os.environ["PATH"] = old


if __name__ == "__main__":
    unittest.main()
