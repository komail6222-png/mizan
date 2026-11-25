"""Download VisA into var/visa and record or verify its manifest. This is the only MIZAN code that touches the network.

    .venv/bin/python scripts/fetch_visa.py --out var/visa

The archive (1,929,840,640 bytes) and the official split are streamed to <out>/<name>.part, hashed while they arrive,
checked for their exact size and only then renamed; the archive is extracted through a staging folder into <out>/data.
The source publishes no checksum, so the first run writes reports/visa/manifest.json (trust on first use) with the
archive's and the split's SHA-256 and every image's; every later run verifies against it and never rewrites it.
VisA is CC BY 4.0: see docs/DATA_LICENSES.md.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mizan import visa  # noqa: E402
from mizan.core.contract import Refused, require  # noqa: E402

MANIFEST = ROOT / "reports" / "visa" / "manifest.json"


def download(url, dest, expected_bytes):
    """Stream url to dest via dest.part; the rename happens only after the exact byte count is confirmed."""
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    n = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as f:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
        require(n == expected_bytes, "VISA_DOWNLOAD", f"{url}: received {n:,} bytes, expected {expected_bytes:,}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return h.hexdigest()


def obtain(url, dest, expected_bytes, say):
    if dest.is_file() and dest.stat().st_size == expected_bytes:
        say(f"{dest.name}: already downloaded, hashing")
        return visa.sha256_file(dest)
    dest.unlink(missing_ok=True)
    say(f"{dest.name}: downloading {expected_bytes:,} bytes from {url}")
    return download(url, dest, expected_bytes)


def extract(archive, data):
    """Extract with the tarfile 'data' filter (no absolute paths, no escapes, no links out) through a staging folder."""
    staging = data.with_name(data.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with tarfile.open(archive) as t:
            t.extractall(staging, filter="data")
        os.replace(staging, data)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def data_root(data, rows):
    """The folder under data that holds every image the split lists (the archive may wrap them in a top folder)."""
    for candidate in [data, *sorted(d for d in data.iterdir() if d.is_dir())]:
        if all((candidate / r["image"]).is_file() for r in rows):
            return candidate
    raise Refused("VISA_DATA", f"No folder under {data} holds every image the split lists")


def main(argv=None, *, archive_bytes=visa.ARCHIVE_BYTES, split_bytes=visa.SPLIT_BYTES):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="download folder, for example var/visa (git-ignored)")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--archive-url", default=visa.ARCHIVE_URL)
    parser.add_argument("--split-url", default=visa.SPLIT_URL)
    args = parser.parse_args(argv)
    say = lambda s: print(s, flush=True)  # noqa: E731
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        known = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest.exists() else None
        archive = args.out / Path(urlparse(args.archive_url).path).name
        split = args.out / "1cls.csv"
        archive_sha = obtain(args.archive_url, archive, archive_bytes, say)
        split_sha = obtain(args.split_url, split, split_bytes, say)
        if known:
            require(archive_sha == known["archive_sha256"] and split_sha == known["split_sha256"], "VISA_HASH",
                    "The archive or the split differs from the one the manifest records")
        data = args.out / "data"
        if not data.is_dir():
            say("extracting")
            extract(archive, data)
        rows = visa.read_split(split)
        root = data_root(data, rows)
        if known:
            visa.verify_manifest(root, known)
            say(f"verified {len(known['images']):,} images against {args.manifest}")
            return 0
        manifest = visa.build_manifest(root, rows, archive_sha, split_sha)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        staged = args.manifest.with_name(args.manifest.name + ".part")
        staged.write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(staged, args.manifest)
        counts = Counter((x["object"], x["split"], x["label"]) for x in manifest["images"])
        for key in sorted(counts):
            say(f"  {' '.join(key)}: {counts[key]}")
        say(f"archive sha256 {archive_sha}\nsplit sha256 {split_sha}\nwrote {args.manifest} ({len(manifest['images']):,} images)")
        return 0
    except Refused as exc:
        print(f"Refused {exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
