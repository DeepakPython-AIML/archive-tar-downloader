# archive-bulk-download

Download `.tar` bulk files from the [arXiv.org Bulk Content](https://archive.org/details/arxiv-bulk) collection on archive.org.

You pass the number of tar files you want (e.g. `10` or `20`), and the script downloads that many into the project's `downloads/` folder.

## Requirements

- Python 3.14+ (see `.python-version`)
- [uv](https://docs.astral.sh/uv/) (optional, but recommended)
- No third-party dependencies (standard library only)

## Usage

Run from the project root:

```bash
# Download 20 tar files
uv run archive-bulk-download 20

# Download 10 tar files
uv run archive-bulk-download 10

# Save to a custom folder instead of ./downloads
uv run archive-bulk-download 5 --output-dir /path/to/folder

# List tar file paths to tar_files.csv (default filename)
uv run archive-bulk-download 20 --list

# List tar file paths to a custom CSV file
uv run archive-bulk-download 20 --list my_tar_files.csv

# List ALL available tar files with sizes and total GB (no count)
uv run archive-bulk-download --list
uv run archive-bulk-download --list all_tar_files.csv

# Short form
uv run archive-bulk-download 20 -l
uv run archive-bulk-download 20 -l my_tar_files.csv
uv run archive-bulk-download -l
```

Without `uv`:

```bash
PYTHONPATH=src python3 -m archive_bulk_download.downloader 10
```

### Arguments

| Argument | Description | Default |
| --- | --- | --- |
| `count` | Number of `.tar` files to download/list (positive integer). Required for download; optional with `--list` (omits to list all). | required |
| `-o`, `--output-dir` | Directory to save the files in. | `./downloads` |
| `-l`, `--list` [FILE] | List `.tar` file info to a CSV file instead of downloading. Use `FILE` as the output name (default: `tar_files.csv`). Columns: `id, name, url, size` (size in human-readable MB). Omit `COUNT` to list **all** available files with sizes and a total GB sum row at the end. | — |

## How it works

1. Lists the items (the grid entries on the collection page) through the archive.org search API, sorted by identifier (`arXiv_pdf_0001_001`, `arXiv_pdf_0001_002`, ...).
2. Reads each item's metadata to find its `.tar` file, e.g. `https://archive.org/download/arXiv_pdf_0609_003/arXiv_pdf_0609_003.tar`.
3. Items without a `.tar` file (such as `arxiv-bulk-hashes` and `arxiv-bulk-metadata`) are skipped.
4. Downloads files one by one until `count` files are saved.

### Behaviour

- **Skip completed files:** before downloading, the script checks whether `<name>.tar` already exists in the output directory and matches archive.org's expected size. If it does, the file is skipped (not re-downloaded) and still counts toward your `count` target. Running `archive-bulk-download 20` when 5 files are already present downloads only the remaining 15.
- **Resume partial downloads:** in-progress downloads are saved as `<name>.tar.part`. If the script is stopped (Ctrl+C) or the connection drops, running it again resumes each `.tar.part` file from where it left off using HTTP `Range` requests. If a partial file is already complete, it is renamed to `.tar` without re-downloading.
- **Size verification:** a `.tar` file is only considered finished once its size matches archive.org's reported size, preventing truncated/corrupted files from being treated as complete.
- **Items without a `.tar` file** (for example `arxiv-bulk-hashes` and `arxiv-bulk-metadata`) are skipped automatically.
- **Retries:** failed requests are retried up to 3 times.
- **Exit code:** `0` if all requested files were downloaded or already present, `1` otherwise, `130` if interrupted.

## Notes

- Each tar file is roughly 130–500 MB, so large counts need several GB of free disk space.
- `downloads/` is listed in `.gitignore`, so the archives are not committed.

