"""Resource snapshots never double-count canonical service costs or cross ownership scopes."""

import json
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace
from sqlalchemy import select, func
from backend.tests.test_workflow import setup, upload, dash
from backend.app import SessionLocal, Subscription, Fact, Customer
from backend.operations import Job, AzureState, AzureFailure
from backend.resource_costs import (
    ResourceFact,
    ResourceBatch,
    ResourceCoverage,
    publish_resources,
    query_resources,
    resource_identity,
)
from backend.worker import run_once


def records(sid):
    rid = f"/subscriptions/{sid}/resourceGroups/rg-app/providers/Microsoft.Compute/virtualMachines/vm-app"
    return [
        dict(
            subscription_id=sid,
            usage_date="2026-11-01",
            resource_id=rid,
            service="Compute",
            currency="USD",
            amount="100",
        ),
        dict(
            subscription_id=sid,
            usage_date="2026-11-02",
            resource_id=rid,
            service="Compute",
            currency="USD",
            amount="190",
        ),
        dict(
            subscription_id=sid,
            usage_date="2026-11-02",
            resource_id="",
            service="Compute",
            currency="USD",
            amount="10",
        ),
    ]


def publish_fixture(client):
    c, sub = setup(client)
    upload(client, sub, verified="false")
    with SessionLocal.begin() as db:
        publish_resources(db, db.get(Subscription, sub["id"]), records(sub["id"]))
    return c, sub


def test_subscription_statistics_empty_subscription_and_resource_reconciliation(client):
    c, sub = publish_fixture(client)
    empty = client.post(
        "/api/v1/subscriptions",
        json={
            "id": "50000000-0000-4000-8000-000000000001",
            "tenant_id": sub["tenant_id"],
            "name": "No data",
        },
    ).json()
    params = {"start": "2026-11-01", "end": "2026-11-02", "customer_id": c["id"]}
    rows = client.get("/api/v1/subscriptions/consumption", params=params).json()
    assert rows["total"] == 2
    row = next(r for r in rows["items"] if r["subscription_id"] == sub["id"])
    none = next(r for r in rows["items"] if r["subscription_id"] == empty["id"])
    assert (
        Decimal(row["raw_costs"][0]["amount"]) == 300
        and Decimal(row["trial_usd"]) == 255
        and row["verified_usd"] is None
        and row["received_days"] == 2
    )
    assert none["raw_costs"] == [] and none["trial_usd"] is None
    data = client.get(
        "/api/v1/resource-costs", params={**params, "subscription_id": sub["id"]}
    ).json()
    assert data["total"] == 2 and data["reconciliation"] == "MATCHED"
    vm = next(r for r in data["items"] if r["allocated"])
    unallocated = next(r for r in data["items"] if not r["allocated"])
    assert (
        vm["name"] == "vm-app"
        and vm["resource_group"] == "rg-app"
        and vm["resource_type"] == "Microsoft.Compute/virtualMachines"
    )
    assert (
        Decimal(vm["raw_costs"][0]["amount"]) == 290
        and Decimal(unallocated["raw_costs"][0]["amount"]) == 10
    )
    details = client.get(
        "/api/v1/resource-costs/" + vm["resource_key"] + "/details",
        params={**params, "subscription_id": sub["id"]},
    ).json()
    assert details["total"] == 2 and Decimal(
        details["items"][0]["trial_usd"]
    ) == Decimal("161.5")
    assert details["items"][0]["verified_usd"] is None
    assert Decimal(dash(client)["raw"]["currencies"][0]["total"]) == 300
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(Fact)) == 2
            and db.scalar(select(func.count()).select_from(ResourceFact)) == 3
        )


def test_resource_day_replacement_idempotency_and_purge_history(client, monkeypatch):
    c, sub = publish_fixture(client)
    with SessionLocal.begin() as db:
        entity = db.get(Subscription, sub["id"])
        publish_resources(db, entity, records(sub["id"]))
        assert db.scalar(select(func.count()).select_from(ResourceBatch)) == 2
        changed = [
            {**r, "amount": "180"}
            for r in records(sub["id"])
            if r["usage_date"] == "2026-11-02" and r["resource_id"]
        ]
        publish_resources(db, entity, changed)
    data = client.get(
        "/api/v1/resource-costs",
        params={
            "start": "2026-11-01",
            "end": "2026-11-02",
            "subscription_id": sub["id"],
        },
    ).json()
    assert (
        data["total"] == 1
        and Decimal(data["totals"][0]["amount"]) == 280
        and data["reconciliation"] == "DIFFERENT_SNAPSHOT"
    )
    new = client.post(
        "/api/v1/customers",
        json={"name": "New owner", "factor": "0.5", "effective_date": "2026-01-01"},
    ).json()
    tenant = client.post(
        "/api/v1/tenants",
        json={
            "id": "40000000-0000-4000-8000-000000000001",
            "customer_id": new["id"],
            "name": "New tenant",
        },
    ).json()
    client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/transfer",
        json={"tenant_id": tenant["id"], "effective_date": "2026-11-02"},
    )
    monkeypatch.setenv("PURGE_ENABLED", "true")
    preview = client.post("/api/v1/customers/" + c["id"] + "/purge-preview").json()
    assert (
        client.post(
            "/api/v1/customers/" + c["id"] + "/purge",
            json={"challenge": preview["challenge"], "confirm": c["id"]},
        ).status_code
        == 200
    )
    data = client.get(
        "/api/v1/resource-costs",
        params={
            "start": "2026-11-01",
            "end": "2026-11-02",
            "subscription_id": sub["id"],
        },
    ).json()
    assert Decimal(data["totals"][0]["amount"]) == 180
    with SessionLocal() as db:
        assert not list(
            db.scalars(select(ResourceBatch).where(ResourceBatch.day == "2026-11-01"))
        )
        publish_resources(db, db.get(Subscription, sub["id"]), records(sub["id"]))
        assert not list(
            db.scalars(select(ResourceFact).where(ResourceFact.day == "2026-11-01"))
        )
    # Full scope cascade must also remove supplementary resource FKs and raw snapshots.
    preview = client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/purge-preview"
    ).json()
    assert (
        client.post(
            "/api/v1/subscriptions/" + sub["id"] + "/purge",
            json={"challenge": preview["challenge"], "confirm": sub["id"]},
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert (
            not list(db.scalars(select(ResourceFact)))
            and not list(db.scalars(select(ResourceBatch)))
            and not list(db.scalars(select(ResourceCoverage)))
        )
        assert db.get(Customer, new["id"])


def test_resource_worker_success_and_failure_are_isolated(client):
    _, sub = setup(client)
    with SessionLocal.begin() as db:
        db.add(AzureState(id=sub["id"], sync="SUCCEEDED", cost="SUCCEEDED"))
    request = {"start": "2026-11-01", "end": "2026-11-02"}
    job = client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/resource-costs/sync", json=request
    ).json()
    assert (
        client.post(
            "/api/v1/subscriptions/" + sub["id"] + "/resource-costs/sync", json=request
        ).json()["job_id"]
        == job["job_id"]
    )
    with (
        patch("backend.worker.tenant_token", return_value="fixture"),
        patch(
            "backend.resource_costs.query_resources", return_value=records(sub["id"])
        ),
    ):
        assert run_once()
    with SessionLocal() as db:
        assert db.get(Job, job["job_id"]).state == "SUCCEEDED"
        assert (
            db.scalar(select(func.count()).select_from(Fact)) == 0
            and db.scalar(select(func.count()).select_from(ResourceFact)) == 3
        )
    second = client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/resource-costs/sync", json=request
    ).json()
    with (
        patch("backend.worker.tenant_token", return_value="fixture"),
        patch(
            "backend.resource_costs.query_resources",
            side_effect=AzureFailure("AZURE_HTTP_429", 180),
        ),
    ):
        run_once()
    with SessionLocal() as db:
        assert db.get(Job, second["job_id"]).state == "RETRY_WAIT"
        assert db.get(AzureState, sub["id"]).sync == "SUCCEEDED"


def test_resource_azure_contract_preserves_precision_and_unassigned_costs():
    sid = "20000000-0000-4000-8000-000000000001"
    raw = records(sid)[0]
    text = (
        '{"properties":{"columns":[{"name":"Cost"},{"name":"UsageDate"},{"name":"ResourceId"},{"name":"ServiceName"},{"name":"Currency"}],"rows":[[0.123456789012345678901,20261101,'
        + json.dumps(raw["resource_id"])
        + ',"Compute","USD"],[1,20261101,null,"Shared","USD"]]}}'
    )
    response = SimpleNamespace(ok=True, status_code=200, headers={}, text=text)
    with patch("backend.operations.requests.request", return_value=response) as call:
        result = query_resources(sid, "fixture", "2026-11-01", "2026-11-02")
        assert (
            result[0]["amount"] == "0.123456789012345678901"
            and result[1]["resource_id"] == ""
        )
        assert [
            g["name"] for g in call.call_args.kwargs["json"]["dataset"]["grouping"]
        ] == ["ResourceId", "ServiceName"]
    assert (
        resource_identity(raw["resource_id"].upper())["resource_key"]
        == resource_identity(raw["resource_id"])["resource_key"]
    )
