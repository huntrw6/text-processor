---
name: process-text
description: Simplify or shrink .md/.txt files (ASD-STE100, caveman)
version: 1.1.0
metadata:
  hermes:
    tags: [writing, markdown, documentation, editing]
    category: writing
    requires_toolsets: [terminal]
---

# Process Text

Rewrite a Markdown or plain-text file in one of two styles without changing what it says:

- **technical**: ASD-STE100 Simplified Technical English. Short sentences, active voice, simple tenses, no contractions.
- **caveman**: fewer tokens. No filler, pleasantries, or (at full and ultra) articles.

The scripts are plain Python with no packages to install. They are offline and apply fixed rules only: they never call an LLM, never use an API key, and never start other programs. You do the sentence-level rewriting yourself.
Invocation by the user looks like `/process-text README.md -caveman` or `/process-text notes.txt -technical`.

## When to Use

- The user asks to simplify, clarify, or "STE" a `.md` or `.txt` file.
- The user asks to shorten a file, cut tokens, "caveman" it, or compress it.
- Not for code or config files (`.py`, `.json`, `.yaml`, `.html`). The scripts refuse them on purpose.

## Procedure

1. Find the scripts folder:
   ```sh
   S=$(dirname "$(find "${HERMES_HOME:-$HOME/.hermes}/skills" -path '*/process-text/scripts/proc.py' | head -1)"); echo "$S"
   ```
   If that prints an empty line, try the project: `find . -path '*process-text/scripts/proc.py'`.
2. Pick the mode from the request: technical, caveman, or both. With no tag, use technical.
3. **Technical**
   1. `python3 "$S/proc.py" FILE -technical` writes `updated-FILE` next to FILE. FILE is not changed.
   2. `python3 "$S/ste.py" check updated-FILE --format json` lists what is left: line, column, rule, severity, message.
   3. Rewrite each flagged paragraph in `updated-FILE` with your file tools. Load the rules first: `skill_view("process-text", "references/ste-rules.md")`.
      Keep code, inline code, links, URLs, paths, flags, numbers, names, and every "not", "never", and "must". Do not edit headings, tables, or code blocks.
   4. Run step 2 again. Repeat until no `error` findings remain. Fix `warning` findings when the fix keeps the meaning, and tell the user about the rest.
   5. `python3 "$S/verify.py" FILE updated-FILE`. Fix every PROBLEM it prints.
4. **Caveman**
   1. `python3 "$S/proc.py" FILE -caveman --level full `. Levels: `lite`, `full` (default), `ultra`.
   2. At `full` and `ultra` you may shorten further by hand: fragments are fine and short words are better. Keep the words that set order or limits (must, never, only, unless, before, after, then).
   3. `python3 "$S/verify.py" FILE updated-FILE --require-shorter`. Fix every PROBLEM it prints.
5. **Both tags**: do step 3 first. Then run `python3 "$S/proc.py" updated-FILE final-FILE -caveman --level lite ` and `python3 "$S/verify.py" FILE final-FILE`.
   Use only `lite` here. Dropping articles breaks STE Rule 4.2 (do not omit words).
6. Tell the user the output path, the before and after word counts from `verify.py`, and what is left. Show `diff -u FILE updated-FILE | head -60`.

| Level | What it removes |
| --- | --- |
| `lite` | Filler (really, basically, just), pleasantries, wordy phrases. Keeps articles and full sentences. |
| `full` | Also a, an, the. Fragments are fine. |
| `ultra` | Also uses abbreviations (config, DB, env, docs) and `~`. By hand, add arrows for cause and flow. |

## Recipe: shrink a README to setup and usage

Trigger phrase that works: `/process-text README.md -technical`, then cut to setup and usage only.

1. The scripts only reword; they never decide what to delete. Cut sections by hand first: features, background, troubleshooting, build/release steps, long protocol notes. Keep install, run examples, one options table, one keys table.
2. Keep the license section as two lines linking to the license file. Do not drop it unless the user says so.
3. Write the short draft, then run the Technical steps (`ste.py check`, `proc.py -technical`, `verify.py`) on it. A draft written in short, active sentences usually shows 0 findings.
4. Save the original first (`cp README.md ../README.full.bak`). Write to README.md only if the user asks for that; otherwise leave `updated-README.md`.
5. Skip `-caveman` for human-read docs: it drops articles and reads oddly.
6. Check every flag and command in the new text against the code or `--help`; `verify.py` cannot do this.

## Pitfalls

- Never overwrite the user's original unless they ask. The default output is `updated-FILE`. `--in-place` updates the original and keeps `FILE.bak`; use it only on request.
- The scripts never send text anywhere. If a user asks for an API or another LLM, say that this tool does not support it and do the rewriting yourself.
- Paragraphs that contain safety words (warning, caution, irreversible, permanently, `--force`) are skipped on purpose. Leave them unchanged, in both modes.
- Only `.md`, `.markdown`, `.txt`, and `.text` files are accepted. For another prose format, such as `.rst`, add `--as text`.
- If `updated-FILE` already exists the script stops. Use `--force` to replace it, or give another output name as the second argument.
- The checker uses patterns, not a grammar parser. It flags some technical nouns (for example "logging") and misses some passives. `--min-severity info` on `ste.py check` shows the lowest-level findings that are hidden by default.
- The file must be on the machine where the `terminal` tool runs. A Docker or remote backend has its own filesystem.
- `python3 "$S/ste.py" dict build ASD-STE100_ISSUE9.txt` adds a word-list check. It needs the text of the user's own copy of the standard (`pdftotext -layout ASD-STE100_ISSUE9.pdf ASD-STE100_ISSUE9.txt`, run by the user). Never commit or share the result.

## Verification

- Technical: `ste.py check updated-FILE` shows no `error` findings, and `verify.py` prints `OK`.
- Caveman: `verify.py ... --require-shorter` prints `OK` and the word count went down.
- You read the diff yourself. `verify.py` catches lost code, links, numbers, negations, and identifiers. It cannot prove that the meaning is the same.

## Files

- `scripts/proc.py`: the command (`-technical`, `-caveman`, `--level`, `--in-place`).
- `scripts/ste.py`: the STE checker (`check`, `fix`) and the Markdown parser.
- `scripts/terse.py`: the token-shrinking rules.
- `scripts/verify.py`: the check that a rewrite kept every fact.
- `references/ste-rules.md`: the rules the checker uses, with a way to fix each one.
