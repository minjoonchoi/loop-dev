"""Stop old linked entrypoints with an actionable migration instruction."""
import sys


def main(argv=None, *, entrypoint=None):
    print("loop-dev was renamed to loop-work. From the source checkout run "
          "python3 scripts/install.py --uninstall-legacy (add --target PROJECT for project scope), "
          "then make install with the same scope and start a new conversation. "
          "Knowledge and task records are preserved.", file=sys.stderr)
    return 2
