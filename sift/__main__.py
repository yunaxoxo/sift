"""Allow ``python -m sift ...`` as an alternative to the ``sift`` script."""

from sift.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
