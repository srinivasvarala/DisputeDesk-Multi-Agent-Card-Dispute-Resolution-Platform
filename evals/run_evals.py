"""Entry point for the golden-set evals (see src/disputedesk/evals/runner.py).

    uv run python evals/run_evals.py --thresholds evals/thresholds.yml

Exits non-zero when any threshold fails, so CI can gate on it.
"""

import sys

from disputedesk.evals.runner import main

if __name__ == "__main__":
    sys.exit(main())
