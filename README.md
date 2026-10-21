# process-text

Shorten or simplify a markdown or text file. Use `-technical` for simplified technical English. Use `-caveman` short word use.

The tool is completely offline. It applies fixed rules and leaves sentence-level rewriting to you.

Use Python 3.9 or newer. The tool accepts `.md` and `.txt` files only.

## Install

```sh
python3 skills/process-text/scripts/process --install
```

Add `~/.local/bin` to `PATH` if a shell cannot find `process`.

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
