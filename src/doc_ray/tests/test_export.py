"""Tests for the DB export helpers that don't require a database."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from doc_ray.adapters.export import _json_default


def test_serializes_datetime_date_uuid_decimal():
    u = uuid.uuid4()
    payload = {
        "when": datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "day": date(2024, 1, 2),
        "id": u,
        "num": Decimal("3.5"),
    }
    out = json.loads(json.dumps(payload, default=_json_default))
    assert out["when"].startswith("2024-01-02T03:04:05")
    assert out["day"] == "2024-01-02"
    assert out["id"] == str(u)
    assert out["num"] == 3.5


def test_memoryview_decodes_to_text():
    assert _json_default(memoryview(b"hi")) == "hi"


def test_unknown_type_raises():
    with pytest.raises(TypeError):
        _json_default(object())
