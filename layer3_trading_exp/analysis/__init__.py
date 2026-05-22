"""Phase 1.5 — End-of-run analysis framework.

Tooling that Jason runs at end of the 30-day experiment to evaluate H1/H2/H3
against the JSONL opportunity log. Each module exposes a pure `compute_*`
function (returns a stats dict) plus a `plot_*` function (writes a PNG).
`generate_report` consumes the stats dicts and produces a Markdown report
template with `TO BE COMPLETED BY JASON` placeholders for interpretation.

Tooling, not analysis. Jason annotates the conclusions.
"""

from . import _loader
from . import h1_opportunity_rate
from . import h2_filter_activation
from . import h3_distributional_comparison
from . import generate_report

__all__ = [
    "_loader",
    "h1_opportunity_rate",
    "h2_filter_activation",
    "h3_distributional_comparison",
    "generate_report",
]
