"""Run DataRanger with `python -m dataranger`, for when the `dataranger` command is not on PATH."""
import sys

from .cli import main

sys.exit(main())
