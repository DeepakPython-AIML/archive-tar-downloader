"""Download .tar bulk files from the arXiv bulk collection on archive.org.

Collection page : https://archive.org/details/arxiv-bulk
Item page       : https://archive.org/details/<identifier>
Tar download    : https://archive.org/download/<identifier>/<identifier>.tar

Usage:
    uv run archive-bulk-download 20            # download 20 tar files
    python -m archive_bulk_download.downloader 10 --output-dir data
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

COLLECTION = "arxiv-bulk"
COLLECTION_URL = f"https://archive.org/details/{COLLECTION}"
SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

PAGE_SIZE = 100
CHUNK_SIZE = 1024 * 1024
RETRIES = 3
TIMEOUT = 60
USER_AGENT = "archive-bulk-download/0.1 (+https://archive.org/details/arxiv-bulk)"

# <project root>/downloads  (this file lives in <root>/src/archive_bulk_download/)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "downloads"


def _open(url: str, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def _get_json(url: str) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with _open(url) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def iter_item_identifiers() -> Iterator[str]:
    """Yield item identifiers (the grid items) of the collection, page by page."""
    page = 1
    while True:
        query = urllib.parse.urlencode(
            [
                ("q", f"collection:{COLLECTION}"),
                ("fl[]", "identifier"),
                ("sort[]", "identifier asc"),
                ("rows", PAGE_SIZE),
                ("page", page),
                ("output", "json"),
            ]
        )
        docs = _get_json(f"{SEARCH_URL}?{query}")["response"]["docs"]
        if not docs:
            return
        for doc in docs:
            yield doc["identifier"]
        page += 1


def find_tar_file(identifier: str) -> tuple[str, int | None] | None:
    """Return (filename, size) of the item's .tar file, or None if it has none."""
    files = _get_json(METADATA_URL.format(identifier=urllib.parse.quote(identifier))).get("files", [])
    for file in files:
        if file.get("name", "").lower().endswith(".tar"):
            size = file.get("size")
            return file["name"], int(size) if size else None
    return None


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _download_chunked(response, out, downloaded: int, expected_size: int | None) -> int:
    """Stream response body into `out`, returning total bytes written."""
    last_print = 0.0
    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            break
        out.write(chunk)
        downloaded += len(chunk)
        now = time.monotonic()
        if now - last_print >= 0.5:
            last_print = now
            total = _human(expected_size) if expected_size else "?"
            print(f"\r    {_human(downloaded)} / {total}", end="", flush=True)
    print()
    return downloaded


def _is_complete(destination: Path, expected_size: int | None) -> bool:
    """Return True if destination exists and (optionally) matches expected size."""
    if not destination.exists():
        return False
    if expected_size is None:
        return True
    return destination.stat().st_size == expected_size


def download_file(url: str, destination: Path, expected_size: int | None) -> None:
    """Download `url` to `destination`, resuming a partial `.part` file if present."""
    part = destination.with_name(destination.name + ".part")

    for attempt in range(1, RETRIES + 1):
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}

            try:
                response = _open(url, headers)
            except urllib.error.HTTPError as error:
                if error.code == 416:  # requested range not satisfiable -> already complete
                    break
                raise

            with response:
                # If we asked to resume but the server ignored Range (200 instead of 206),
                # restart from scratch.
                if offset and response.status != 206:
                    offset = 0

                mode = "ab" if offset else "wb"
                with part.open(mode) as out:
                    _download_chunked(response, out, offset, expected_size)

            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            print(f"\n    attempt {attempt}/{RETRIES} failed: {error}")
            if attempt == RETRIES:
                raise
            time.sleep(2 * attempt)

    if expected_size is not None and part.stat().st_size != expected_size:
        raise RuntimeError(
            f"Size mismatch for {destination.name}: "
            f"got {part.stat().st_size}, expected {expected_size}"
        )
    part.rename(destination)


def run(count: int, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Collection : {COLLECTION_URL}")
    print(f"Saving to  : {output_dir}")
    print(f"Target     : {count} tar file(s)\n")

    downloaded_new = 0
    skipped = 0
    failed = 0

    for identifier in iter_item_identifiers():
        if downloaded_new + skipped >= count:
            break

        try:
            tar = find_tar_file(identifier)
        except RuntimeError as error:
            print(f"[skip] {identifier}: {error}")
            failed += 1
            continue
        if tar is None:
            print(f"[skip] {identifier}: no .tar file in this item")
            continue

        filename, size = tar
        destination = output_dir / filename
        position = f"[{downloaded_new + skipped + 1}/{count}]"

        # Already fully downloaded: skip without re-downloading.
        if _is_complete(destination, size):
            print(f"{position} {filename} already downloaded, skipping")
            skipped += 1
            continue

        # Partial .part file exists -> download_file() resumes it.
        url = DOWNLOAD_URL.format(
            identifier=urllib.parse.quote(identifier),
            filename=urllib.parse.quote(filename),
        )
        print(f"{position} {filename} ({_human(size) if size else 'unknown size'})")
        try:
            download_file(url, destination, size)
        except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError) as error:
            print(f"    failed: {error}")
            failed += 1
            continue
        downloaded_new += 1

    print()
    print(
        f"Finished: {downloaded_new} new, {skipped} already present, "
        f"{failed} failed ({downloaded_new + skipped}/{count}) in {output_dir}"
    )
    return 0 if downloaded_new + skipped == count else 1


class ProgressBar:
    """Simple in-place terminal progress bar (standard library only)."""

    def __init__(self, total: int | None = None, width: int = 40):
        self.total = total
        self.width = width
        self.current = 0
        self.started = False

    def update(self, current: int, total: int | None = None, suffix: str = "") -> None:
        self.current = current
        if total is not None:
            self.total = total
        if self.total:
            pct = current / self.total * 100
            filled = int(self.width * current // self.total)
            bar = "#" * filled + "-" * (self.width - filled)
            line = f"\r[{bar}] {pct:5.1f}% ({current}/{self.total}) {suffix}"
        else:
            line = f"\rListing file {current}... {suffix}"
        print(line, end="", flush=True)
        self.started = True

    def finish(self, suffix: str = "") -> None:
        if self.started:
            print()  # move to next line


def list_tar_files(count: int | None, output_file: Path) -> int:
    """Write .tar file info to a CSV file instead of downloading.

    CSV columns: id, name, url, size.
    Opens/closes the file after every row so no long-lived connection is held.
    If ``count`` is None, all available `.tar` files are listed and a total
    size row is appended at the end.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"Collection : {COLLECTION_URL}")
    print(f"Output file: {output_file}")
    if count is not None:
        print(f"Target     : {count} tar file(s)\n")
    else:
        print("Target     : all available tar files\n")

    # Write CSV header first (create / truncate file).
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "url", "size"])

    listed = 0
    failed = 0
    total_bytes = 0
    progress = ProgressBar(total=count, width=40)

    for identifier in iter_item_identifiers():
        if count is not None and listed >= count:
            break

        try:
            tar = find_tar_file(identifier)
        except RuntimeError as error:
            print(f"\n[skip] {identifier}: {error}")
            failed += 1
            progress.update(listed, suffix=f"{failed} failed")
            continue
        if tar is None:
            print(f"\n[skip] {identifier}: no .tar file in this item")
            failed += 1
            progress.update(listed, suffix=f"{failed} skipped")
            continue

        filename, size = tar
        url = DOWNLOAD_URL.format(
            identifier=urllib.parse.quote(identifier),
            filename=urllib.parse.quote(filename),
        )

        listed += 1
        size_str = _human(size) if size else "unknown"
        if size:
            total_bytes += size

        # Open, append one row, close immediately — no long-lived connection.
        with output_file.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([identifier, filename, url, size_str])

        progress.update(listed, suffix=filename)

    progress.finish()

    # Append total size row at the end.
    total_gb = total_bytes / (1024**3)
    with output_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([])
        writer.writerow(["total", "", "", f"{total_gb:.2f} GB ({total_bytes:,} bytes)"])

    print()
    if count is not None:
        print(
            f"Listed {listed} tar file(s) in {output_file} ({failed} failed)"
        )
        return 0 if listed == count else 1
    else:
        print(
            f"Listed {listed} tar file(s) in {output_file} "
            f"({failed} failed), total size: {total_gb:.2f} GB"
        )
        return 0 if failed == 0 else 1


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download .tar files from the arXiv bulk collection on archive.org."
    )
    parser.add_argument(
        "count",
        type=_positive_int,
        nargs="?",
        default=None,
        help="number of .tar files to download/list. "
        "If omitted with --list, all available files are listed.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"directory to save files in (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "-l",
        "--list",
        nargs="?",
        const="tar_files.csv",
        default=None,
        help="list .tar file info to a CSV file instead of downloading "
        "(default filename: tar_files.csv). "
        "Omit COUNT to list all available files with sizes and total GB.",
    )
    args = parser.parse_args(argv)
    try:
        if args.list is not None:
            list_path = Path(args.list)
            sys.exit(list_tar_files(args.count, list_path))
        else:
            if args.count is None:
                parser.error("the following arguments are required: count")
            sys.exit(run(args.count, args.output_dir))
    except KeyboardInterrupt:
        print("\nInterrupted. Partial downloads (*.part) are resumed on the next run.")
        sys.exit(130)


if __name__ == "__main__":
    main()

