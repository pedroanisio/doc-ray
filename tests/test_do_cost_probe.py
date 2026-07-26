"""Unit tests for the DigitalOcean billing probe (scripts/do_cost_probe.py).

The point under test is defensiveness: a changed or partial API payload must
produce a gap in the data, never an invented number.
"""

from __future__ import annotations

from pathlib import Path

import do_cost_probe as probe


def test_render_exports_every_known_balance_field() -> None:
    text = probe.render(
        {
            "month_to_date_usage": "412.55",
            "month_to_date_balance": "412.55",
            "account_balance": "-100.00",
        },
        now=1_800_000_000,
    )

    assert "digitalocean_month_to_date_usage_usd 412.55" in text
    assert "digitalocean_month_to_date_balance_usd 412.55" in text
    assert "digitalocean_account_balance_usd -100.0" in text
    assert "digitalocean_billing_scrape_success 1" in text
    assert "digitalocean_billing_scrape_timestamp_seconds 1800000000" in text


def test_render_skips_fields_the_payload_does_not_carry() -> None:
    # A payload that drops a field must drop the metric, not default it to zero:
    # a zeroed spend metric reads as "we spent nothing", which is a lie.
    text = probe.render({"month_to_date_usage": "10.00"}, now=1.0)

    assert "digitalocean_month_to_date_usage_usd 10.0" in text
    assert "digitalocean_account_balance_usd" not in text
    assert "digitalocean_billing_scrape_success 1" in text


def test_render_marks_a_failed_scrape_instead_of_going_silent() -> None:
    text = probe.render(None, now=1.0)

    assert "digitalocean_billing_scrape_success 0" in text
    assert "digitalocean_month_to_date_usage_usd" not in text
    # No timestamp on failure — a stale one would make the data look fresh.
    assert "digitalocean_billing_scrape_timestamp_seconds" not in text


def test_render_reports_failure_when_the_payload_has_no_usable_field() -> None:
    # A 200 response carrying an unrecognised shape is a failure to observe,
    # not a success with zero metrics.
    text = probe.render({"unexpected": "shape"}, now=1.0)

    assert "digitalocean_billing_scrape_success 0" in text


def test_numeric_accepts_api_strings_and_rejects_junk() -> None:
    assert probe._numeric("412.55") == 412.55
    assert probe._numeric(3) == 3.0
    assert probe._numeric(None) is None
    assert probe._numeric("n/a") is None
    # bool is an int subclass; True must not become 1.0 USD.
    assert probe._numeric(True) is None


def test_write_atomically_leaves_no_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "do_billing.prom"

    probe.write_atomically(target, "metric 1\n")
    probe.write_atomically(target, "metric 2\n")

    assert target.read_text(encoding="utf-8") == "metric 2\n"
    assert list(target.parent.glob("*.tmp")) == []


def test_main_exits_nonzero_without_a_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)

    assert probe.main(["--print"]) == 1
    assert "DIGITALOCEAN_TOKEN" in capsys.readouterr().err


def test_poll_once_writes_a_failure_metric_when_the_api_is_unreachable(
    tmp_path: Path, monkeypatch
) -> None:
    def _boom(_token: str, timeout: float = 20.0) -> dict[str, object]:
        raise OSError("connection refused")

    monkeypatch.setattr(probe, "fetch_balance", _boom)
    out = tmp_path / "do_billing.prom"

    assert probe.poll_once("token", out, echo=False) is False
    assert "digitalocean_billing_scrape_success 0" in out.read_text(encoding="utf-8")
