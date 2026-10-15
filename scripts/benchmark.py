#!/usr/bin/env python3
"""Run the full comparison and improvement report from any working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop_work.cli import main

if __name__ == '__main__':
    raise SystemExit(main(['bench', 'check', *sys.argv[1:]]))
