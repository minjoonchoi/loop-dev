"""Write formatted CSV to stdout, leaving the input file unchanged."""
import argparse
import json
from pathlib import Path
import sys

from formatting import render


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    sys.stdout.write(render(rows, settings))


if __name__ == "__main__":
    main()
