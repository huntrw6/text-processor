#!/usr/bin/env python3
"""verify - check that a rewritten file kept every fact of the original.

    python3 verify.py ORIGINAL UPDATED [--require-shorter] [--as markdown|text]

Use it after you (or an LLM) rewrote a file by hand. It reports:
  - code blocks, front matter, and headings that changed
  - code, links, URLs, paths, flags, constants, versions, and quoted text that are missing
  - numbers that disappeared
  - paragraphs (matched one to one, in order) that lost a negation (not, never, no) or an identifier

Exit code: 0 = nothing found, 1 = problems found, 2 = usage error.
It cannot prove the meaning is the same. It catches the common ways a rewrite loses facts.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import ste
import terse

FENCE_BLOCK_RE = re.compile(r"^(?P<f>`{3,}|~{3,})[^\n]*\n.*?^(?P=f)[ \t]*$", re.S | re.M)
FRONT_RE = re.compile(r"\A---\n.*?\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S)


def split_parts(text: str, plain: bool):
    """Return (front matter, fenced code blocks, text without them)."""
    text = text.replace("\r\n", "\n")
    front = ""
    if not plain:
        m = FRONT_RE.match(text)
        if m:
            front, text = m.group(0), text[m.end():]
    blocks = [] if plain else [m.group(0) for m in FENCE_BLOCK_RE.finditer(text)]
    rest = text if plain else FENCE_BLOCK_RE.sub("", text)
    return front, blocks, rest


def prose_blocks(text: str, plain: bool) -> list:
    return [b for b in ste.parse_markdown(text.replace("\r\n", "\n"), plain=plain) if not b.off]


def verify(orig: str, new: str, plain: bool = False, require_shorter: bool = False) -> tuple:
    """Return (problems, notes). Each problem is a string."""
    problems: list = []
    notes: list = []
    of, ob, orest = split_parts(orig, plain)
    nf, nb, nrest = split_parts(new, plain)

    if of != nf:
        problems.append("front matter changed")
    if ob != nb:
        problems.append(f"code blocks changed ({len(ob)} before, {len(nb)} after, or their content differs)")

    miss = Counter(m.group(0) for m in terse.PROTECT_RE.finditer(orest)) - Counter(m.group(0) for m in terse.PROTECT_RE.finditer(nrest))
    if miss:
        shown = ", ".join(sorted(miss)[:6])
        problems.append(f"missing code, link, path, flag, constant, version, or quoted text: {shown}" + (" ..." if len(miss) > 6 else ""))

    onums, nnums = set(terse.NUM_RE.findall(orest)), set(terse.NUM_RE.findall(nrest))
    if onums - nnums:
        problems.append("numbers missing: " + ", ".join(sorted(onums - nnums)[:8]))

    ob_blocks, nb_blocks = prose_blocks(orig, plain), prose_blocks(new, plain)
    oh = [b.text for b in ob_blocks if b.kind == "heading"]
    nh = [b.text for b in nb_blocks if b.kind == "heading"]
    if oh != nh:
        problems.append("headings changed")

    op = [b for b in ob_blocks if b.kind != "heading"]
    np_ = [b for b in nb_blocks if b.kind != "heading"]
    if len(op) != len(np_):
        notes.append(f"paragraph count changed ({len(op)} -> {len(np_)}), so the paragraph-by-paragraph checks were skipped")
    else:
        for a, b in zip(op, np_):
            o_text = " ".join(t.strip() for _, _, t in a.lines)
            n_text = " ".join(t.strip() for _, _, t in b.lines)
            o_p, o_store = terse.protect(o_text)
            n_p, n_store = terse.protect(n_text)
            line = a.lines[0][0] + 1
            if sorted(o_store) != sorted(n_store):
                problems.append(f"line {line}: code, link, or protected term changed in this paragraph")
                continue
            why = terse.validate(o_p, n_p, require_shorter=require_shorter)
            if why and why not in ("empty reply",):
                problems.append(f"line {line}: {why}")
    return problems, notes


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="verify", description="Check that a rewritten file kept every fact of the original.")
    p.add_argument("original")
    p.add_argument("updated")
    p.add_argument("--require-shorter", action="store_true", help="Also require each paragraph to be shorter (for -caveman)")
    p.add_argument("--as", dest="as_format", choices=["markdown", "text"], help="Treat the files as this format")
    args = p.parse_args(argv)
    a, b = Path(args.original), Path(args.updated)
    for f in (a, b):
        if not f.is_file():
            print(f"verify: no such file: {f}", file=sys.stderr)
            return 2
    fmt = ste.file_format(a, args.as_format)
    if fmt is None:
        print(f"verify: '{a.suffix}' files are not supported. Use --as text or --as markdown.", file=sys.stderr)
        return 2
    orig, new = ste.read_text(a), ste.read_text(b)
    if orig is None or new is None:
        return 2
    problems, notes = verify(orig, new, plain=(fmt == "text"), require_shorter=args.require_shorter)
    wo, wn = len(orig.split()), len(new.split())
    pct = f" ({(wn - wo) * 100 // wo:+d}%)" if wo else ""
    print(f"verify: {a.as_posix()} -> {b.as_posix()}: {wo} -> {wn} words{pct}")
    for n in notes:
        print(f"  note: {n}")
    for q in problems:
        print(f"  PROBLEM: {q}")
    if problems:
        print(f"{len(problems)} problem(s). Fix them in {b.as_posix()}, then run verify again.")
        return 1
    print("OK: no lost code, links, numbers, negations, or identifiers found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
