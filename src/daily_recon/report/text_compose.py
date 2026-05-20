"""Plain-text fallback composer."""
from __future__ import annotations

from daily_recon.report.html_compose import ReconReportData

_CHECK_LABELS = {
    "trade_drift": "Trade Drift",
    "historical_position_drift": "Historical Position Drift",
    "position_break": "Position Break",
    "prior_day_trades": "T-1 Trades",
}
_ORDER = ["position_break", "prior_day_trades", "trade_drift", "historical_position_drift"]


def compose_text(data: ReconReportData) -> str:
    total = sum(data.counts.values())
    lines = [
        "Karbone Risk · Operations",
        f"RINs Reconciliation · {data.business_date.isoformat()}",
        f"Run: {data.run_id}  Prior: {data.prior_run_date or 'none'}",
        "",
        f"Total exceptions: {total}",
        "",
        "Counts by check:",
    ]
    for k in _ORDER:
        lines.append(f"  {_CHECK_LABELS[k]}: {data.counts.get(k, 0)}")
    lines.append("")
    if total == 0:
        lines.append("All checks clean.")
    else:
        lines.append("See attached CSVs for full exception detail.")
    return "\n".join(lines)
