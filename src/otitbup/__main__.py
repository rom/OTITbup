"""Enable `python -m otitbup ...` in addition to the `otitbup` console
script (useful when the script dir isn't on PATH)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
