"""Plain-text fallback composer."""
from __future__ import annotations

from daily_recon.report.html_compose import ReconReportData

_CHECK_LABELS = {
    "position_break": "Position Break",
}
_ORDER = ["position_break"]


def compose_text(data: ReconReportData) -> str:
    n = data.counts.get("position_break", 0)
    lines = [
        "Karbone Risk · Operations",
        f"RINs Reconciliation · {data.business_date.isoformat()}",
        f"Run: {data.run_id}  Prior: {data.prior_run_date or 'none'}",
        "",
        f"Position breaks: {n}",
        "",
    ]
    if n == 0:
        lines.append("No position breaks.")
    else:
        lines.append("See attached position_breaks.csv.")
    return "\n".join(lines)
