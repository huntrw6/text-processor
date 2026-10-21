#!/usr/bin/env python3
"""process - one command for both passes.

    process text.md -technical            ASD-STE100 Simplified Technical English (the default)
    process text.md -caveman              fewer tokens: drop filler, pleasantries, and (full/ultra) articles
    process text.md -technical -caveman   technical first, then a light shrink (lite level only)

    The result goes to updated-text.md. The original is not changed.
    process text.md new-text.md -caveman  choose the output name yourself
    process text.md -caveman --in-place   update text.md itself (the original is kept as text.md.bak)
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

import ste
import terse

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="process",
        description="Rewrite a Markdown file. -technical: ASD-STE100 Simplified Technical English. -caveman: fewer tokens. "
                    "Use both to run technical first, then a light shrink. With no tag, -technical runs.")
    p.add_argument("input", help="Markdown (.md) or plain text (.txt) file to process")
    p.add_argument("output", nargs="?", help="Write the result here (default: updated-NAME next to the input)")
    p.add_argument("--in-place", action="store_true", help="Update the input file instead (a .bak copy is kept)")
    p.add_argument("--as", dest="as_format", choices=["markdown", "text"], help="Treat the file as this format whatever its extension")
    m = p.add_argument_group("modes")
    m.add_argument("-technical", "--technical", action="store_true", help="ASD-STE100: short sentences, active voice, no contractions, simple tenses")
    m.add_argument("-caveman", "--caveman", action="store_true", help="Fewer tokens: no filler or pleasantries; at full and ultra also no articles")
    c = p.add_argument_group("-caveman options")
    c.add_argument("--level", choices=terse.LEVELS, help="lite | full (default, or $TERSE_LEVEL) | ultra. With -technical, only lite is allowed.")
    c.add_argument("--include-warnings", action="store_true", help="Also shorten paragraphs that contain safety words")
    c.add_argument("--min-words", type=int, default=6, help="Skip paragraphs shorter than this (default 6)")
    t = p.add_argument_group("-technical options")
    t.add_argument("--disable", help="Comma-separated rule ids to turn off, for example 3.5,GR-7")
    t.add_argument("--only", help="Comma-separated rule ids to check")
    t.add_argument("--max-procedural", type=int, help="Word limit for instructions (default 20)")
    t.add_argument("--max-descriptive", type=int, help="Word limit for descriptive text (default 25)")
    t.add_argument("--min-severity", choices=list(ste.SEVERITIES), default="warning")
    t.add_argument("--no-dict", action="store_true", help="Ignore the STE dictionary file")
    g = p.add_argument_group("both modes")
    g.add_argument("--no-llm", action="store_true", help="Use the built-in rules only")
    g.add_argument("--cmd", help="Use this command as the LLM, for example 'claude -p'")
    g.add_argument("--model", help="Model for the Anthropic API (default: claude-sonnet-5-5, or $STE_MODEL)")
    g.add_argument("--jobs", type=int, default=4)
    g.add_argument("--dry-run", action="store_true", help="Show a diff and write nothing")
    g.add_argument("--no-backup", action="store_true", help="With --in-place, do not keep a .bak copy")
    g.add_argument("--force", action="store_true", help="Replace the output file if it exists")
    g.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"process {__version__}")
    return p


def run(args) -> int:
    tech = args.technical or not args.caveman      # no tag = -technical
    cave = args.caveman
    both = tech and cave

    plan = ste.plan_files(args, "process")
    if isinstance(plan, int):
        return plan
    src, dest, in_place, fmt = plan

    level = None
    if cave:
        if both:
            if args.level in ("full", "ultra"):
                print("process: -technical with -caveman allows only --level lite. Dropping articles (full, ultra) breaks "
                      "ASD-STE100 Rule 4.2 (do not omit words). Use lite, or run -caveman on its own.", file=sys.stderr)
                return 2
            level = "lite"
        else:
            level = args.level or os.environ.get("TERSE_LEVEL") or "full"
            if level not in terse.LEVELS:
                print(f"process: level must be one of {', '.join(terse.LEVELS)} (got '{level}')", file=sys.stderr)
                return 2

    text = ste.read_text(src)
    if text is None:
        return 2
    cfg = ste.apply_cli_config(args, src.resolve().parent)
    cfg.plain = fmt == "text"
    dic = None if args.no_dict else ste.Dictionary.find(cfg.dictionary, cfg.root)
    llm, label = ste.pick_llm(args)
    if llm is None and label is None:
        print("process: no LLM found, so only the built-in rules run. For better results, set ANTHROPIC_API_KEY, "
              "install Claude Code, or pass --cmd.", file=sys.stderr)
    modes = " + ".join(m for m, on in (("technical", tech), ("caveman" + (f" ({level})" if level else ""), cave)) if on)
    print(f"{src.as_posix()}: {modes}; using {label or 'built-in rules (no LLM)'}", file=sys.stderr)

    keep = lambda fs: [f for f in fs if ste.SEVERITIES[f.severity] >= ste.SEVERITIES[args.min_severity]]
    new = text
    ste_before = len(keep(ste.lint_text(text, cfg, dic)))

    if tech:
        if llm is None:
            new, _ = ste.apply_safe_fixes(new, cfg, dic)
            detail = ""
        else:
            new, st = ste.rewrite_text(new, cfg, dic, llm, args.min_severity, args.jobs, args.verbose)
            detail = f"; {st['rewritten']} of {st['candidates']} paragraph(s) rewritten"
            for line, why in st["reasons"]:
                if args.verbose or not why.startswith("rewritten"):
                    print(f"  technical, line {line}: kept as is: {why}" if not why.startswith("rewritten") else f"  technical, line {line}: {why}", file=sys.stderr)
        mid = len(keep(ste.lint_text(new, cfg, dic)))
        print(f"  technical: {ste_before} -> {mid} STE finding(s){detail}", file=sys.stderr)

    if cave:
        before_text = new
        new, st = terse.terse_text(new, level, llm, args.include_warnings, args.min_words, args.jobs, args.verbose, plain=cfg.plain)
        wb, wa = st["words_before"], st["words_after"]
        pct = f" ({(wa - wb) * 100 // wb:+d}%)" if wb else ""
        print(f"  caveman: prose {wb} -> {wa} words{pct}; shortened {st['changed']} of {st['blocks']} paragraph(s)", file=sys.stderr)
        for line, why in st["kept"]:
            if args.verbose or (not why.startswith("shortened") and why not in ("nothing to shorten", "unchanged")):
                print(f"  caveman, line {line}: {why}", file=sys.stderr)

    tb, ta = terse.est_tokens(text), terse.est_tokens(new)
    tpct = f" ({(ta - tb) * 100 // tb:+d}%)" if tb else ""
    print(f"  file: ~{tb} -> ~{ta} tokens{tpct} (estimate, 4 characters per token)", file=sys.stderr)
    if both:
        final = len(keep(ste.lint_text(new, cfg, dic)))
        print(f"  STE findings after both passes: {final}", file=sys.stderr)

    if args.dry_run:
        ste.show_diff(src.as_posix(), text, new)
        return 0
    if in_place:
        if new == text:
            print("No changes.", file=sys.stderr)
            return 0
        if not args.no_backup:
            bak = ste.backup_path(src)
            ste.write_text(bak, text)
            print(f"Backup of the original: {bak.as_posix()}", file=sys.stderr)
        ste.write_text(src, new)
        print(f"Updated {src.as_posix()}", file=sys.stderr)
    else:
        ste.write_text(dest, new)
        print(f"Wrote {dest.as_posix()} (the original is unchanged)", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------------------
# File manager menus (Nautilus "Scripts", Dolphin service menu)
# --------------------------------------------------------------------------------------

MENU_ITEMS = [
    # (file name, Nautilus menu label, process flags, Dolphin label)
    ("process-technical", "Process - technical", ["-technical"], "Technical (ASD-STE100)"),
    ("process-caveman", "Process - caveman", ["-caveman"], "Caveman (fewer tokens)"),
    ("process-both", "Process - technical + caveman", ["-technical", "-caveman"], "Technical + caveman (lite)"),
]

WRAPPER = """#!/usr/bin/env bash
# Generated by: process --install-scripts   (remove with: process --uninstall-scripts)
# File managers do not read ~/.bashrc. Put ANTHROPIC_API_KEY=... in ~/.config/process/env if you use the API.
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:$PATH"
if [ -f "$HOME/.config/process/env" ]; then set -a; . "$HOME/.config/process/env"; set +a; fi

PROCESS=__PROCESS__
FLAGS=(__FLAGS__)

notify() {
  if command -v notify-send >/dev/null 2>&1; then notify-send -a process "$1" "$2"; else echo "$1: $2"; fi
}

if [ "$#" -eq 0 ]; then notify "Process" "No file selected."; exit 1; fi
for f in "$@"; do
  case "$f" in
    *.md|*.markdown|*.mdown|*.mkd|*.txt|*.text) ;;
    *) notify "Process" "Skipped $(basename "$f"): only .md and .txt files are supported."; continue ;;
  esac
  if out=$(python3 "$PROCESS" "$f" "${FLAGS[@]}" 2>&1); then
    notify "Process done: $(basename "$f")" "$(printf '%s\\n' "$out" | tail -n 6)"
  else
    notify "Process FAILED: $(basename "$f")" "$(printf '%s\\n' "$out" | tail -n 6)"
  fi
done
"""

ENV_TEMPLATE = """# Read by the right-click menu scripts (file managers do not load ~/.bashrc).
# Uncomment and fill in if you use the Anthropic API. If you use Claude Code (claude -p), you do not need this.
# ANTHROPIC_API_KEY=sk-ant-...
# STE_MODEL=claude-sonnet-5-5
"""


def wrapper_text(process_path: Path, flags: list) -> str:
    return (WRAPPER.replace("__PROCESS__", shlex.quote(str(process_path)))
                   .replace("__FLAGS__", " ".join(shlex.quote(f) for f in flags)))


def _write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        path.unlink()
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _paths(home: Path) -> dict:
    return {
        "nautilus": home / ".local" / "share" / "nautilus" / "scripts",
        "dolphin": home / ".local" / "share" / "kio" / "servicemenus",
        "support": home / ".local" / "share" / "process",
        "env": home / ".config" / "process" / "env",
    }


def install_scripts(target: str = "auto", home: Optional[Path] = None) -> int:
    home = home or Path.home()
    p = _paths(home)
    process_path = Path(__file__).resolve().parent / "process"
    if not process_path.exists():
        print(f"process: cannot find {process_path}", file=sys.stderr)
        return 1
    for f in (process_path, Path(__file__).resolve(), process_path.parent / "terse.py", process_path.parent / "ste.py"):
        try:   # downloaded files often lose the executable bit
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        except OSError:
            pass
    want = {"nautilus", "dolphin"} if target == "all" else {target} if target in ("nautilus", "dolphin") else set()
    if target == "auto":
        if shutil.which("nautilus") or p["nautilus"].parent.exists():
            want.add("nautilus")
        if shutil.which("dolphin") or p["dolphin"].exists():
            want.add("dolphin")
    if not want:
        print("process: found neither Nautilus nor Dolphin. Choose one: process --install-scripts nautilus|dolphin|all", file=sys.stderr)
        return 1

    if "nautilus" in want:
        for fname, label, flags, _ in MENU_ITEMS:
            _write_exec(p["nautilus"] / label, wrapper_text(process_path, flags))
        print(f"Nautilus: added {len(MENU_ITEMS)} scripts to {p['nautilus']}")
        print("  Right-click a .md file > Scripts > Process - ...   (if it does not appear, run: nautilus -q  and reopen)")
    if "dolphin" in want:
        for fname, _, flags, _ in MENU_ITEMS:
            _write_exec(p["support"] / fname, wrapper_text(process_path, flags))
        actions = "".join(f"{fname};" for fname, *_ in MENU_ITEMS)
        lines = ["[Desktop Entry]", "Type=Service", "MimeType=text/markdown;text/x-markdown;text/plain;", f"Actions={actions}",
                 "X-KDE-Submenu=Process", "Icon=text-x-generic", ""]
        for fname, _, _, dlabel in MENU_ITEMS:
            lines += [f"[Desktop Action {fname}]", f"Name={dlabel}", "Icon=text-x-generic",
                      f'Exec="{p["support"] / fname}" %F', ""]
        _write_exec(p["dolphin"] / "process.desktop", "\n".join(lines))
        print(f"Dolphin: added a 'Process' service menu: {p['dolphin'] / 'process.desktop'}")
        print("  Right-click a .md file > Process > ...   (if it does not appear, reopen Dolphin)")
    if not p["env"].exists():
        p["env"].parent.mkdir(parents=True, exist_ok=True)
        p["env"].write_text(ENV_TEMPLATE, encoding="utf-8")
        p["env"].chmod(0o600)
    print(f"\nUsing the Anthropic API from the menu? Put ANTHROPIC_API_KEY in {p['env']} (file managers do not read ~/.bashrc).")
    print("Results appear as a desktop notification. Each file gets a new updated-FILE next to it; the original is not changed.")
    return 0


def uninstall_scripts(home: Optional[Path] = None) -> int:
    home = home or Path.home()
    p = _paths(home)
    removed = 0
    for _, label, _, _ in MENU_ITEMS:
        f = p["nautilus"] / label
        if f.exists():
            f.unlink()
            removed += 1
    for fname, *_ in MENU_ITEMS:
        f = p["support"] / fname
        if f.exists():
            f.unlink()
            removed += 1
    f = p["dolphin"] / "process.desktop"
    if f.exists():
        f.unlink()
        removed += 1
    print(f"Removed {removed} file(s). Your settings in {p['env']} were kept.")
    return 0


def manage_scripts(cmd: str, rest: list) -> int:
    if cmd == "--uninstall-scripts":
        return uninstall_scripts()
    return install_scripts(rest[0] if rest else "auto")


def main(argv: Optional[list] = None) -> int:
    return run(build_parser().parse_intermixed_args(argv))   # flags and file names may come in any order


if __name__ == "__main__":
    sys.exit(main())
