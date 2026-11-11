"""Official tier reads, durable retries and deletion remain isolated from costs."""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, func
from backend.tests.test_workflow import setup
from backend.app import SessionLocal, Tenant, Subscription, User, Fact, cipher
from backend.operations import Job, AzureState, AzureFailure, setting
from backend.subscription_tiers import SubscriptionTier, read_tier, queue
from backend.worker import run_once

SID = "20000000-0000-4000-8000-000000000001"


def response(name="Tier 1"):
    return {
        "id": f"/subscriptions/{SID}/providers/Microsoft.CognitiveServices/quotaTiers/default",
        "type": "Microsoft.CognitiveServices/quotaTiers",
        "properties": {
            "currentTierName": name,
            "assignmentDate": "2026-02-26T11:57:51.9516626Z",
            "tierUpgradeEligibilityInfo": {"nextTierName": "Tier-5"},
        },
    }


def fixture(client):
    _, sub = setup(client)
    with SessionLocal.begin() as db:
        t = db.get(Tenant, sub["tenant_id"])
        t.client_id = "30000000-0000-4000-8000-000000000001"
        t.secret = cipher().encrypt(b"local-tier-test-only").decode()
        db.add(AzureState(id=sub["id"], sync="SUCCEEDED", cost="SUCCEEDED"))
    return sub


def request(client, sub):
    result = client.post("/api/v1/subscriptions/" + sub["id"] + "/tier/refresh")
    assert result.status_code == 202, result.text
    return result.json()


def test_official_tier_is_read_only_current_field_and_exact_scope():
    with patch(
        "backend.subscription_tiers.azure_request", return_value=response("Free-Tier")
    ) as call:
        assert read_tier(SID, "fixture")["name"] == "Free-Tier"
        call.assert_called_once_with(
            "GET",
            f"https://management.azure.com/subscriptions/{SID}/providers/Microsoft.CognitiveServices/quotaTiers/default?api-version=2025-12-01",
            "fixture",
        )
    for doc, code in [
        (
            {**response(), "id": response()["id"].replace(SID, "other")},
            "TIER_SCOPE_MISMATCH",
        ),
        (
            {
                **response(),
                "properties": {
                    "tierUpgradeEligibilityInfo": {"nextTierName": "Tier-1"}
                },
            },
            "TIER_INVALID_RESPONSE",
        ),
        (
            {**response(), "properties": {"currentTierName": ""}},
            "TIER_INVALID_RESPONSE",
        ),
    ]:
        with (
            patch("backend.subscription_tiers.azure_request", return_value=doc),
            pytest.raises(AzureFailure) as error,
        ):
            read_tier(SID, "fixture")
        assert error.value.code == code
    for original, mapped in [
        ("COST_ACCESS_DENIED", "TIER_ACCESS_DENIED"),
        ("AZURE_HTTP_404", "TIER_NOT_AVAILABLE"),
    ]:
        with (
            patch(
                "backend.subscription_tiers.azure_request",
                side_effect=AzureFailure(original),
            ),
            pytest.raises(AzureFailure) as error,
        ):
            read_tier(SID, "fixture")
        assert error.value.code == mapped


def test_tier_job_cache_api_and_no_financial_invalidation(client):
    sub = fixture(client)
    with SessionLocal() as db:
        revision = setting(db, "data_revision")
    job = request(client, sub)
    assert request(client, sub)["job_id"] == job["job_id"]
    with (
        patch("backend.subscription_tiers.tenant_token", return_value="fixture"),
        patch(
            "backend.subscription_tiers.read_tier",
            return_value={"name": "Tier 1", "assignment_date": "2026-02-26T11:57:51Z"},
        ),
    ):
        assert run_once()
    data = next(
        s for s in client.get("/api/v1/subscriptions").json() if s["id"] == sub["id"]
    )
    assert data["tier"]["name"] == "Tier 1" and data["tier"]["state"] == "SUCCEEDED"
    page = client.get(
        "/api/v1/catalog/subscriptions", params={"search": sub["id"]}
    ).json()
    assert page["items"][0]["tier"]["name"] == "Tier 1"
    with SessionLocal.begin() as db:
        assert db.get(Job, job["job_id"]).state == "SUCCEEDED"
        assert db.get(AzureState, sub["id"]).sync == "SUCCEEDED"
        assert db.scalar(select(func.count()).select_from(Fact)) == 0
        assert setting(db, "data_revision") == revision
        owner = db.scalar(select(User).where(User.role == "admin"))
        assert queue(db, owner, db.get(Subscription, sub["id"])) is None
        assert queue(db, owner, db.get(Subscription, sub["id"]), force=True)


def test_tier_retries_preserve_previous_value_without_cost_failure(client):
    sub = fixture(client)
    with SessionLocal.begin() as db:
        db.add(
            SubscriptionTier(
                subscription_id=sub["id"],
                name="Tier 1",
                state="SUCCEEDED",
                last_success="2026-11-01T00:00:00+00:00",
            )
        )
    job = request(client, sub)
    before = datetime.now(timezone.utc)
    with (
        patch("backend.subscription_tiers.tenant_token", return_value="fixture"),
        patch(
            "backend.subscription_tiers.read_tier",
            side_effect=AzureFailure("AZURE_HTTP_429", 180),
        ),
    ):
        run_once()
    with SessionLocal.begin() as db:
        j = db.get(Job, job["job_id"])
        assert j.state == "RETRY_WAIT" and datetime.fromisoformat(
            j.available
        ) >= before + timedelta(seconds=180)
        assert db.get(SubscriptionTier, sub["id"]).name == "Tier 1"
        assert db.get(AzureState, sub["id"]).sync == "SUCCEEDED"
        j.attempts = 3
        j.available = before.isoformat()
    with (
        patch("backend.subscription_tiers.tenant_token", return_value="fixture"),
        patch(
            "backend.subscription_tiers.read_tier",
            side_effect=AzureFailure("TIER_ACCESS_DENIED"),
        ),
    ):
        run_once()
    with SessionLocal() as db:
        tier = db.get(SubscriptionTier, sub["id"])
        assert (
            tier.name == "Tier 1"
            and tier.state == "FAILED"
            and tier.error == "TIER_ACCESS_DENIED"
        )
        assert db.get(AzureState, sub["id"]).cost == "SUCCEEDED"


def test_cancel_and_full_purge_remove_tier_metadata(client, monkeypatch):
    sub = fixture(client)
    job = request(client, sub)
    client.post("/api/v1/sync/jobs/" + job["job_id"] + "/cancel")
    with patch("backend.subscription_tiers.read_tier") as read:
        run_once()
        read.assert_not_called()
    with SessionLocal() as db:
        assert db.get(SubscriptionTier, sub["id"]).state == "CANCELLED"
    monkeypatch.setenv("PURGE_ENABLED", "true")
    preview = client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/purge-preview"
    ).json()
    result = client.post(
        "/api/v1/subscriptions/" + sub["id"] + "/purge",
        json={"challenge": preview["challenge"], "confirm": sub["id"]},
    )
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert db.get(SubscriptionTier, sub["id"]) is None


def test_subscription_sync_queues_independent_tier_read(client):
    sub = fixture(client)
    result = client.post("/api/v1/subscriptions/" + sub["id"] + "/verify")
    assert result.status_code == 202
    with (
        patch("backend.worker.tenant_token", return_value="fixture"),
        patch(
            "backend.worker.discover",
            return_value=[{"id": sub["id"], "name": sub["name"]}],
        ),
        patch("backend.worker.query_cost", return_value=[]),
    ):
        run_once()
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.kind == "tier")))
        assert len(jobs) == 1 and jobs[0].state == "QUEUED"
        assert db.get(SubscriptionTier, sub["id"]).state == "QUEUED"
