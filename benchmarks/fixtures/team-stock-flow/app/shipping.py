import argparse
import json
from pathlib import Path

from planner import plan
from stock import read_stock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", required=True)
    parser.add_argument("--order", required=True)
    parser.add_argument("--policy", required=True)
    args = parser.parse_args()
    try:
        requests = json.loads(Path(args.order).read_text(encoding="utf-8"))
        policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
        result = plan(read_stock(args.stock), requests, policy)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
