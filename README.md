# process-text

Shorten or simplify a Markdown or text file. Use `-technical` for ASD-STE100 English. Use `-caveman` for fewer tokens.

The tool is offline. It makes no network calls, starts no other programs, and never uses an LLM or an API key. It applies fixed rules and leaves sentence-level rewriting to you, or to the agent that runs it.

Use Python 3.9 or newer. The tool accepts `.md` and `.txt` files only.

## Install

```sh
python3 skills/process-text/scripts/process --install
```

If the shell cannot find `process`, add `~/.local/bin` to `PATH`.

## Run

```sh
process FILE -technical
process FILE -caveman
process FILE -technical -caveman
process FILE OUTPUT -caveman
process FILE -caveman --level lite
process FILE -caveman --in-place
process FILE -technical --dry-run
process FILE -technical --dry-run
```

The result is `updated-FILE`. `FILE` does not change.

- `--level` accepts `lite`, `full` (default), and `ultra`.
- `--in-place` replaces `FILE` and saves a copy as `FILE.bak`.
- The tool always uses the built-in rules only. The old `--no-llm` flag is accepted and does nothing.

## Right-click menu

```sh
process --install-scripts
```

This works in Nautilus and Dolphin.

## Hermes

```sh
hermes skills install huntrw6/text-processor/skills/process-text
```

Enter `/process-text FILE -caveman` in Hermes.
