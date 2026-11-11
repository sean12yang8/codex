"""Azure response contracts through the worker, rather than mocked normalized costs."""

import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from backend.app import SessionLocal, Fact, Batch, dec
from backend.operations import (
    AzureFailure,
    Job,
    azure_request,
    azure_retry_after,
    normalize_csv,
    query_cost,
)
from backend.worker import run_once
from backend.tests.test_workflow import setup, upload, dash


SUB_ID = "20000000-0000-4000-8000-000000000001"


def response(rows):
    # Parse actual JSON numeric literals without passing them through Python floats.
    return SimpleNamespace(
        ok=True,
        status_code=200,
        headers={},
        text='{"properties":{"columns":[{"name":"Cost","type":"Number"},{"name":"UsageDate","type":"Number"},{"name":"ServiceName","type":"String"},{"name":"Currency","type":"String"}],"rows":['
        + rows
        + "]}}",
    )


def test_high_precision_api_worker_and_csv_round_trip(client):
    _, sub = setup(client)
    a, b = "1.12345678901234567890123456789", "-0.000000000000987654321"
    client.put(
        "/api/v1/tenants/" + sub["tenant_id"] + "/credentials",
        json={
            "client_id": "30000000-0000-4000-8000-000000000001",
            "client_secret": "fixture-only",
        },
    )
    client.put(
        "/api/v1/tenants/" + sub["tenant_id"] + "/cost-basis",
        json={"verified": True, "evidence": "test fixture reconciliation"},
    )
    # Explicit dates isolate the ingestion contract from today's default lookback.
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job))
        job.start = "2026-11-01"
        job.end = "2026-11-02"
    with (
        patch("backend.worker.tenant_token", return_value="fixture-token"),
        patch(
            "backend.worker.discover",
            return_value=[{"id": sub["id"], "name": sub["name"], "status": "Enabled"}],
        ),
        patch(
            "backend.operations.requests.request",
            return_value=response(
                f'[{a},20261101,"Compute","USD"],[{b},20261102,"Storage","USD"]'
            ),
        ) as request,
    ):
        assert run_once()
        assert request.call_args.kwargs["headers"]["ClientType"] == "AzureLens"
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.kind == "verify"))
        assert job.state == "SUCCEEDED" and job.error == ""
        assert [
            Decimal(f.amount) for f in db.scalars(select(Fact).order_by(Fact.day))
        ] == [Decimal(a), Decimal(b)]
        raw = json.loads(db.scalar(select(Batch)).rows)
        assert [Decimal(r["amount"]) for r in raw] == [Decimal(a), Decimal(b)]
    expected = (Decimal(a) + Decimal(b)) * Decimal("0.85")
    assert Decimal(dash(client)["total"]) == expected
    # Both supported Azure CSV and standard CSV must preserve the same source precision.
    data = f"SubscriptionId,Date,ServiceName,BillingCurrencyCode,CostInBillingCurrency\n{SUB_ID},11/01/2026,Compute,USD,{a}\n".encode()
    assert a.encode() in normalize_csv(data)
    result = upload(
        client,
        sub,
        rows=f"2026-11-01,Compute,USD,{a}\n2026-11-02,Storage,USD,{b}",
        reason="reconciled complete CSV",
    )
    assert result.status_code == 200, result.text
    assert Decimal(dash(client)["total"]) == expected
    # Raising the raw-cost precision does not weaken configuration validation.
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        dec("0.1234567890123")


def test_invalid_azure_amount_is_typed_not_an_internal_failure():
    for value in ['"NaN"', '"Infinity"', "1000000000000001"]:
        with patch(
            "backend.operations.requests.request",
            return_value=response(f'[{value},20261101,"Compute","USD"]'),
        ):
            with pytest.raises(AzureFailure, match="COST_INVALID_AMOUNT"):
                query_cost(SUB_ID, "fixture-token", "2026-11-01", "2026-11-02")


def test_rate_limit_hints_and_safe_client_identifier():
    headers = {
        "Retry-After": "15",
        "x-ms-ratelimit-microsoft.costmanagement-clienttype-retry-after": "75",
        "X-MS-RATELIMIT-MICROSOFT.COSTMANAGEMENT-QPU-RETRY-AFTER": "180",
    }
    throttled = SimpleNamespace(ok=False, status_code=429, headers=headers)
    with patch(
        "backend.operations.requests.request", return_value=throttled
    ) as request:
        with pytest.raises(AzureFailure) as error:
            azure_request(
                "POST",
                f"https://management.azure.com/subscriptions/{SUB_ID}/providers/Microsoft.CostManagement/query?api-version=2023-03-01",
                "fixture-token",
                json={},
            )
        assert error.value.code == "AZURE_HTTP_429" and error.value.retry_after == 180
        assert request.call_args.kwargs["headers"]["ClientType"] == "AzureLens"
    assert azure_retry_after({}, 429) == 60
    assert azure_retry_after({"Retry-After": "nonsense"}, 429) == 60
    future = format_datetime(
        datetime.now(timezone.utc) + timedelta(seconds=180), usegmt=True
    )
    assert 178 <= azure_retry_after({"Retry-After": future}, 503) <= 180


def test_worker_persists_throttle_deadline_and_safe_unexpected_diagnostics(
    client, caplog
):
    _, sub = setup(client)
    job = client.post(
        "/api/v1/sync/jobs",
        json={
            "scope_type": "subscription",
            "scope_id": sub["id"],
            "kind": "sync",
            "start": "2026-11-01",
            "end": "2026-11-02",
        },
    ).json()
    with patch(
        "backend.worker.process_partition",
        side_effect=AzureFailure("AZURE_HTTP_429", 180),
    ):
        run_once()
    with SessionLocal.begin() as db:
        task = db.get(Job, job["id"])
        assert task.state == "RETRY_WAIT" and task.attempts == 1
        assert (
            datetime.fromisoformat(task.available) - datetime.now(timezone.utc)
        ).total_seconds() > 178
        task.available = "2020-01-01T00:00:00+00:00"
    with (
        caplog.at_level(logging.ERROR),
        patch(
            "backend.worker.process_partition",
            side_effect=RuntimeError("secret=fixture-should-never-be-logged"),
        ),
    ):
        run_once()
    assert "RuntimeError" in caplog.text and job["id"] in caplog.text
    assert "fixture-should-never-be-logged" not in caplog.text
