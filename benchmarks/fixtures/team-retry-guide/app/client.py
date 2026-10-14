"""Show a retry decision. This command performs no network request."""
import argparse
import json
from pathlib import Path

from retry import decide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--status", required=True, type=int)
    parser.add_argument("--attempt", required=True, type=int)
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    print(json.dumps(decide(args.status, args.attempt, settings)))


if __name__ == "__main__":
    main()
