#!/usr/bin/env python3
"""Export DigitalOcean month-to-date spend as Prometheus textfile metrics.

The infrastructure exporters report what EXISTS; this reports what it COSTS.
Both are needed: a droplet that is powered off still bills, and an idle GPU bills
identically to a busy one, so inventory alone never answers "what am I spending".

Written as a textfile-collector producer rather than an HTTP exporter so it needs
no web framework and no port: it writes a ``.prom`` file that node-exporter
already serves. Stdlib only, so it runs in the app image unmodified.

PALS'S LAW APPLIES TO APIS TOO. The response shape is not assumed: every field is
looked up defensively and a key that is missing or non-numeric is simply not
exported. A cost dashboard that invents a number because a payload changed is
worse than one with a gap — the gap is visible, the invention is not.

Usage::

    export DIGITALOCEAN_TOKEN=dop_v1_...          # read scope is enough
    python scripts/do_cost_probe.py --out /textfile/do_billing.prom
    python scripts/do_cost_probe.py --loop 900 --out /textfile/do_billing.prom
    python scripts/do_cost_probe.py --print       # stdout, no file

Exit codes: 0 = metrics written; 1 = no token; 2 = the API call failed (one-shot
mode only — in ``--loop`` mode a failed poll is logged and retried).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BALANCE_URL = "https://api.digitalocean.com/v2/customers/my/balance"

#: Balance fields worth exporting, mapped to their metric name. Anything absent
#: from the payload is skipped rather than defaulted — see PALS's Law above.
_BALANCE_FIELDS = {
    "month_to_date_usage": "digitalocean_month_to_date_usage_usd",
    "month_to_date_balance": "digitalocean_month_to_date_balance_usd",
    "account_balance": "digitalocean_account_balance_usd",
}

_HELP = {
    "digitalocean_month_to_date_usage_usd": "Usage charged this billing period, USD",
    "digitalocean_month_to_date_balance_usd": "Balance including this period's usage, USD",
    "digitalocean_account_balance_usd": "Account balance carried from prior periods, USD",
    "digitalocean_billing_scrape_success": "1 when the last billing API call succeeded",
    "digitalocean_billing_scrape_timestamp_seconds": "Unix time of the last successful scrape",
}


def fetch_balance(token: str, timeout: float = 20.0) -> dict[str, Any]:
    """Fetch the account balance payload from the DigitalOcean API.

    Args:
        token: A DigitalOcean API token; read scope suffices.
        timeout: Socket timeout in seconds.

    Returns:
        The decoded JSON object.

    Raises:
        urllib.error.URLError: The request failed or timed out.
        ValueError: The response was not a JSON object.
    """
    request = urllib.request.Request(
        BALANCE_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("balance response was not a JSON object")
    return payload


def _numeric(value: Any) -> float | None:
    """Coerce an API field to float, or ``None`` when it is not a number.

    DigitalOcean returns these as strings ("12.34"); a future payload might not.
    Anything that does not parse is dropped rather than guessed.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def render(payload: dict[str, Any] | None, now: float) -> str:
    """Render the Prometheus exposition text for *payload*.

    Args:
        payload: The balance response, or ``None`` when the call failed.
        now: Unix timestamp to stamp a successful scrape with.

    Returns:
        Exposition-format text, always including the scrape-success metric so a
        broken probe is visible as a value rather than as missing data.
    """
    lines: list[str] = []
    exported = 0
    for field, metric in _BALANCE_FIELDS.items():
        value = _numeric((payload or {}).get(field))
        if value is None:
            continue
        lines += [f"# HELP {metric} {_HELP[metric]}", f"# TYPE {metric} gauge", f"{metric} {value}"]
        exported += 1
    success = 1 if payload is not None and exported else 0
    name = "digitalocean_billing_scrape_success"
    lines += [f"# HELP {name} {_HELP[name]}", f"# TYPE {name} gauge", f"{name} {success}"]
    if success:
        stamp = "digitalocean_billing_scrape_timestamp_seconds"
        lines += [
            f"# HELP {stamp} {_HELP[stamp]}",
            f"# TYPE {stamp} gauge",
            f"{stamp} {now:.0f}",
        ]
    return "\n".join(lines) + "\n"


def write_atomically(path: Path, text: str) -> None:
    """Write *text* to *path* via a temp file + rename.

    node-exporter reads this file on its own schedule; a partial write would be
    parsed as a truncated metric set. Rename within the same directory is atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def poll_once(token: str, out: Path | None, echo: bool) -> bool:
    """Run one probe; return whether the API call succeeded."""
    try:
        payload: dict[str, Any] | None = fetch_balance(token)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        print(f"digitalocean billing probe failed: {exc}", file=sys.stderr)
        payload = None
    text = render(payload, time.time())
    if out is not None:
        write_atomically(out, text)
    if echo or out is None:
        print(text, end="")
    return payload is not None


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="do_cost_probe", description="Export DigitalOcean spend as Prometheus metrics."
    )
    parser.add_argument("--out", default=None, help="Textfile path to write (.prom).")
    parser.add_argument(
        "--loop",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Poll forever at this interval instead of exiting (billing APIs are rate limited).",
    )
    parser.add_argument("--print", action="store_true", help="Also echo the metrics to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Probe DigitalOcean billing once, or on a loop."""
    args = build_parser().parse_args(argv)
    token = os.environ.get("DIGITALOCEAN_TOKEN", "").strip()
    if not token:
        print("fatal: DIGITALOCEAN_TOKEN is not set", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else None
    if args.loop is None:
        return 0 if poll_once(token, out, args.print) else 2
    interval = max(60.0, args.loop)
    while True:
        poll_once(token, out, args.print)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
