#!/usr/bin/env python3
"""terse - shrink the prose of a Markdown file to fewer tokens, and keep every fact.

    terse text.md                 write updated-text.md and leave text.md unchanged
    terse text.md new-text.md     write new-text.md instead
    terse text.md --in-place      update text.md itself (the original is kept as text.md.bak)
    terse text.md --level ultra   lite | full (default) | ultra

Inspired by the "caveman" skill (github.com/JuliusBrussee/caveman, MIT). This is an independent,
small implementation: it drops filler, pleasantries, wordy phrases, and (at full and ultra) articles.
It never touches code, inline code, links, URLs, paths, flags, numbers, quoted text, tables, or headings,
and it leaves safety text alone. Needs ste.py in the same folder.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import sys
from pathlib import Path
from typing import Optional

import ste

__version__ = "0.1.0"
LEVELS = ("lite", "full", "ultra")

# --------------------------------------------------------------------------------------
# What must never change
# --------------------------------------------------------------------------------------

_EXTRA = (
    r'"[^"\n]{1,200}"|\u201c[^\u201d\n]{1,200}\u201d'                       # quoted text
    r"|(?<![\w/])--?[A-Za-z][\w-]*"                                          # command-line flags
    r"|[\w.~-]*(?:/[\w.~-]+)+/?"                                             # paths
    r"|\b[\w-]+\.(?:md|py|js|ts|tsx|jsx|json|toml|ya?ml|sh|txt|html|css|go|rs|rb|java|cpp|sql|env|lock|cfg|ini)\b"  # file names
    r"|\bv?\d+(?:\.\d+)+\b"                                                  # version numbers
    r"|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"                                        # CONSTANTS and env vars
)
PROTECT_RE = re.compile(ste.PLACEHOLDER_RE.pattern + "|" + _EXTRA, re.S)
MARK = "\x02"   # "capitalize the next letter"

SAFETY_RE = re.compile(
    r"\b(?:warning|caution|danger|security|irreversible|irreversibly|undone|destructive|data loss|permanently|"
    r"drop table|rm -rf|force[- ]push|--force|delete all)\b", re.I)


def protect(text: str):
    store: list = []

    def sub(m):
        store.append(m.group(0))
        return f"\u27e6{len(store) - 1}\u27e7"

    return PROTECT_RE.sub(sub, text), store


# --------------------------------------------------------------------------------------
# Deterministic compression (no LLM)
# --------------------------------------------------------------------------------------

def _start(m) -> bool:
    """Is this match at the start of a sentence?"""
    before = m.string[: m.start()]
    if before == "" or before.endswith(MARK):
        return True
    if not re.search(r"[.!?][\"')\]]*\s+$", before):
        return False
    tok = re.search(r"(\S+)\s+$", before)
    t = tok.group(1).lower().rstrip(".") if tok else ""
    return not (t in ste.ABBREV or re.fullmatch(r"[a-z](\.[a-z])+", t) or len(t) == 1)


def _delete(m) -> str:
    return MARK if _start(m) else ""


PLEASANTRY_SENTENCES = re.compile(
    r"(?:(?<=[.!?]\s)|^)(?:I hope this helps|Hope this helps|Happy to help|Let me know if (?:you|there)[^.!?]*|"
    r"Feel free to (?:ask|reach out|contact)[^.!?]*)[.!]?\s*", re.I)
PLEASANTRY_START = re.compile(r"(?:(?<=[.!?]\s)|^)(?:sure|certainly|of course|absolutely|great|okay|ok|alright)\s*[,!.]\s*", re.I)
LEAD_INS = re.compile(
    r"\b(?:please note that|it is worth noting that|it should be noted that|it is important to note that|"
    r"it is important to remember that|keep in mind that|be aware that|as you can see,|needless to say,)\s*", re.I)
PLEASE = re.compile(r"\bplease\s+", re.I)
DEGREE = re.compile(r"(?<!not )(?<!n't )(?<!never )\b(?:really|quite|fairly|relatively|somewhat|very)\s+", re.I)
RATHER = re.compile(r"(?<!not )\brather\s+(?!than\b)", re.I)
FILLER = re.compile(r"\b(?:basically|actually|simply|essentially|literally|obviously|definitely)\s+", re.I)
JUST = re.compile(r"\bjust\s+(?=(?:run|use|add|set|call|open|click|type|copy|paste|install|create|write|put|pass|make|check|try|do|get|ask|need|want|have|be)\b)", re.I)

WORDY = {
    "in order to": "to", "so as to": "to", "due to the fact that": "because", "owing to the fact that": "because",
    "despite the fact that": "although", "in spite of the fact that": "although",
    "at this point in time": "now", "at the present time": "now", "in the event that": "if",
    "is able to": "can", "are able to": "can", "has the ability to": "can", "have the ability to": "can",
    "a large number of": "many", "a number of": "several", "the majority of": "most", "a majority of": "most",
    "for the purpose of": "for", "with regard to": "about", "with regards to": "about", "with respect to": "about",
    "in regard to": "about", "prior to": "before", "subsequent to": "after", "in addition to": "besides",
    "on a regular basis": "regularly", "at all times": "always", "each and every": "every", "whether or not": "whether",
    "make use of": "use", "makes use of": "uses", "making use of": "using",
    "utilize": "use", "utilizes": "uses", "utilized": "used", "utilizing": "using",
    "utilise": "use", "utilises": "uses", "utilised": "used", "utilising": "using",
}
WORDY_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in WORDY), key=len, reverse=True)) + r")\b", re.I)

ARTICLE_MID = re.compile(r"(?<![\w'\u2019./-])(?:the|an|a)\s+(?=[A-Za-z\u27e6])")
ARTICLE_START = re.compile(r"(?<![\w'\u2019./-])(?:The|An|A)\s+(?=[A-Za-z\u27e6])")

ABBREV = {
    "configuration": "config", "configurations": "configs", "database": "DB", "databases": "DBs",
    "application": "app", "applications": "apps", "environment": "env", "environments": "envs",
    "repository": "repo", "repositories": "repos", "directory": "dir", "directories": "dirs",
    "documentation": "docs", "information": "info", "implementation": "impl", "implementations": "impls",
    "parameter": "param", "parameters": "params", "authentication": "auth", "connection": "conn",
    "connections": "conns", "versus": "vs", "and then": "then", "for example": "e.g.", "that is,": "i.e.,",
}
APPROX_RE = re.compile(r"\bapproximately\s+(?=\d)", re.I)
ABBREV_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in ABBREV), key=len, reverse=True)) + r")(?![\w])", re.I)


def det_compress(ptext: str, level: str) -> str:
    """Compress protected text (code and links are placeholders). Same-meaning edits only."""
    s = ptext
    s = PLEASANTRY_SENTENCES.sub(_delete, s)
    s = PLEASANTRY_START.sub(_delete, s)
    for rx in (DEGREE, RATHER, FILLER, JUST):      # remove filler first, so lead-in phrases match
        s = rx.sub(_delete, s)
    s = LEAD_INS.sub(_delete, s)
    s = PLEASE.sub(_delete, s)
    s = WORDY_RE.sub(lambda m: ste._match_case(m.group(0), WORDY[m.group(0).lower()]), s)
    if level in ("full", "ultra"):
        s = ARTICLE_START.sub(lambda m: MARK if _start(m) else m.group(0), s)   # capital A/The only at sentence start
        s = ARTICLE_MID.sub("", s)
    if level == "ultra":
        s = APPROX_RE.sub("~", s)
        s = ABBREV_RE.sub(lambda m: ste._match_case(m.group(0), ABBREV[m.group(0).lower()]), s)
    # tidy spacing and punctuation left by deletions
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"^\s*[,;:]\s*", "", s)
    s = re.sub(MARK + r"+(\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    s = s.replace(MARK, "").strip()
    return s


# --------------------------------------------------------------------------------------
# LLM compression
# --------------------------------------------------------------------------------------

LEVEL_TEXT = {
    "lite": "Remove filler, hedging that carries no information, pleasantries, and wordy phrases. Keep articles (a, an, the) and keep full sentences.",
    "full": "Also drop articles (a, an, the). Sentence fragments are fine. Prefer short words (use, not utilize).",
    "ultra": "Maximum compression. Use common abbreviations (DB, config, env, auth, repo, dir, docs, info, app), "
             "arrows for cause and flow (A -> B), and = for equivalence. Drop conjunctions when the meaning stays clear.",
}

SYSTEM_PROMPT = """You shorten technical documentation prose so that it uses fewer tokens and keeps every fact.
The style is terse, direct, and plain. This is documentation: do not role-play, do not write "me" or "ugh", no jokes.

Intensity: {level_text}

Always:
- Drop filler (just, really, basically, actually, simply), pleasantries (sure, certainly, of course, happy to), and empty hedging.
- Keep every fact, number, name, condition, and exception. Keep negations (not, never, no, cannot) exactly.
- Keep words that set order or limits: must, must not, never, only, unless, before, after, then, first, finally.
- Keep every placeholder that looks like ⟦0⟧ exactly as written, once each. They stand for code, links, paths, and flags.
- Keep identifiers, product names, commands, and the capitalization of names.
- Keep Markdown emphasis (**bold**, *italic*) on the same words where possible.
- If shortening would make the meaning, or the order of steps, unclear, keep the original sentence.
- Do not merge, split, or reorder blocks.

Reply with one element for each input block, using the same ids, and nothing else:
<b id="1">shortened text</b>"""


def build_prompt(level: str, batch: list) -> str:
    body = "\n".join(f'<b id="{i}">{t}</b>' for i, t in batch)
    return f"<blocks>\n{body}\n</blocks>"


NEG_RE = re.compile(r"\b(?:not|no|never|none|cannot|can't|don't|doesn't|won't|without|nothing|neither|nor|disable[ds]?)\b|n't\b|\u2260", re.I)
IDENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")
ROLEPLAY_RE = re.compile(r"\b(?:ugh|ooga|booga|caveman)\b|\bme (?:think|know|say)\b", re.I)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _identifiers(text: str) -> set:
    out = set()
    for tok in IDENT_RE.findall(re.sub(r"\u27e6\d+\u27e7", " ", text)):
        tok = tok.strip(".:-")
        if len(tok) >= 2 and (any(c.isdigit() for c in tok) or "_" in tok or re.search(r"[a-z][A-Z]", tok)):
            out.add(tok)
    return out


def validate(orig: str, new: str, require_shorter: bool = True) -> Optional[str]:
    """Return a reason to reject `new`, or None."""
    if not new:
        return "empty reply"
    if sorted(re.findall(r"\u27e6\d+\u27e7", orig)) != sorted(re.findall(r"\u27e6\d+\u27e7", new)):
        return "placeholders for code or links were changed"
    o_plain, n_plain = re.sub(r"\u27e6\d+\u27e7", " ", orig), re.sub(r"\u27e6\d+\u27e7", " ", new)
    if require_shorter and ste.count_words(new) >= ste.count_words(orig):
        return "not shorter"
    if any(n not in n_plain for n in NUM_RE.findall(o_plain)):
        return "a number was dropped"
    if NEG_RE.search(o_plain) and not NEG_RE.search(n_plain):
        return "a negation was dropped"
    if any(i not in new for i in _identifiers(orig)):
        return "an identifier was changed"
    if ROLEPLAY_RE.search(new) and not ROLEPLAY_RE.search(orig):
        return "reply role-plays"
    if re.match(r"([-*+]|\d+[.)]|#{1,6}|>)\s", new) and not re.match(r"([-*+]|\d+[.)]|#{1,6}|>)\s", orig):
        return "reply added Markdown structure"
    if "\n" in new:
        return "reply has line breaks"
    return None


# --------------------------------------------------------------------------------------
# Whole-file processing
# --------------------------------------------------------------------------------------

def est_tokens(text: str) -> int:
    """Rough estimate: about four characters per token. Not a real tokenizer."""
    return (len(text) + 3) // 4


def terse_text(text: str, level: str, llm, include_warnings: bool, min_words: int, jobs: int, verbose: bool, plain: bool = False):
    """Return (new_text, stats)."""
    ls = ste.line_starts_of(text)
    cands = []        # (block, original string, protected string, store)
    stats = {"blocks": 0, "changed": 0, "kept": [], "words_before": 0, "words_after": 0}
    for b in ste.parse_markdown(text, plain=plain):
        if b.kind == "heading" or b.off:
            continue
        original = " ".join(t.strip() for _, _, t in b.lines)
        words = len(original.split())
        if words < min_words:
            continue
        line = b.lines[0][0] + 1
        if "\u27e6" in original:
            stats["kept"].append((line, "contains reserved placeholder characters"))
            continue
        if not include_warnings and SAFETY_RE.search(original):
            stats["kept"].append((line, "safety text (use --include-warnings to shorten it)"))
            continue
        ptext, store = protect(original)
        cands.append((b, original, ptext, store))
    stats["blocks"] = len(cands)

    results: dict = {}   # index -> (new protected text or None, reason)
    if llm is None:
        for i, (_, _, ptext, _) in enumerate(cands):
            new = det_compress(ptext, level)
            results[i] = (new, None) if new != ptext else (None, "nothing to shorten")
    else:
        batches, cur, size = [], [], 0
        for i, (_, _, ptext, _) in enumerate(cands):
            if cur and (len(cur) >= 12 or size + len(ptext) > 6000):
                batches.append(cur)
                cur, size = [], 0
            cur.append((i, ptext))
            size += len(ptext)
        if cur:
            batches.append(cur)
        system = SYSTEM_PROMPT.format(level_text=LEVEL_TEXT[level])

        def run(batch):
            try:
                reply = llm(system, build_prompt(level, batch))
            except Exception as e:
                return batch, None, f"LLM call failed: {e}"
            got = {int(i): re.sub(r"\s*\n\s*", " ", t.strip()) for i, t in re.findall(r'<b id="(\d+)">(.*?)</b>', reply, re.S)}
            return batch, got, None

        with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            for batch, got, err in ex.map(run, batches):
                for i, ptext in batch:
                    if got is None:
                        results[i] = (None, err)
                    elif i not in got:
                        results[i] = (None, "no reply for this block")
                    else:
                        why = validate(ptext, got[i])
                        results[i] = (None, why) if why else (got[i], None)

    reps = []
    for i, (b, original, ptext, store) in enumerate(cands):
        new_p, why = results[i]
        line = b.lines[0][0] + 1
        if new_p is None or new_p == ptext:
            stats["kept"].append((line, why or "unchanged"))
            continue
        new = ste.unprotect(new_p, store)
        stats["changed"] += 1
        stats["words_before"] += len(original.split())
        stats["words_after"] += len(new.split())
        if not new.strip():                      # only pleasantries: remove the whole paragraph
            first, last = b.lines[0][0], b.lines[-1][0]
            start = ls[first]
            end = ls[last + 1] if last + 1 < len(ls) else len(text)
            m = re.match(r"\r?\n", text[end:])  # also remove one blank line after it
            reps.append((start, end + (m.end() if m else 0), ""))
            continue
        reps.append(ste.place_block_text(b, new, ls, text))
        if verbose:
            stats["kept"].append((line, f"shortened ({len(original.split())} -> {len(new.split())} words)"))
    new_text, _ = ste.apply_replacements(text, reps)
    return new_text, stats


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def run(args) -> int:
    plan = ste.plan_files(args, "terse")
    if isinstance(plan, int):
        return plan
    src, dest, in_place, fmt = plan
    text = ste.read_text(src)
    if text is None:
        return 2
    level = args.level or os.environ.get("TERSE_LEVEL") or "full"
    if level not in LEVELS:
        print(f"terse: level must be one of {', '.join(LEVELS)} (got '{level}')", file=sys.stderr)
        return 2
    llm, label = ste.pick_llm(args)
    if llm is None:
        if label is None:
            print("terse: no LLM found, so only word-level shortening was applied. For better results, set ANTHROPIC_API_KEY, "
                  "install Claude Code, or pass --cmd.", file=sys.stderr)
        label = "built-in rules (no LLM)"
    print(f"{src.as_posix()}: level {level}, using {label}", file=sys.stderr)
    new, st = terse_text(text, level, llm, args.include_warnings, args.min_words, args.jobs, args.verbose, plain=(fmt == "text"))

    wb, wa = st["words_before"], st["words_after"]
    pct = f" ({(wa - wb) * 100 // wb:+d}%)" if wb else ""
    tb, ta = est_tokens(text), est_tokens(new)
    tpct = f" ({(ta - tb) * 100 // tb:+d}%)" if tb else ""
    print(f"  prose: {wb} -> {wa} words{pct}; file: ~{tb} -> ~{ta} tokens{tpct} (estimate, 4 characters per token)", file=sys.stderr)
    print(f"  shortened {st['changed']} of {st['blocks']} paragraph(s)", file=sys.stderr)
    for line, why in st["kept"]:
        if args.verbose or not why.startswith("shortened"):
            if args.verbose or why not in ("nothing to shorten", "unchanged"):
                print(f"  line {line}: {why}", file=sys.stderr)
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="terse", description="Shrink the prose of a Markdown file to fewer tokens. Code, links, and numbers stay as they are.")
    p.add_argument("input", help="Markdown (.md) or plain text (.txt) file")
    p.add_argument("output", nargs="?", help="Write the result here (default: updated-NAME next to the input)")
    p.add_argument("--in-place", action="store_true", help="Update the input file instead (a .bak copy is kept)")
    p.add_argument("--as", dest="as_format", choices=["markdown", "text"], help="Treat the file as this format whatever its extension")
    p.add_argument("--level", choices=LEVELS, help="lite: no filler, keep articles. full: also drop articles (default, or $TERSE_LEVEL). ultra: abbreviations and arrows.")
    p.add_argument("--no-llm", action="store_true", help="Use the built-in word rules only")
    p.add_argument("--cmd", help="Use this command as the LLM, for example 'claude -p'")
    p.add_argument("--model", help="Model for the Anthropic API (default: claude-sonnet-5-5, or $STE_MODEL)")
    p.add_argument("--include-warnings", action="store_true", help="Also shorten paragraphs that contain safety words (warning, irreversible, ...)")
    p.add_argument("--min-words", type=int, default=6, help="Skip paragraphs shorter than this (default 6)")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--dry-run", action="store_true", help="Show a diff and write nothing")
    p.add_argument("--no-backup", action="store_true", help="With --in-place, do not keep a .bak copy")
    p.add_argument("--force", action="store_true", help="Replace the output file if it exists")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"terse {__version__}")
    return p


def main(argv: Optional[list] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
