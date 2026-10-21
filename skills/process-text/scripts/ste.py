#!/usr/bin/env python3
"""ste - check and fix Markdown files toward ASD-STE100 Simplified Technical English (Issue 9).

Commands
    ste process IN [OUT]       Apply the safe fixes to one file. Writes updated-IN (or OUT); --in-place updates IN.
    ste check   [paths]        Report problems (exit 1 when findings reach --fail-on).
    ste fix     [paths]        Apply safe, mechanical fixes (dry run unless --write).
    ste dict build TXT         Build a local word list from the text of your copy of the standard.
    ste rules                  List the rules this tool checks.

Offline and self-contained: standard library only, Python 3.9+. It makes no network calls, starts no other
programs, and reads no environment variables. The sentence-level rewriting is left to the agent or person who
reads the findings from 'ste check'.

The checks are deterministic heuristics (regular expressions, no part-of-speech tagger).
They find likely problems; they do not prove compliance.
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import fnmatch
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.1.0"

SEVERITIES = {"info": 0, "warning": 1, "error": 2}
SEV_WEIGHT = {"info": 1, "warning": 2, "error": 3}

# rule id -> (summary, default severity, auto-fixable, how it is checked)
RULES = {
    "1.2": ("Use approved words (built-in list; dictionary hints show as info)", "warning", "some", "word list"),
    "1.14": ("Use American English spelling", "warning", "yes", "word list"),
    "3.2": ("No perfect or progressive tenses", "error", "no", "pattern"),
    "3.5": ("Avoid -ing forms (shown with --min-severity info)", "info", "no", "pattern, many exceptions"),
    "3.6": ("Use the active voice", "warning", "no", "pattern, heuristic"),
    "4.2": ("Do not use contractions", "error", "yes", "pattern"),
    "5.1": ("Procedure sentences: maximum 20 words", "error", "no", "word count"),
    "5.2": ("One instruction per sentence", "warning", "no", "pattern, heuristic"),
    "5.3": ("Write instructions in the imperative form", "warning", "no", "pattern, heuristic"),
    "5.4": ("Put the condition before the command", "info", "no", "pattern, heuristic"),
    "6.3": ("Descriptive sentences: maximum 25 words", "error", "no", "word count"),
    "6.6": ("Paragraph: maximum 6 sentences", "error", "no", "sentence count"),
    "8.1": ("Do not use semicolons", "error", "yes", "pattern"),
    "GR-6": ("Do not use Latin abbreviations (e.g., i.e., etc.)", "warning", "some", "pattern"),
    "GR-7": ("Use gender-neutral language", "warning", "no", "pattern"),
}

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", ".mypy_cache", ".ste"}


@dataclass
class Config:
    max_procedural: int = 20
    max_descriptive: int = 25
    max_paragraph_sentences: int = 6
    disable: set = field(default_factory=set)
    allow_words: set = field(default_factory=set)
    names: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    fail_on: str = "error"
    dictionary: Optional[str] = None
    plain: bool = False   # plain-text mode: no Markdown syntax (code blocks, tables, HTML, front matter)
    root: Path = field(default_factory=lambda: Path.cwd())

    def on(self, rule: str) -> bool:
        return rule not in self.disable and rule.split(".")[0] not in self.disable


def load_config(start: Optional[Path] = None) -> Config:
    """Find .ste.toml in `start` or a parent directory."""
    cfg = Config()
    d = (start or Path.cwd()).resolve()
    for p in [d, *d.parents]:
        f = p / ".ste.toml"
        if f.is_file():
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore
                except ImportError:
                    print(f"ste: found {f} but cannot read TOML (need Python 3.11+ or 'tomli'); using defaults", file=sys.stderr)
                    return cfg
            with open(f, "rb") as fh:
                data = tomllib.load(fh).get("ste", {})
            cfg.root = p
            cfg.max_procedural = int(data.get("max_procedural", cfg.max_procedural))
            cfg.max_descriptive = int(data.get("max_descriptive", cfg.max_descriptive))
            cfg.max_paragraph_sentences = int(data.get("max_paragraph_sentences", cfg.max_paragraph_sentences))
            cfg.disable = {str(x) for x in data.get("disable", [])}
            cfg.allow_words = {str(x).lower() for x in data.get("allow_words", [])}
            cfg.names = sorted((str(x) for x in data.get("names", [])), key=len, reverse=True)
            cfg.exclude = [str(x) for x in data.get("exclude", [])]
            cfg.fail_on = str(data.get("fail_on", cfg.fail_on))
            cfg.dictionary = data.get("dictionary")
            break
    return cfg


# --------------------------------------------------------------------------------------
# Markdown parsing: split a file into blocks of prose
# --------------------------------------------------------------------------------------

@dataclass
class Block:
    kind: str                      # paragraph | list_item | heading
    lines: list                    # [(line_index, prefix, text)]
    ordered: bool = False
    mode: Optional[str] = None     # 'procedural' | 'descriptive' | None (auto)
    off: bool = False              # inside an ste-disable region

    @property
    def text(self) -> str:
        return "\n".join(t for _, _, t in self.lines)


QUOTE_RE = re.compile(r"^(\s{0,3}(?:>[ ]?)+)(.*)$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])(\s+)(\S.*)$")
TASK_RE = re.compile(r"^\[[ xX]\]\s+")
REFDEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
DIRECTIVE_RE = re.compile(r"^\s*<!--\s*(?:ste|terse)(?:-|:\s*)(disable|enable|procedural|descriptive|auto)\s*-->\s*$")
HTML_BLOCK_RE = re.compile(
    r"^\s{0,3}(?:</?(?:div|p|table|thead|tbody|tr|td|th|details|summary|pre|ul|ol|li|section|aside|figure|picture|"
    r"source|video|iframe|center|hr|br|h[1-6]|blockquote|nav|header|footer|img|a)\b[^>]*>|(?:<[^>]+>\s*)+$)",
    re.I,
)


def parse_markdown(text: str, plain: bool = False) -> list:
    """Return prose blocks. Skips front matter, code, HTML, tables, and reference definitions.

    plain=True is for .txt files: only paragraphs, lists, and headings are recognized, so an indented
    paragraph or a line with a | is still prose.
    """
    lines = text.split("\n")
    n = len(lines)
    blocks: list = []
    cur: Optional[Block] = None
    in_fence = None
    in_list = False
    off = False
    mode: Optional[str] = None
    i = 0

    def flush():
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
            cur = None

    if n and not plain and lines[0].strip() == "---":
        for j in range(1, n):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break

    while i < n:
        raw = lines[i].rstrip("\r")
        qm = QUOTE_RE.match(raw)
        qpre, body = (qm.group(1), qm.group(2)) if qm else ("", raw)
        fm = None if plain else FENCE_RE.match(body)

        if in_fence:
            if fm and fm.group(1)[0] == in_fence[0] and len(fm.group(1)) >= in_fence[1] and body.strip() == fm.group(1):
                in_fence = None
            i += 1
            continue
        if fm:
            flush()
            in_fence = (fm.group(1)[0], len(fm.group(1)))
            i += 1
            continue
        if not body.strip():
            flush()
            i += 1
            continue

        d = DIRECTIVE_RE.match(body)
        if d:
            flush()
            act = d.group(1)
            if act == "disable":
                off = True
            elif act == "enable":
                off = False
            elif act == "auto":
                mode = None
            else:
                mode = act
            i += 1
            continue
        if body.lstrip().startswith("<!--"):
            flush()
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            i = j + 1
            continue
        if not plain and HTML_BLOCK_RE.match(body):
            flush()
            i += 1
            while i < n and lines[i].strip():
                i += 1
            continue
        if HEADING_RE.match(body):
            flush()
            blocks.append(Block("heading", [(i, qpre, body)], off=off, mode=mode))
            in_list = False
            i += 1
            continue
        if cur is not None and cur.kind == "paragraph" and SETEXT_RE.match(body):
            cur.kind = "heading"
            flush()
            i += 1
            continue
        if HR_RE.match(body):
            flush()
            i += 1
            continue
        if cur is None and not plain and REFDEF_RE.match(body):
            i += 1
            continue
        if cur is None and not plain and "|" in body and i + 1 < n:
            nxt = QUOTE_RE.match(lines[i + 1])
            nxt_body = nxt.group(2) if nxt else lines[i + 1]
            if "|" in nxt_body and TABLE_SEP_RE.match(nxt_body):
                while i < n and lines[i].strip() and "|" in lines[i]:
                    i += 1
                continue

        lm = LIST_RE.match(body)
        if lm:
            flush()
            in_list = True
            content = lm.group(4)
            prefix = qpre + body[: len(body) - len(content)]
            tm = TASK_RE.match(content)
            if tm:
                prefix += tm.group(0)
                content = content[tm.end():]
            cur = Block("list_item", [(i, prefix, content)], ordered=lm.group(2)[0].isdigit(), off=off, mode=mode)
            i += 1
            continue

        expanded = body.replace("\t", "    ")
        indent = len(expanded) - len(expanded.lstrip(" "))
        lead = body[: len(body) - len(body.lstrip(" \t"))]
        if cur is None:
            if not plain and not in_list and indent >= 4:
                i += 1  # indented code block
                continue
            if indent == 0:
                in_list = False
            cur = Block("paragraph", [(i, qpre + lead, body[len(lead):])], off=off, mode=mode)
        else:
            cur.lines.append((i, qpre + lead, body[len(lead):]))
        i += 1

    flush()
    return blocks


# --------------------------------------------------------------------------------------
# Masking, sentence splitting, word counting (Rules 8.5 to 8.7)
# --------------------------------------------------------------------------------------

CODE = "\x01"
INLINE_CODE_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


def _blank(m) -> str:
    return " " * len(m.group(0))


def _code(m) -> str:
    return CODE * len(m.group(0))


def mask(text: str) -> str:
    """Hide markup. The result has the same length as the input, so offsets still line up.

    Inline code and URLs become one block of CODE characters (they count as one word).
    Link targets, images, HTML tags, and emphasis marks become spaces.
    """
    s = INLINE_CODE_RE.sub(_code, text)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", _blank, s)
    s = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", lambda m: " " + m.group(1) + " " * (len(m.group(2)) + 3), s)
    s = re.sub(r"\[([^\]]+)\]\[([^\]]*)\]", lambda m: " " + m.group(1) + " " * (len(m.group(2)) + 3), s)
    s = re.sub(r"\[\^[^\]]+\]", _blank, s)
    s = re.sub(r"<(?:https?://[^>\s]+|[\w.+-]+@[\w-]+\.[\w.]+)>", _code, s)
    s = re.sub(r"https?://[^\s)>\]]+", _code, s)
    s = re.sub(r"</?[A-Za-z][^>\n]*>", _blank, s)
    s = re.sub(r"&#?\w+;", _blank, s)
    s = re.sub(r"\*\*|__|~~|\*", _blank, s)
    s = re.sub(r"(?<![\w])_(?=\S)|(?<=\S)_(?![\w])", " ", s)
    return s


ABBREV = {"e.g", "i.e", "vs", "cf", "viz", "al", "approx", "no", "fig", "dr", "mr", "mrs", "ms", "inc", "ltd", "st", "jr", "sr"}
SENT_END_RE = re.compile(r"[.!?]+[\"')\]”’]*(?=\s+[\"“(\[\x01A-Z])")


def split_sentences(s: str) -> list:
    """Return (start, end) spans of sentences in masked text."""
    spans = []
    start = 0
    for m in SENT_END_RE.finditer(s):
        punct = m.group(0)
        if punct.startswith(".") and not punct.startswith(".."):
            before = re.search(r"(\S+)$", s[start:m.start()])
            tok = before.group(1).lower().lstrip("([\"'") if before else ""
            if tok in ABBREV or (len(tok) == 1 and tok.isalpha()) or re.fullmatch(r"[a-z](\.[a-z])+", tok):
                continue
        end = m.end()
        spans.append((start, end))
        start = end
    spans.append((start, len(s)))
    out = []
    for a, b in spans:
        seg = s[a:b]
        lead = len(seg) - len(seg.lstrip())
        seg = seg.strip()
        if seg and any(c.isalnum() or c == CODE for c in seg):
            out.append((a + lead, a + lead + len(seg)))
    return out


_UNIT = (
    r"(?:%|°[CF]?|[kKMGT]?i?B|[kMG]?Hz|[kMG]?bps|[mkunμ]?(?:s|g|V|W)|[mkμ]A|[mnμ]F|[mμ]H|px|pt|rem|em|min|sec|kg|lbs?|ft|mph|rpm|dB|Nm"
    r"|seconds?|minutes?|hours?|days?|milliseconds?|bytes?|kilobytes?|megabytes?|gigabytes?|terabytes?|percent|pixels?|degrees|ohms?|volts?|watts?"
    r"|meters?|millimeters?|kilograms?|pounds?|inches|feet)"
)
TOKEN_RE = re.compile(
    r"\([^()]*\)"                                   # text in parentheses = 1 word (8.5)
    r"|“[^”]*”|\"[^\"]*\""           # quoted text = 1 word (8.6.5)
    r"|\x01+"                                       # code or URL = 1 word
    r"|\bNo\.\s?\d+\b|\d{1,2}(?::\d\d)?\s?[ap]\.m\."  # "No. 1" and "10 a.m." = 1 word (8.6.3, 8.6.4)
    r"|\d[\d.,]*(?:[ ]?" + _UNIT + r"(?![A-Za-z]))"  # number with unit = 1 word (8.6.2)
    r"|\S+"                                         # everything else, hyphenated words included (8.7)
)


def count_words(sent: str) -> int:
    return sum(1 for t in TOKEN_RE.findall(sent) if any(c.isalnum() or c == CODE for c in t))


# --------------------------------------------------------------------------------------
# Word lists used by the checks
# --------------------------------------------------------------------------------------

IMPERATIVE_VERBS = set(
    """add apply ask assemble attach avoid build call change check choose clean clear click clone close commit compare
    configure confirm connect copy create define delete deploy disable download edit enable ensure enter execute export
    fill find follow generate get give go grant import include initialize insert install keep launch load locate make
    mark merge move navigate open paste press provide pull push put read record refer reload remove rename replace reset
    restart restore return review run save scroll search select send set specify start stop store submit take tap test
    toggle try turn type uninstall unzip update upgrade upload use verify view wait write""".split()
)
NOT_IMPERATIVE_NEXT = {
    "is", "are", "was", "were", "will", "can", "has", "have", "had", "does", "did", "should", "may", "might", "must",
    "means", "returns", "contains", "provides", "allows", "lets", "makes", "takes", "gives", "needs", "becomes", "shows",
}

APPROVED_ING = {"lighting", "opening", "routing", "servicing", "mating", "missing", "remaining", "during", "something"}
NOT_VERB_ING = {
    "thing", "things", "something", "nothing", "anything", "everything", "string", "strings", "ring", "spring", "king",
    "bring", "during", "morning", "evening", "ceiling", "sibling", "swing", "wing", "sing", "sting", "fling", "cling",
    "sling", "ping", "ding", "bling", "viking", "pudding", "wedding", "sterling", "darling", "inning", "bing", "zing",
}
NOT_PARTICIPLE_ED = {
    "need", "feed", "seed", "speed", "bed", "red", "shed", "embed", "proceed", "succeed", "exceed", "bleed", "breed",
    "creed", "deed", "greed", "heed", "indeed", "steed", "weed", "sped", "hundred", "kindred", "sacred", "naked", "wicked",
}
IRREGULAR_PARTICIPLES = (
    "been taken given written chosen driven eaten fallen forgotten gotten hidden risen shown known seen grown drawn broken "
    "spoken stolen woken worn torn born beaten bitten proven thrown flown frozen forbidden forgiven mistaken "
    "done made got gone run become come built sent set put held kept left found lost read met paid said told thought "
    "brought caught felt heard hit led meant spent stood won"
).split()
_PART = r"(?:" + "|".join(IRREGULAR_PARTICIPLES) + r"|[a-z]{2,}ed)"
_ADV = r"(?:(?:not|never|already|just|also|since|still|only|always|now|currently|then|first|later|previously|usually|typically|automatically|manually|explicitly|directly|simply)\s+)*"

PERFECT_RE = re.compile(r"\b(have|has|had)\s+" + _ADV + r"(" + _PART + r")(?![\w-])", re.I)
PROGRESSIVE_RE = re.compile(r"\b(am|is|are|was|were|be|been|being)\s+" + _ADV + r"([a-z]{2,}ing)(?![\w-])", re.I)
PASSIVE_RE = re.compile(
    r"\b(is|are|was|were|be|been|being|gets?|got)\s+" + _ADV + r"(" + _PART + r")(?![\w-])(\s+by\b)?", re.I
)

CONTRACTION_RE = re.compile(
    r"\b(?:[A-Za-z]+n[’']t|[A-Za-z]+[’'](?:ll|re|ve|d|m)|(?:it|that|there|here|what|who|he|she|let|how|where|when|why)[’']s)\b",
    re.I,
)
CONTRACTION_MAP = {
    "can't": "cannot", "won't": "will not", "shan't": "shall not", "ain't": None,
    "it's": "it is", "that's": "that is", "there's": "there is", "here's": "here is", "what's": "what is",
    "who's": "who is", "he's": "he is", "she's": "she is", "let's": "let us", "how's": "how is",
    "where's": "where is", "when's": "when is", "why's": "why is",
    "i'm": "I am",
}
for _stem, _full in {"you": "you", "we": "we", "they": "they", "it": "it", "i": "I", "he": "he", "she": "she", "that": "that", "there": "there"}.items():
    CONTRACTION_MAP[f"{_stem}'ll"] = f"{_full} will"
    CONTRACTION_MAP[f"{_stem}'re"] = f"{_full} are"
    CONTRACTION_MAP[f"{_stem}'ve"] = f"{_full} have"

# A verb entry in the dictionary is probably a noun here when one of these words comes before it.
NOUN_CONTEXT = {"the", "a", "an", "this", "that", "these", "those", "each", "every", "your", "its", "their", "our", "my",
                "his", "her", "of", "no", "any", "some", "all", "new", "same", "other", "such", "which", "one", "two"}
MODAL_ADVICE = {
    "should": "MUST for a requirement, or IF for a condition",
    "shall": "MUST",
    "may": "CAN",
    "might": "CAN",
    "would": "CAN",
    "could": "CAN",
}
REPLACE_WORDS = {
    "utilize": "use", "utilizes": "uses", "utilized": "used", "utilizing": "using",
    "utilise": "use", "utilises": "uses", "utilised": "used", "utilising": "using",
    "commence": "start", "commences": "starts", "commenced": "started", "commencing": "starting",
    "ensure": "make sure", "ensures": "makes sure", "ensured": "made sure", "ensuring": "making sure",
    "prior to": "before",
}
REPLACE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in REPLACE_WORDS), key=len, reverse=True)) + r")\b", re.I)
LATIN_RE = re.compile(r"(?<![\w.])(e\.g\.|i\.e\.|etc\.|viz\.|cf\.|et al\.)", re.I)
LATIN_FIX = {"e.g.": "for example", "i.e.": "that is"}
GENDER_RE = re.compile(r"\b(he|she|him|his|hers|her|himself|herself)\b", re.I)

# (British stem, American stem, suffix alternatives)
BRITISH = [
    ("colour", "color", "s|ed|ing|ful|less"), ("behaviour", "behavior", "s|al"), ("favour", "favor", "s|ed|ing|ite|ites"),
    ("honour", "honor", "s|ed|ing|able"), ("neighbour", "neighbor", "s|ed|ing|hood|hoods"), ("flavour", "flavor", "s|ed|ing"),
    ("labour", "labor", "s|ed"), ("centre", "center", "s|d"), ("metre", "meter", "s"), ("litre", "liter", "s"),
    ("theatre", "theater", "s"), ("catalogue", "catalog", "s|d"), ("licence", "license", "s|d"), ("grey", "gray", "s|ed|ing|er"),
    ("defence", "defense", "s"), ("offence", "offense", "s"), ("programme", "program", "s"), ("judgement", "judgment", "s"),
    ("organis", "organiz", "e|es|ed|ing|ation|ations|er|ers"), ("recognis", "recogniz", "e|es|ed|ing"),
    ("initialis", "initializ", "e|es|ed|ing|ation"), ("serialis", "serializ", "e|es|ed|ing|ation"),
    ("optimis", "optimiz", "e|es|ed|ing|ation"), ("customis", "customiz", "e|es|ed|ing|ation"),
    ("normalis", "normaliz", "e|es|ed|ing|ation"), ("synchronis", "synchroniz", "e|es|ed|ing|ation"),
    ("authoris", "authoriz", "e|es|ed|ing|ation"), ("minimis", "minimiz", "e|es|ed|ing"), ("maximis", "maximiz", "e|es|ed|ing"),
    ("summaris", "summariz", "e|es|ed|ing"), ("visualis", "visualiz", "e|es|ed|ing|ation"), ("analys", "analyz", "e|es|ed|ing"),
    ("cancell", "cancel", "ed|ing"), ("modell", "model", "ed|ing"), ("labell", "label", "ed|ing"), ("travell", "travel", "ed|ing|er"),
    ("whilst", "while", ""), ("amongst", "among", ""), ("learnt", "learned", ""),
]
BRITISH_RES = [(re.compile(r"\b" + b + r"((?:" + suf + r")?)\b" if suf else r"\b" + b + r"()\b", re.I), a) for b, a, suf in BRITISH]


def _match_case(orig: str, repl: str) -> str:
    if len(orig) > 1 and orig.isupper():
        return repl.upper()
    if orig[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


# --------------------------------------------------------------------------------------
# Dictionary (built from the user's copy of the standard)
# --------------------------------------------------------------------------------------

POS = r"(?:n|v|adj|adv|pron|art|prep|conj)"

# Technical verbs for computer processes and applications (Rule 1.12, category 2). They are allowed
# even when the dictionary does not list them as approved.
COMPUTER_VERBS = set(
    """click digitize enter press print swipe tap type clear close copy cut delete deselect disable drag encrypt erase
    filter highlight invalidate maximize minimize navigate open paste save scroll sort store validate zoom enable abort boot
    communicate debug download format install load manage process reboot update upgrade upload""".split()
)


def parse_dictionary(text: str) -> dict:
    """Parse the word list from the text of ASD-STE100 (the output of pdftotext -layout)."""
    lines = text.split("\n")
    start = None
    for i, l in enumerate(lines):
        if re.match(r"^A \(art\)\s+Function word", l):
            start = i  # keep the last match: the real word list follows the introduction
    if start is None:
        raise ValueError("Could not find the dictionary word list in this text.")
    junk = re.compile(
        r"Part 2 - Dictionary|^\s*Issue \d|ASD[- ]STE100 Simplified|^\s*20\d\d-\d\d-\d\d\s*$|Blank Page|^Word\s+Approved|^\(part of speech\)\s+ALTERNATIVES"
    )
    head_re = re.compile(r"^ {0,2}(\S.*?)\s+\((" + POS + r")\)[,]?(?:\s{2,}(\S.*))?\s*$")
    wrap_re = re.compile(r"^ {0,2}([A-Za-z][A-Za-z'’ /\-]*?)\s{2,}(\S.*)$")
    alt_re = re.compile(r"^\s{8,34}([A-Z][A-Z0-9'’/\-]*(?: [A-Z0-9'’/\-]+)*(?: \((?:" + POS + r"|TN|TV)\))?)(?:\s{2,}|$)")
    non: dict = {}
    approved: set = set()
    cur = None
    skip_next = False
    hyph_re = re.compile(r"^ {0,2}([A-Za-z]+-)\s{2,}(\S.*)$")
    for idx in range(start, len(lines)):
        l = lines[idx].replace("\f", "")
        if skip_next:
            skip_next = False
            continue
        if not l.strip() or junk.search(l):
            continue
        word = pos = rest = None
        hm = hyph_re.match(l)
        nxt = re.match(r"^ {0,2}([A-Za-z]+)\s+\((" + POS + r")\)", lines[idx + 1]) if idx + 1 < len(lines) else None
        if hm and nxt:
            word, pos, rest = hm.group(1)[:-1] + nxt.group(1), nxt.group(2), hm.group(2)
            skip_next = True
        m = None if word else head_re.match(l)
        if m:
            word, pos, rest = m.group(1), m.group(2), m.group(3) or ""
        else:
            w = wrap_re.match(l)
            if w and idx + 1 < len(lines):
                pm = re.match(r"^ {0,2}\((" + POS + r")\)", lines[idx + 1])
                if pm:
                    word, pos, rest = w.group(1), pm.group(1), w.group(2)
        if word is not None:
            if word.isupper() or word[0].isupper():
                approved.add(word.lower().strip(", "))
                cur = None
                continue
            cur = non.setdefault(word.lower(), {"pos": pos, "alts": []})
            first = re.split(r"\s{2,}", rest.strip())[0] if rest.strip() else ""
            if first and re.match(r"^[A-Z]", first):
                cur["alts"].append(first)
            continue
        if cur is not None:
            m3 = alt_re.match(l)
            if m3 and m3.group(1) not in cur["alts"] and len(cur["alts"]) < 6:
                cur["alts"].append(m3.group(1))
    return {"issue": "9", "approved": sorted(approved), "non_approved": non}


def build_dictionary_from_file(path: str) -> dict:
    """Build the word list from the text of ASD-STE100. This tool never runs other programs,
    so convert the PDF yourself first: pdftotext -layout ASD-STE100_ISSUE9.pdf ASD-STE100_ISSUE9.txt"""
    if not path.lower().endswith(".txt"):
        raise SystemExit("ste: give a .txt file. Convert the PDF first with: pdftotext -layout ASD-STE100_ISSUE9.pdf ASD-STE100_ISSUE9.txt")
    text = Path(path).read_text(encoding="utf-8")
    data = parse_dictionary(text)
    data["source"] = Path(path).name
    return data


class Dictionary:
    """Look up non-approved words (and simple inflections) from the built word list."""

    def __init__(self, data: dict):
        self.approved = set(data.get("approved", []))
        self.forms: dict = {}
        self.phrases: dict = {}
        for word, info in data.get("non_approved", {}).items():
            pos = info.get("pos", "")
            alts = info.get("alts", [])
            if " " in word:
                self.phrases[word] = (word, pos, alts)
                continue
            if word in self.approved or word in COMPUTER_VERBS:
                continue
            for f in self._forms(word, pos):
                if f not in self.approved:
                    self.forms.setdefault(f, (word, pos, alts))

    @staticmethod
    def _forms(w: str, pos: str) -> set:
        out = {w}
        if pos == "v":
            out |= {w + "s", w + "es"} if w.endswith(("s", "x", "z", "ch", "sh")) else {w + "s"}
            if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
                out |= {w[:-1] + "ies", w[:-1] + "ied"}
            if w.endswith("e"):
                out |= {w + "d", w[:-1] + "ing"}
            else:
                out |= {w + "ed", w + "ing", w + w[-1] + "ed", w + w[-1] + "ing"}
        elif pos == "n":
            out |= {w + "s", w + "es"}
            if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
                out.add(w[:-1] + "ies")
        return out

    @classmethod
    def find(cls, explicit: Optional[str], root: Path) -> Optional["Dictionary"]:
        candidates = []
        if explicit:
            candidates.append(Path(explicit) if Path(explicit).is_absolute() else root / explicit)
        candidates += [root / ".ste" / "dictionary.json", Path.cwd() / ".ste" / "dictionary.json",
                       Path.home() / ".config" / "ste" / "dictionary.json"]
        for c in candidates:
            if c.is_file():
                try:
                    return cls(json.loads(c.read_text(encoding="utf-8")))
                except (OSError, ValueError) as e:
                    print(f"ste: cannot read dictionary {c}: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    start: int
    end: int
    replacement: Optional[str] = None
    path: str = ""
    line: int = 0
    col: int = 0


COND_LEAD_RE = re.compile(r"^(?:if|when|once|unless|after|before|while|where|for)\b[^,\x01]{0,160}?,\s*", re.I)
LEAD_ADVERB_RE = re.compile(r"^(?:then|next|finally|first|second|third|now|also|optionally|alternatively|otherwise|please),?\s+", re.I)


def is_imperative(sent: str) -> bool:
    t = sent.strip()
    t = LEAD_ADVERB_RE.sub("", t)
    t = COND_LEAD_RE.sub("", t, count=1)
    t = LEAD_ADVERB_RE.sub("", t)
    toks = re.findall(r"[A-Za-z'’]+", t[:90])
    if not toks:
        return False
    w = toks[0].lower()
    if w in ("don't", "don’t"):
        return True
    if w == "do" and len(toks) > 1 and toks[1].lower() == "not":
        return True
    if w in IMPERATIVE_VERBS:
        return not any(x.lower() in NOT_IMPERATIVE_NEXT for x in toks[1:3])
    return False


def analyze_block(block: Block, cfg: Config, dic: Optional[Dictionary] = None) -> list:
    """Findings for one block. Offsets are relative to block.text."""
    if block.kind == "heading" or block.off:
        return []
    raw = block.text
    s = mask(raw)
    for nm in cfg.names:  # proper nouns count as one word (Rule 8.6.7)
        s = re.sub(r"(?<!\w)" + re.escape(nm) + r"(?!\w)", lambda m: CODE * len(m.group(0)), s)
    sents = split_sentences(s)
    out: list = []
    if cfg.on("6.6") and len(sents) > cfg.max_paragraph_sentences:
        a = sents[0][0]
        out.append(Finding("6.6", "error", f"Paragraph has {len(sents)} sentences; the maximum is {cfg.max_paragraph_sentences}. Split it into shorter paragraphs, or use a list.", a, sents[-1][1]))
    for a, b in sents:
        out.extend(_analyze_sentence(s[a:b], a, block, cfg, dic))
    # remove duplicates (same rule and position), then sort
    seen = set()
    uniq = []
    for f in sorted(out, key=lambda f: (f.start, f.rule)):
        k = (f.rule, f.start)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def _analyze_sentence(sent: str, base: int, block: Block, cfg: Config, dic: Optional[Dictionary]) -> list:
    F: list = []
    on = cfg.on
    allow = cfg.allow_words

    def add(rule, sev, msg, a, b, repl=None):
        if on(rule):
            F.append(Finding(rule, sev, msg, base + a, base + b, repl))

    imperative = is_imperative(sent)
    if block.mode == "procedural":
        proc = True
    elif block.mode == "descriptive":
        proc = False
    else:
        proc = (block.kind == "list_item" and block.ordered) or imperative
    kind = "procedural" if proc else "descriptive"

    # 5.1 / 6.3 sentence length
    n = count_words(sent)
    limit = cfg.max_procedural if proc else cfg.max_descriptive
    if n > limit:
        add("5.1" if proc else "6.3", "error",
            f"Sentence has {n} words; the maximum is {limit} in {kind} text. Split it: one idea, or one instruction, in each sentence.",
            0, len(sent))

    # 8.1 semicolon
    for m in re.finditer(r";", sent):
        tail = re.match(r"([ \t]+)([a-z]?)", sent[m.end():])
        if tail and tail.group(2):
            add("8.1", "error", "Do not use semicolons. Write two sentences.", m.start(), m.end() + len(tail.group(0)),
                ". " + tail.group(2).upper())
        elif tail:
            add("8.1", "error", "Do not use semicolons. Write two sentences.", m.start(), m.end() + len(tail.group(1)), ". ")
        else:
            add("8.1", "error", "Do not use semicolons. Write two sentences.", m.start(), m.end(), ".")

    # 4.2 contractions
    for m in CONTRACTION_RE.finditer(sent):
        orig = m.group(0)
        key = orig.lower().replace("’", "'")
        full = CONTRACTION_MAP.get(key, "MISSING")
        if full == "MISSING":
            if key.endswith("n't"):
                full = key[:-3] + " not"
            else:
                full = None
        repl = _match_case(orig, full) if full else None
        if repl and key == "i'm":
            repl = "I am"
        add("4.2", "error", f"Do not use contractions: write '{repl}' in full." if repl else f"Do not use the contraction '{orig}'. Write all the words.",
            m.start(), m.end(), repl)

    # 3.2 tenses
    prog_spans = set()
    for m in PERFECT_RE.finditer(sent):
        part = m.group(2).lower()
        if part in NOT_PARTICIPLE_ED:
            continue
        add("3.2", "error", f"'{m.group(0)}' is a perfect tense. Use the simple past or the simple present.", m.start(), m.end())
    for m in PROGRESSIVE_RE.finditer(sent):
        ing = m.group(2).lower()
        if ing in NOT_VERB_ING or ing in APPROVED_ING:
            continue
        prog_spans.add(m.start(2))
        add("3.2", "error", f"'{m.group(0)}' is a progressive tense. Use the simple present or the simple past.", m.start(), m.end())

    # 3.6 passive voice
    for m in PASSIVE_RE.finditer(sent):
        part = m.group(2).lower()
        if part in NOT_PARTICIPLE_ED or part == "been" or m.start(2) in prog_spans:
            continue
        if re.match(r"(?:have|has|had)\s+$", sent[max(0, m.start() - 5):m.start()], re.I):
            continue
        if m.group(3):
            add("3.6", "warning", f"Passive voice with an agent ('{m.group(0).strip()}'). Make the agent the subject of an active sentence.", m.start(), m.end())
        elif proc:
            add("3.6", "warning", f"Possible passive voice in an instruction ('{m.group(0)}'). Use the active voice.", m.start(), m.end())
        else:
            add("3.6", "info", f"Possible passive voice ('{m.group(0)}'). It is allowed only when the agent is unknown (Rule 3.6). "
                               "A past participle that describes a condition is allowed (Rule 3.3).", m.start(), m.end())

    # 3.5 -ing forms
    for m in re.finditer(r"\b[A-Za-z]{3,}ing\b", sent):
        w = m.group(0)
        lw = w.lower()
        if lw in NOT_VERB_ING or lw in APPROVED_ING or lw in allow or m.start() in prog_spans:
            continue
        if (m.start() > 0 and sent[m.start() - 1] == "-") or (m.end() < len(sent) and sent[m.end()] == "-"):
            continue
        if w[0].isupper() and m.start() > 0:
            continue  # probably a proper noun
        add("3.5", "info", f"Avoid the -ing form '{w}'. Use it only as a technical noun or as a modifier in a technical noun. "
                              "Add technical terms to allow_words in .ste.toml.", m.start(), m.end())

    # 1.2 vocabulary: built-in replacements, modal verbs, then the dictionary
    reported = set()
    for m in REPLACE_RE.finditer(sent):
        w = m.group(1)
        if w.lower() in allow:
            continue
        repl = _match_case(w, REPLACE_WORDS[w.lower()])
        reported.add(m.start())
        add("1.2", "warning", f"'{w}' is not an approved word. Use '{repl}'.", m.start(), m.end(), repl)
    for m in re.finditer(r"\b(should|shall|may|might|would|could)\b", sent, re.I):
        w = m.group(1)
        if w == "May" and re.match(r"\s+\d", sent[m.end():]):
            continue
        if w.lower() in allow:
            continue
        reported.add(m.start())
        add("1.2", "warning", f"'{w}' is not an approved verb. Use {MODAL_ADVICE[w.lower()]}.", m.start(), m.end())
    if dic is not None and on("1.2"):
        toks = [(t.start(), t.end(), t.group(0).lower().replace("’", "'")) for t in re.finditer(r"[A-Za-z][A-Za-z'’\-]*", sent)]
        i = 0
        while i < len(toks):
            hit = None
            for span in (4, 3, 2):
                if i + span <= len(toks):
                    chunk = toks[i:i + span]
                    if all(sent[chunk[k][1]:chunk[k + 1][0]] == " " for k in range(span - 1)):
                        phrase = " ".join(c[2] for c in chunk)
                        if phrase in dic.phrases:
                            hit = (chunk[0][0], chunk[-1][1], dic.phrases[phrase], span)
                            break
            if hit is None:
                a, b, w = toks[i]
                if w in dic.forms and w not in allow and a not in reported:
                    hit = (a, b, dic.forms[w], 1)
            if hit:
                a, b, (word, pos, alts), span = hit
                prev = toks[i - 1][2] if i > 0 else ""
                noun_use = pos == "v" and (prev in NOUN_CONTEXT or sent[b:b + 1] == ":")
                if not noun_use:
                    alt_txt = ", ".join(x.lower() for x in alts[:3]) or "see the dictionary"
                    add("1.2", "info", f"'{sent[a:b]}' is not in the approved dictionary ({pos}). Dictionary alternatives: {alt_txt}. "
                                       "Technical nouns and verbs are allowed (Rules 1.5 and 1.12).", a, b)
                i += span
            else:
                i += 1

    # 1.14 spelling
    for rx, american in BRITISH_RES:
        for m in rx.finditer(sent):
            if m.group(0).lower() in allow:
                continue
            repl = _match_case(m.group(0), american + m.group(1))
            add("1.14", "warning", f"Use American English spelling: '{repl}'.", m.start(), m.end(), repl)

    # GR-6 Latin abbreviations, GR-7 gendered pronouns
    for m in LATIN_RE.finditer(sent):
        key = m.group(1).lower()
        add("GR-6", "warning", f"Do not use the Latin abbreviation '{m.group(1)}'. Write it in English words.", m.start(), m.end(),
            LATIN_FIX.get(key))
    for m in GENDER_RE.finditer(sent):
        if m.group(1).lower() in allow:
            continue
        add("GR-7", "warning", f"Use gender-neutral language instead of '{m.group(1)}'.", m.start(), m.end())

    # Procedure rules
    if proc:
        for m in re.finditer(r"\b(?:and|then)\s+(?:then\s+)?([a-z]+)\b", sent):
            if m.group(1) in IMPERATIVE_VERBS:
                add("5.2", "warning", "Two instructions in one sentence. Write one instruction in each sentence, "
                                      "unless the two actions occur at the same time.", m.start(), m.end())
        if imperative and not COND_LEAD_RE.match(sent.strip()):
            m = re.search(r"\s(if|when|unless|once)\s", sent)
            if m and m.start() > 6:
                add("5.4", "info", f"Put the condition ('{m.group(1)} ...') first, then a comma, then the command.", m.start() + 1, len(sent))
    m = re.search(r"\b(?:you|we)\s+(?:will\s+)?(?:need to|have to|want to|can then)\b", sent, re.I)
    if m:
        add("5.3", "warning", "Write the instruction in the imperative form (for example, 'Run the test.').", m.start(), m.end())
    if block.kind == "list_item" and block.ordered and not imperative:
        m = re.match(r"(?:then\s+)?(?:you|we)\b", sent, re.I)
        if m:
            add("5.3", "warning", "A step must be an instruction. Use the imperative form.", m.start(), m.end())
    return F


# --------------------------------------------------------------------------------------
# Whole-file helpers
# --------------------------------------------------------------------------------------

def line_starts_of(text: str) -> list:
    starts = [0]
    for m in re.finditer(r"\n", text):
        starts.append(m.end())
    return starts


def block_segments(block: Block, line_starts: list) -> list:
    segs = []
    k = 0
    for li, prefix, t in block.lines:
        segs.append((k, line_starts[li] + len(prefix), len(t)))
        k += len(t) + 1
    return segs


def to_abs(segs: list, k: int) -> int:
    idx = bisect.bisect_right([s[0] for s in segs], k) - 1
    k0, a0, _ = segs[max(idx, 0)]
    return a0 + (k - k0)


def lint_detailed(text: str, cfg: Config, dic: Optional[Dictionary], path: str = ""):
    """Return (list of (block, findings with file offsets), line_starts)."""
    ls = line_starts_of(text)
    result = []
    for b in parse_markdown(text, plain=cfg.plain):
        segs = block_segments(b, ls)
        fs = analyze_block(b, cfg, dic)
        for f in fs:
            f.start, f.end = to_abs(segs, f.start), to_abs(segs, max(f.end - 1, f.start)) + 1
            li = bisect.bisect_right(ls, f.start) - 1
            f.path, f.line, f.col = path, li + 1, f.start - ls[li] + 1
        if fs:
            result.append((b, fs))
    return result, ls


def lint_text(text: str, cfg: Config, dic: Optional[Dictionary], path: str = "") -> list:
    res, _ = lint_detailed(text, cfg, dic, path)
    return sorted((f for _, fs in res for f in fs), key=lambda f: f.start)


def apply_replacements(text: str, reps: list) -> tuple:
    """reps: [(start, end, replacement)]. Returns (new_text, applied_count). Overlaps are skipped."""
    out = text
    applied = 0
    limit = len(text) + 1
    for a, b, r in sorted(reps, key=lambda x: (x[0], x[1]), reverse=True):
        if b > limit:
            continue
        out = out[:a] + r + out[b:]
        limit = a
        applied += 1
    return out, applied


def apply_safe_fixes(text: str, cfg: Config, dic: Optional[Dictionary]) -> tuple:
    """Apply every mechanical fix. Fixes that touch the same words wait for the next pass."""
    total = 0
    for _ in range(4):
        fs = lint_text(text, cfg, dic)
        reps = [(f.start, f.end, f.replacement) for f in fs if f.replacement is not None]
        text, n = apply_replacements(text, reps)
        total += n
        if n == 0:
            break
    return text, total


# --------------------------------------------------------------------------------------
# Protected spans (code, links, URLs stay unchanged)
# --------------------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)"
    r"|(`+)(?!`).+?(?<!`)\1(?!`)"
    r"|\]\([^)]*\)|\]\[[^\]]*\]"
    r"|\[\^[^\]]+\]"
    r"|<(?:https?://[^>\s]+|[\w.+-]+@[\w-]+\.[\w.]+)>"
    r"|https?://[^\s)>\]]+"
    r"|</?[A-Za-z][^>\n]*>"
    r"|&#?\w+;",
    re.S,
)


def protect(text: str) -> tuple:
    store: list = []

    def sub(m):
        store.append(m.group(0))
        return f"⟦{len(store) - 1}⟧"

    return PLACEHOLDER_RE.sub(sub, text), store


def unprotect(text: str, store: list) -> str:
    return re.sub(r"⟦(\d+)⟧", lambda m: store[int(m.group(1))], text)


def place_block_text(block: Block, new: str, line_starts: list, text: str) -> tuple:
    """Return (start, end, replacement) that swaps the block's content for `new`."""
    first_li, first_prefix, _ = block.lines[0]
    last_li, _, last_text = block.lines[-1]
    start = line_starts[first_li] + len(first_prefix)
    end = line_starts[last_li] + len(block.lines[-1][1]) + len(last_text)
    crlf = "\r\n" in text
    if len(block.lines) == 1:
        return start, end, new
    widths = [len(t) for _, _, t in block.lines]
    cont_prefix = block.lines[1][1]
    width = max(max(widths), 60)
    wrapped: list = []
    for piece in textwrap.wrap(new, width=width, break_long_words=False, break_on_hyphens=False) or [new]:
        # A wrapped line must not start like a list item, quote, or heading: Markdown would read it as new structure.
        if wrapped and re.match(r"([-*+>#]|\d+[.)])(\s|$)", piece):
            wrapped[-1] += " " + piece
        else:
            wrapped.append(piece)
    return start, end, (("\r\n" if crlf else "\n") + cont_prefix).join(wrapped)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def iter_files(paths: list, cfg: Config):
    for p in paths:
        p = Path(p)
        if p.is_file():
            yield p
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in DEFAULT_EXCLUDE_DIRS)
                for f in sorted(files):
                    if f.lower().endswith((".md", ".markdown")):
                        fp = Path(root) / f
                        try:
                            rel = fp.resolve().relative_to(cfg.root).as_posix()
                        except ValueError:
                            rel = fp.as_posix()
                        if not any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f, pat) for pat in cfg.exclude):
                            yield fp
        else:
            print(f"ste: no such file or directory: {p}", file=sys.stderr)


MARKDOWN_EXT = {".md", ".markdown", ".mdown", ".mkd"}
TEXT_EXT = {".txt", ".text", ""}      # no extension (README, NOTES) counts as plain text


def file_format(path: Path, override: Optional[str] = None) -> Optional[str]:
    """'markdown', 'text', or None when the file type is not supported (code, config, and so on)."""
    if override in ("markdown", "text"):
        return override
    ext = path.suffix.lower()
    if ext in MARKDOWN_EXT:
        return "markdown"
    if ext in TEXT_EXT:
        return "text"
    return None


def default_output(src: Path) -> Path:
    return src.with_name("updated-" + src.name)


def set_format(fp: Path, args, cfg: Config) -> bool:
    fmt = file_format(fp, getattr(args, "as_format", None))
    if fmt is None:
        print(f"ste: skipping {fp}: unsupported file type '{fp.suffix}'. Use --as text or --as markdown to force it.", file=sys.stderr)
        return False
    cfg.plain = fmt == "text"
    return True


def plan_files(args, tool: str):
    """Work out the input, the output, and the format for a process-style command.

    Returns (src, dest, in_place, fmt), or an int exit code after printing the problem.
    Default output: updated-NAME next to the input. The input is only changed with --in-place.
    """
    src = Path(args.input)
    if not src.is_file():
        print(f"{tool}: no such file: {src}", file=sys.stderr)
        return 2
    fmt = file_format(src, args.as_format)
    if fmt is None:
        print(f"{tool}: '{src.suffix}' files are not supported. This tool is for prose: Markdown (.md) and plain text (.txt). "
              f"Code and config files would be damaged. To force it anyway, add --as text or --as markdown.", file=sys.stderr)
        return 2
    if args.in_place and args.output:
        print(f"{tool}: --in-place cannot be combined with an output file name.", file=sys.stderr)
        return 2
    dest = src if args.in_place else Path(args.output) if args.output else default_output(src)
    in_place = dest.resolve() == src.resolve()
    if not in_place and dest.exists() and not args.force:
        print(f"{tool}: {dest} already exists. Use --force to replace it, or give another output name.", file=sys.stderr)
        return 2
    return src, dest, in_place, fmt


def read_text(path: Path) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", newline="") as fh:   # newline="" keeps CRLF files as they are
            return fh.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"ste: cannot read {path}: {e}", file=sys.stderr)
        return None


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def apply_cli_config(args, start: Optional[Path] = None) -> Config:
    cfg = load_config(start)
    if getattr(args, "disable", None):
        cfg.disable |= {x.strip() for x in args.disable.split(",") if x.strip()}
    if getattr(args, "only", None):
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        cfg.disable |= {r for r in RULES if r not in only and r.split(".")[0] not in only}
    if getattr(args, "max_procedural", None):
        cfg.max_procedural = args.max_procedural
    if getattr(args, "max_descriptive", None):
        cfg.max_descriptive = args.max_descriptive
    if getattr(args, "fail_on", None):
        cfg.fail_on = args.fail_on
    return cfg


def cmd_check(args) -> int:
    cfg = apply_cli_config(args)
    dic = None if args.no_dict else Dictionary.find(cfg.dictionary, cfg.root)
    total = {"info": 0, "warning": 0, "error": 0}
    per_rule: dict = {}
    nfiles = 0
    hidden = 0
    all_json = []
    for fp in iter_files(args.paths or ["."], cfg):
        if not set_format(fp, args, cfg):
            continue
        text = read_text(fp)
        if text is None:
            continue
        nfiles += 1
        shown = fp.as_posix()
        for f in lint_text(text, cfg, dic, shown):
            if SEVERITIES[f.severity] < SEVERITIES[args.min_severity]:
                hidden += 1
                continue
            total[f.severity] += 1
            per_rule[f.rule] = per_rule.get(f.rule, 0) + 1
            if args.format == "json":
                all_json.append({"file": shown, "line": f.line, "column": f.col, "severity": f.severity, "rule": f.rule,
                                 "message": f.message, "fixable": f.replacement is not None})
            elif args.format == "github":
                lvl = {"error": "error", "warning": "warning", "info": "notice"}[f.severity]
                print(f"::{lvl} file={shown},line={f.line},col={f.col},title=STE {f.rule}::{f.message}")
            else:
                fix = " [fixable]" if f.replacement is not None else ""
                print(f"{shown}:{f.line}:{f.col}: {f.severity} [{f.rule}] {f.message}{fix}")
    if args.format == "json":
        print(json.dumps(all_json, indent=2))
    elif args.format == "text":
        parts = ", ".join(f"{r}: {c}" for r, c in sorted(per_rule.items()))
        print(f"\n{nfiles} file(s). {total['error']} error(s), {total['warning']} warning(s), {total['info']} info.", file=sys.stderr)
        if parts:
            print(f"By rule: {parts}", file=sys.stderr)
        if hidden:
            print(f"{hidden} lower-severity finding(s) hidden. Use --min-severity info to show them.", file=sys.stderr)
        if dic is None and cfg.on("1.2"):
            print("Tip: run 'ste dict build ASD-STE100_ISSUE9.txt' to turn on the full dictionary check.", file=sys.stderr)
    threshold = SEVERITIES[cfg.fail_on]
    return 1 if any(total[s] and SEVERITIES[s] >= threshold for s in total) else 0


def show_diff(path: str, old: str, new: str) -> None:
    sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))


def cmd_fix(args) -> int:
    cfg = apply_cli_config(args)
    dic = None if args.no_dict else Dictionary.find(cfg.dictionary, cfg.root)
    changed = 0
    for fp in iter_files(args.paths or ["."], cfg):
        if not set_format(fp, args, cfg):
            continue
        old = read_text(fp)
        if old is None:
            continue
        new, n = apply_safe_fixes(old, cfg, dic)
        if new != old:
            changed += 1
            if args.write:
                write_text(fp, new)
                print(f"fixed {fp.as_posix()} ({n} change(s))")
            else:
                show_diff(fp.as_posix(), old, new)
    if not args.write and changed:
        print(f"\n{changed} file(s) would change. Run again with --write to apply.", file=sys.stderr)
    return 0


def backup_path(p: Path) -> Path:
    b = p.with_name(p.name + ".bak")
    n = 1
    while b.exists():
        b = p.with_name(f"{p.name}.bak{n}")
        n += 1
    return b


def cmd_process(args) -> int:
    plan = plan_files(args, "ste")
    if isinstance(plan, int):
        return plan
    src, dest, in_place, fmt = plan
    text = read_text(src)
    if text is None:
        return 2
    cfg = apply_cli_config(args, src.resolve().parent)
    cfg.plain = fmt == "text"
    dic = None if args.no_dict else Dictionary.find(cfg.dictionary, cfg.root)
    keep = lambda fs: [f for f in fs if SEVERITIES[f.severity] >= SEVERITIES[args.min_severity]]
    before = len(keep(lint_text(text, cfg, dic)))
    new, _ = apply_safe_fixes(text, cfg, dic)
    after = len(keep(lint_text(new, cfg, dic)))
    print(f"{src.as_posix()}: {before} -> {after} finding(s) after mechanical fixes", file=sys.stderr)
    if args.dry_run:
        show_diff(src.as_posix(), text, new)
        return 0
    if in_place:
        if new == text:
            print("No changes needed.", file=sys.stderr)
            return 0
        if not args.no_backup:
            bak = backup_path(src)
            write_text(bak, text)
            print(f"Backup of the original: {bak.as_posix()}", file=sys.stderr)
        write_text(src, new)
        print(f"Updated {src.as_posix()}", file=sys.stderr)
    else:
        write_text(dest, new)
        print(f"Wrote {dest.as_posix()} (the original is unchanged)", file=sys.stderr)
    if after:
        print(f"{after} finding(s) remain. See them with: ste.py check {dest.as_posix()}", file=sys.stderr)
    return 0


def cmd_dict(args) -> int:
    if args.action == "build":
        if not args.pdf:
            raise SystemExit("ste: give the text of your copy of the standard: ste dict build ASD-STE100_ISSUE9.txt")
        data = build_dictionary_from_file(args.pdf)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {out}: {len(data['non_approved'])} non-approved words, {len(data['approved'])} approved words.")
        print("ASD owns the copyright of the dictionary. Keep this file private: add .ste/ to .gitignore.")
        return 0
    cfg = load_config()
    d = Dictionary.find(cfg.dictionary, cfg.root)
    print("No dictionary found. Run: ste dict build ASD-STE100_ISSUE9.txt" if d is None
          else f"Dictionary loaded: {len(d.forms) + len(d.phrases)} non-approved forms, {len(d.approved)} approved words.")
    return 0


def cmd_rules(_args) -> int:
    print(f"{'Rule':<6} {'Severity':<8} {'Fix':<5} {'Method':<26} Summary")
    for rid, (summary, sev, fix, how) in RULES.items():
        print(f"{rid:<6} {sev:<8} {fix:<5} {how:<26} {summary}")
    print("\nNot checked (they need judgment): 1.5-1.13 technical terms, 2.1 multi-word nouns, 4.1, 4.3 to 4.5, 5.5, "
          "6.1, 6.2, 6.4, 6.5, section 7 safety instructions, 8.2 to 8.5, 9.1 to 9.4, GR-1 to GR-5, GR-8.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ste", description="Check and fix Markdown toward ASD-STE100 Simplified Technical English. Offline: no network, no LLM.")
    p.add_argument("--version", action="version", version=f"ste {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, writes=False):
        sp.add_argument("paths", nargs="*", help="Files or folders (default: current folder)")
        sp.add_argument("--disable", help="Comma-separated rule ids to turn off, for example 3.5,GR-7")
        sp.add_argument("--only", help="Comma-separated rule ids to check")
        sp.add_argument("--max-procedural", type=int, help="Word limit for instructions (default 20)")
        sp.add_argument("--max-descriptive", type=int, help="Word limit for descriptive text (default 25)")
        sp.add_argument("--no-dict", action="store_true", help="Ignore the dictionary file")
        sp.add_argument("--as", dest="as_format", choices=["markdown", "text"], help="Treat files as this format whatever their extension")
        if writes:
            sp.add_argument("--write", action="store_true", help="Change the files (default: show a diff)")

    c = sub.add_parser("check", help="Report problems")
    common(c)
    c.add_argument("--format", choices=["text", "json", "github"], default="text")
    c.add_argument("--min-severity", choices=list(SEVERITIES), default="warning", help="Hide findings below this level (default: warning)")
    c.add_argument("--fail-on", choices=list(SEVERITIES), help="Exit 1 at this severity or higher (default: error)")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("fix", help="Apply safe mechanical fixes")
    common(f, writes=True)
    f.set_defaults(func=cmd_fix)

    pr = sub.add_parser("process", help="Apply the safe mechanical fixes to one file. Writes updated-NAME unless you name an output")
    pr.add_argument("input", help="Markdown (.md) or plain text (.txt) file to process")
    pr.add_argument("output", nargs="?", help="Write the result here (default: updated-NAME next to the input)")
    pr.add_argument("--in-place", action="store_true", help="Update the input file instead (a .bak copy is kept)")
    pr.add_argument("--as", dest="as_format", choices=["markdown", "text"], help="Treat the file as this format whatever its extension")
    pr.add_argument("--disable", help="Comma-separated rule ids to turn off")
    pr.add_argument("--only", help="Comma-separated rule ids to check")
    pr.add_argument("--max-procedural", type=int)
    pr.add_argument("--max-descriptive", type=int)
    pr.add_argument("--no-dict", action="store_true", help="Ignore the dictionary file")
    pr.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)   # accepted and ignored: this tool never calls an LLM
    pr.add_argument("--min-severity", choices=list(SEVERITIES), default="warning")
    pr.add_argument("--dry-run", action="store_true", help="Show a diff and write nothing")
    pr.add_argument("--no-backup", action="store_true", help="With --in-place, do not keep a .bak copy")
    pr.add_argument("--force", action="store_true", help="Replace the output file if it exists")
    pr.add_argument("-v", "--verbose", action="store_true")
    pr.set_defaults(func=cmd_process)

    d = sub.add_parser("dict", help="Build or inspect the local dictionary")
    d.add_argument("action", choices=["build", "info"])
    d.add_argument("pdf", nargs="?", help="Path to ASD-STE100_ISSUE9.txt (make it with: pdftotext -layout ASD-STE100_ISSUE9.pdf ASD-STE100_ISSUE9.txt)")
    d.add_argument("--out", default=".ste/dictionary.json")
    d.set_defaults(func=cmd_dict)

    ru = sub.add_parser("rules", help="List the rules this tool checks")
    ru.set_defaults(func=cmd_rules)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
