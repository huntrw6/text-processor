# process-text

Rewrite a Markdown or text file. Use `-technical` for ASD-STE100 English. Use `-caveman` for fewer tokens.

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
process FILE -technical --no-llm
```

The result is `updated-FILE`. `FILE` does not change.

- `--level` accepts `lite`, `full` (default), and `ultra`.
- `--in-place` replaces `FILE` and saves a copy as `FILE.bak`.
- `--no-llm` uses the built-in rules only. Without it, the tool uses `ANTHROPIC_API_KEY` or `claude -p`.

## Right-click menu

```sh
process --install-scripts
```

This works in Nautilus and Dolphin.

## Hermes

```sh
hermes skills install OWNER/REPO/skills/process-text
```

Enter `/process-text FILE -caveman` in Hermes.
