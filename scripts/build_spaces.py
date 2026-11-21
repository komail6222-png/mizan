"""Produce three independent static portfolio exhibits from the local accepted results."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mizan.demos import exhibit_html  # noqa: E402
SPACES = {
    "sanad": ("Bilingual UPS procurement", "📑", ["evidence"]),
    "basira": ("Few-shot surface inspection", "🔍", ["crack", "intact", "scarce"]),
    "rasid": ("Risk-aware energy planning", "⚡", ["energy", "neutral", "oracle"]),
}
DESCRIPTIONS = {
    "sanad": "Compare an Arabic manufacturer sheet with an English approved schedule. Change the authority policy and trace a verdict to its exact source text.",
    "basira": "Inspect an image and its anomaly overlay. Compare a cracked ring, an intact ring that triggered a false alert, and a small calibration bank.",
    "rasid": "Compare two risk settings, examine the hourly battery schedule, and download the independently checked plan with its scenario costs.",
}


def build(source, destination):
    data = json.loads(Path(source).read_bytes())
    for kind, (title, emoji, names) in SPACES.items():
        folder = Path(destination) / kind
        folder.mkdir(parents=True, exist_ok=True)
        run = dict(data["run"])
        run["receipts"] = [r for r in run["receipts"] if r["fixture"] in names]
        packet = {"run": run, "results": {n: data["results"][n] for n in names}}
        (folder / "index.html").write_text(exhibit_html(packet, kind), encoding="utf-8")
        readme = (f"---\ntitle: MIZAN {kind.upper()}\nemoji: {emoji}\ncolorFrom: green\ncolorTo: blue\n"
                  f"sdk: static\napp_file: index.html\npinned: false\nshort_description: {title}\n---\n\n"
                  f"# {kind.upper()} | {title}\n\n{DESCRIPTIONS[kind]}\n\n"
                  "This interactive exhibit replays recorded local CPU runs on generated fixtures. "
                  "The page re-verifies every SHA-256 digest and Ed25519 receipt in your browser and exports them as JSON. "
                  "The full source release supplies the input importer, live job API, baselines and verification script.\n")
        (folder / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=ROOT / "reports/demo/demo_results.json")
    parser.add_argument("--out", default=ROOT / "spaces")
    args = parser.parse_args()
    build(args.source, args.out)
