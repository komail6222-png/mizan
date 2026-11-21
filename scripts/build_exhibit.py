"""Rebuild the combined exhibit page from an existing signed packet, without re-running or re-signing anything.

python scripts/build_exhibit.py --source reports/demo/demo_results.json
writes reports/demo/MIZAN_Demo.html and the copy served by `python -m mizan serve` (mizan/web/index.html).
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mizan.core.contract import strict_load  # noqa: E402
from mizan.demos import exhibit_html  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=ROOT / "reports/demo/demo_results.json")
    parser.add_argument("--out", nargs="+", default=[ROOT / "reports/demo/MIZAN_Demo.html", ROOT / "mizan/web/index.html"])
    args = parser.parse_args()
    page = exhibit_html(strict_load(Path(args.source).read_bytes()))
    for target in args.out:
        Path(target).write_text(page, encoding="utf-8")
        print(target)
