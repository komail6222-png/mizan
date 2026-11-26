"""Download the OPSD household 60-minute file into var/opsd and check it. The only OPSD code that touches the network.

    .venv/bin/python scripts/fetch_opsd.py --out var/opsd

The file (15,654,559 bytes, Open Power System Data, Data Package Household Data, version 2020-04-15, CC BY 4.0) is
downloaded through a .part file and kept only when its size and SHA-256 equal the pinned values in mizan/opsd.py.
A file already present is verified, never replaced. See docs/DATA_LICENSES.md.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import opsd, visa  # noqa: E402
from mizan.core.contract import Refused, require  # noqa: E402
from scripts.fetch_visa import download  # noqa: E402


def fetch(out: Path, url=opsd.URL, *, file_bytes=opsd.FILE_BYTES, file_sha256=opsd.FILE_SHA256, say=print) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    dest = out / opsd.FILE_NAME
    if dest.exists():
        sha = visa.sha256_file(dest)
        require(dest.stat().st_size == file_bytes and sha == file_sha256, "OPSD_HASH",
                f"{dest} is present but is not the pinned file (SHA-256 {sha}); move it away to download again")
        say(f"{dest}: present, {file_bytes:,} bytes, SHA-256 verified")
        return dest
    say(f"{dest.name}: downloading {file_bytes:,} bytes from {url}")
    sha = download(url, dest, file_bytes, say=say)
    if sha != file_sha256:
        dest.unlink(missing_ok=True)
        raise Refused("OPSD_HASH", f"{url}: SHA-256 {sha}, expected {file_sha256}; nothing was kept")
    say(f"{dest}: {file_bytes:,} bytes, SHA-256 verified")
    return dest


def main(argv=None, *, file_bytes=opsd.FILE_BYTES, file_sha256=opsd.FILE_SHA256):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="download folder, for example var/opsd (git-ignored)")
    parser.add_argument("--url", default=opsd.URL)
    args = parser.parse_args(argv)
    try:
        fetch(args.out, args.url, file_bytes=file_bytes, file_sha256=file_sha256)
    except Refused as exc:
        print(f"Refused {exc.code}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
