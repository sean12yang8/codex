from decimal import Decimal
from unittest.mock import patch
import pytest
from sqlalchemy import select
from backend.tests.test_workflow import setup, upload, dash
from backend.app import SessionLocal, Fact, Batch, Customer, Tenant
from backend.operations import Alert, normalize_csv, AzureFailure
from backend.worker import run_once, build_export, evaluate_alerts


def test_auto_queue_and_worker_discovery(client):
    customer, sub = setup(client)
    client.put(
        "/api/v1/tenants/" + sub["tenant_id"] + "/credentials",
        json={
            "client_id": "30000000-0000-4000-8000-000000000001",
            "client_secret": "only-fixture-not-live",
        },
    )
    jobs = client.get("/api/v1/sync/jobs").json()
    assert len(jobs) == 1
    with (
        patch("backend.worker.tenant_token", return_value="fixture-token"),
        patch(
            "backend.worker.discover",
            return_value=[{"id": sub["id"], "name": sub["name"], "status": "Enabled"}],
        ),
        patch(
            "backend.worker.query_cost",
            side_effect=lambda sid, token, start, end: [
                {
                    "subscription_id": sid,
                    "usage_date": start,
                    "service": "Compute",
                    "currency": "USD",
                    "amount": "100",
                }
            ],
        ),
    ):
        assert run_once()
    states = client.get("/api/v1/azure/states").json()
    assert any(
        s["id"] == sub["tenant_id"]
        and s["auth"] == "SUCCEEDED"
        and s["discovery"] == "SUCCEEDED"
        for s in states
    )
    with SessionLocal() as db:
        batch = db.scalar(select(Batch))
        assert batch.verified is False and batch.complete is False
    assert (
        client.get(
            "/api/v1/dashboard",
            params={"start": jobs[0]["start"], "end": jobs[0]["end"]},
        ).json()["total"]
        is None
    )


def test_job_retry_cancel_priority(client):
    _, sub = setup(client)
    data = {
        "scope_type": "tenant",
        "scope_id": sub["tenant_id"],
        "start": "2026-11-01",
        "end": "2026-11-02",
        "kind": "backfill",
    }
    a = client.post("/api/v1/sync/jobs", json=data).json()
    assert client.post("/api/v1/sync/jobs", json=data).json()["id"] == a["id"]
    data["kind"] = "daily"
    b = client.post("/api/v1/sync/jobs", json=data).json()
    with patch(
        "backend.worker.tenant_token", side_effect=AzureFailure("SP_AUTH_FAILED")
    ):
        assert run_once()
    jobs = client.get("/api/v1/sync/jobs").json()
    failed = next(j for j in jobs if j["id"] == b["id"])
    assert failed["state"] == "RETRY_WAIT" and failed["attempts"] == 1
    client.post("/api/v1/sync/jobs/" + a["id"] + "/cancel")
    run_once()
    assert (
        next(j for j in client.get("/api/v1/sync/jobs").json() if j["id"] == a["id"])[
            "state"
        ]
        == "CANCELLED"
    )


def test_user_disable_and_secret_redaction(client):
    u = client.post(
        "/api/v1/users",
        json={
            "email": "new@local.test",
            "name": "new",
            "password": "TestPasswordLong123",
            "role": "member",
        },
    ).json()
    assert "password" not in u
    assert (
        client.put("/api/v1/users/" + u["id"], json={"active": False}).status_code
        == 200
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "new@local.test", "password": "TestPasswordLong123"},
    )
    assert r.status_code == 401
    _, sub = setup(client)
    assert (
        client.put(
            "/api/v1/tenants/" + sub["tenant_id"] + "/cost-basis",
            json={"verified": True, "evidence": "signed fixture reconciliation"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/tenants/" + sub["tenant_id"] + "/cost-basis").json()[
        "verified"
    ]


def test_official_csv_normalization():
    a = b"SubscriptionId,Date,ServiceName,BillingCurrencyCode,CostInBillingCurrency\n20000000-0000-4000-8000-000000000001,11/01/2026,Compute,USD,0.1\n"
    b = b"SubscriptionId,UsageDateTime,MeterCategory,Currency,PreTaxCost\n20000000-0000-4000-8000-000000000001,2026-11-01T00:00:00Z,Compute,USD,0.1\n"
    assert normalize_csv(a) == normalize_csv(b)
    assert b"2026-11-01" in normalize_csv(a)
    with pytest.raises(AzureFailure):
        normalize_csv(b"garbage\n1\n")


def test_historical_ownership(client):
    old, sub = setup(client)
    upload(client, sub)
    new = client.post(
        "/api/v1/customers",
        json={"name": "New owner", "factor": "0.5", "effective_date": "2026-01-01"},
    ).json()
    t = client.post(
        "/api/v1/tenants",
        json={
            "id": "40000000-0000-4000-8000-000000000001",
            "customer_id": new["id"],
            "name": "New tenant",
        },
    ).json()
    assert (
        client.post(
            "/api/v1/subscriptions/" + sub["id"] + "/transfer",
            json={"tenant_id": t["id"], "effective_date": "2026-11-02"},
        ).status_code
        == 200
    )
    assert Decimal(dash(client, customer_id=old["id"])["total"]) == Decimal("85")
    assert Decimal(dash(client, customer_id=new["id"])["total"]) == Decimal("100")
    assert dash(client, customer_id=old["id"])["expected_days"] == 1
    from backend.report_query import rows

    with SessionLocal() as db:
        exported = list(
            rows(
                db,
                {
                    "dimension": "customer",
                    "granularity": "month",
                    "start": "2026-11-01",
                    "end": "2026-11-02",
                },
            )
        )
        headers = exported[0]
        records = [dict(zip(headers, r)) for r in exported[1:]]
        amounts = {r["customer_id"]: Decimal(r["acr_usd"]) for r in records}
        assert amounts == {old["id"]: Decimal("85"), new["id"]: Decimal("100")}
        timestamps = {b.created for b in db.scalars(select(Batch))}
        assert all(
            r["data_timestamp"] in timestamps
            and r["discount_effective"] == "2026-01-01"
            for r in records
        )


def test_async_excel_and_csv_export(client):
    _, sub = setup(client)
    upload(client, sub)
    for fmt in ["xlsx", "csv"]:
        task = client.post(
            "/api/v1/reports/tasks",
            json={
                "start": "2026-11-01",
                "end": "2026-11-02",
                "dimension": "service",
                "granularity": "month",
                "format": fmt,
            },
        ).json()
        build_export(task["id"])
        r = client.get("/api/v1/reports/tasks/" + task["id"] + "/download")
        assert r.status_code == 200
        if fmt == "xlsx":
            from openpyxl import load_workbook
            import io

            book = load_workbook(io.BytesIO(r.content))
            assert list(book.active.values)[1][-1] == "255.00"
        else:
            assert "255.00" in r.text


def test_purge_is_gated(client, monkeypatch):
    c, sub = setup(client)
    upload(client, sub)
    challenge = client.post("/api/v1/customers/" + c["id"] + "/purge-preview").json()
    assert challenge["facts"] == 2
    assert (
        client.post(
            "/api/v1/customers/" + c["id"] + "/purge",
            json={"challenge": challenge["challenge"], "confirm": c["id"]},
        ).status_code
        == 403
    )
    monkeypatch.setenv("PURGE_ENABLED", "true")
    assert (
        client.post(
            "/api/v1/customers/" + c["id"] + "/purge",
            json={"challenge": challenge["challenge"], "confirm": c["id"]},
        ).status_code
        == 200
    )

    assert not client.get("/api/v1/customers").json()
    assert not client.get("/api/v1/subscriptions").json()
    with SessionLocal() as db:
        assert not db.scalar(select(Fact)) and not db.scalar(select(Batch))


def test_non_admin_purge_is_denied_and_audited(client):
    c, _ = setup(client)
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "member@test.local", "password": "Test-password-123"},
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/customers/" + c["id"] + "/purge",
        json={"challenge": "irrelevant", "confirm": c["id"]},
    )
    assert response.status_code == 403
    from backend.app import Audit

    with SessionLocal() as db:
        entry = db.scalar(select(Audit).where(Audit.action == "DENY_ADMIN"))
        assert entry and entry.target == "POST /api/v1/customers/" + c["id"] + "/purge"
        assert db.get(Customer, c["id"])


def test_alert_thresholds_zero_and_dedup(client):
    from backend.worker import emit

    c, s = setup(client)
    with SessionLocal.begin() as db:
        a = emit(
            db,
            c["id"],
            "SPIKE",
            {"day": "2026-11-01", "current": "200", "baseline": "50"},
        )
        b = emit(
            db,
            c["id"],
            "SPIKE",
            {"day": "2026-11-02", "current": "220", "baseline": "50"},
        )
        assert a.id == b.id
    alert = client.get("/api/v1/alerts").json()[0]
    assert (
        client.post(
            "/api/v1/alerts/" + alert["id"] + "/actions", json={"action": "ack"}
        ).json()["state"]
        == "ACKNOWLEDGED"
    )
    assert (
        client.post(
            "/api/v1/alerts/" + alert["id"] + "/actions",
            json={"action": "mute", "mute_hours": 24},
        ).json()["state"]
        == "MUTED"
    )
    assert client.post(
        "/api/v1/alerts/" + alert["id"] + "/actions", json={"action": "read"}
    ).json()["read"]
    assert (
        client.post(
            "/api/v1/alerts/" + alert["id"] + "/actions", json={"action": "close"}
        ).json()["state"]
        == "CLOSED"
    )


def test_notification_secrets_and_sso_gate(client):
    body = {
        "email": {
            "enabled": True,
            "host": "smtp.example.com",
            "port": 587,
            "username": "u",
            "password": "sensitive-test-secret",
            "from": "sender@example.com",
            "to": ["receiver@example.com"],
        },
        "teams": {"enabled": False},
    }
    assert client.put("/api/v1/settings/notifications", json=body).status_code == 200
    response = client.get("/api/v1/settings/notifications")
    assert "sensitive-test-secret" not in response.text
    assert response.json()["email"]["password_configured"]
    assert client.get("/api/v1/auth/providers").json()["sso"] is False
    assert client.get("/api/v1/auth/sso/login").status_code == 400


def test_alert_boundary_conditions(client):
    c, s = setup(client)

    def report(values):
        return {
            "coverage": "COMPLETE_DECLARED",
            "trend": [{"value": str(v)} for v in values],
        }

    with SessionLocal.begin() as db:
        with patch("backend.worker.calculate", return_value=report([100] * 7 + [250])):
            evaluate_alerts(db)
        assert len(list(db.scalars(select(Alert)))) == 1
        a = db.scalar(select(Alert))
        assert a.rule == "SPIKE"
        with patch("backend.worker.calculate", return_value=report([100] * 7 + [200])):
            evaluate_alerts(db)
        assert a.state == "RESOLVED"  # difference exactly 100 must not trigger
        with patch("backend.worker.calculate", return_value=report([0] * 8)):
            evaluate_alerts(db)
        assert db.scalar(select(Alert).where(Alert.rule == "ZERO"))


def seed_resource_restore_fixture(subscription):
    from backend.app import Subscription
    from backend.resource_costs import publish_resources

    sid = subscription["id"]
    with SessionLocal.begin() as db:
        from backend.subscription_tiers import SubscriptionTier

        db.add(SubscriptionTier(subscription_id=sid, name="Tier 1", state="SUCCEEDED"))
        publish_resources(
            db,
            db.get(Subscription, sid),
            [
                dict(
                    subscription_id=sid,
                    usage_date="2026-11-01",
                    resource_id=f"/subscriptions/{sid}/resourceGroups/restore/providers/Microsoft.Compute/virtualMachines/fixture",
                    service="Compute",
                    currency="USD",
                    amount="100",
                )
            ],
        )


def test_restore_replays_external_deletion_ledger(client, monkeypatch, tmp_path):
    import sqlite3
    from backend.app import engine, URL, DATA
    from backend.operations import replay_tombstones

    if not URL.startswith("sqlite"):
        pytest.skip(
            "SQLite backup simulation; PostgreSQL restore is exercised separately"
        )
    c, s = setup(client)
    upload(client, s)
    seed_resource_restore_fixture(s)
    dbpath = URL.removeprefix("sqlite:///")
    snapshot = tmp_path / "old.db"
    with sqlite3.connect(dbpath) as src, sqlite3.connect(snapshot) as dst:
        src.backup(dst)
    challenge = client.post("/api/v1/customers/" + c["id"] + "/purge-preview").json()
    monkeypatch.setenv("PURGE_ENABLED", "true")
    assert (
        client.post(
            "/api/v1/customers/" + c["id"] + "/purge",
            json={"challenge": challenge["challenge"], "confirm": c["id"]},
        ).status_code
        == 200
    )
    assert (DATA / "deletion-ledger.jsonl").exists()
    engine.dispose()
    with sqlite3.connect(snapshot) as src, sqlite3.connect(dbpath) as dst:
        src.backup(dst)
    replay_tombstones()
    with SessionLocal() as db:
        assert db.get(Customer, c["id"]) is None
        assert not db.scalar(select(Fact))

    from backend.resource_costs import ResourceFact, ResourceBatch
    from backend.subscription_tiers import SubscriptionTier

    with SessionLocal() as db:
        assert not db.scalar(select(SubscriptionTier))
        assert not db.scalar(select(ResourceFact)) and not db.scalar(
            select(ResourceBatch)
        )


def test_onboarding_and_monthly_cache_invalidation(client):
    body = {
        "name": "Onboard",
        "factor": "0.85",
        "effective_date": "2026-01-01",
        "tenants": [
            {
                "id": "10000000-0000-4000-8000-000000000002",
                "client_id": "30000000-0000-4000-8000-000000000002",
                "client_secret": "test-only-no-real-access",
            }
        ],
    }
    r = client.post("/api/v1/customers/onboarding", json=body)
    assert r.status_code == 201, r.text
    assert len(r.json()["jobs"]) == 1
    assert client.post("/api/v1/customers/onboarding", json=body).status_code == 409
    _, s = setup(client)
    upload(client, s)
    result = client.get(
        "/api/v1/dashboard",
        params={"start": "2026-11-01", "end": "2026-11-02", "granularity": "month"},
    ).json()
    assert result["trend"] == [{"day": "2026-11", "value": "255.00"}]
    assert "report" not in result
    # A new discount invalidates previously materialized dashboard amounts.
    with SessionLocal() as db:
        cid = db.get(Tenant, s["tenant_id"]).customer_id
    client.post(
        "/api/v1/customers/" + cid + "/discounts",
        json={"factor": "0.5", "effective_date": "2026-11-02"},
    )
    assert Decimal(dash(client)["total"]) == Decimal("185")


def test_postgres_restore_replays_ledger(client, monkeypatch):
    import uuid
    from backend.app import engine, URL
    from backend.operations import replay_tombstones

    if URL.startswith("sqlite"):
        pytest.skip("Requires isolated PostgreSQL acceptance database")
    import psycopg
    from psycopg import sql

    target = engine.url.database
    if not target.startswith(("acr_tests", "test_")):
        pytest.skip("Destructive restore test requires a dedicated test database name")
    c, s = setup(client)
    upload(client, s)
    seed_resource_restore_fixture(s)
    engine.dispose()
    config = dict(
        host=engine.url.host,
        port=engine.url.port or 5432,
        user=engine.url.username,
        password=engine.url.password,
        dbname="postgres",
        autocommit=True,
    )
    snapshot = "test_snapshot_" + uuid.uuid4().hex
    with psycopg.connect(**config) as db:
        db.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(snapshot), sql.Identifier(target)
            )
        )
    challenge = client.post("/api/v1/customers/" + c["id"] + "/purge-preview").json()
    monkeypatch.setenv("PURGE_ENABLED", "true")
    assert (
        client.post(
            "/api/v1/customers/" + c["id"] + "/purge",
            json={"challenge": challenge["challenge"], "confirm": c["id"]},
        ).status_code
        == 200
    )
    engine.dispose()
    with psycopg.connect(**config) as db:
        db.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target))
        )
        db.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(target), sql.Identifier(snapshot)
            )
        )
        db.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(snapshot)))
    replay_tombstones()
    with SessionLocal() as db:
        assert db.get(Customer, c["id"]) is None and not db.scalar(select(Fact))

    from backend.resource_costs import ResourceFact, ResourceBatch
    from backend.subscription_tiers import SubscriptionTier

    with SessionLocal() as db:
        assert not db.scalar(select(SubscriptionTier))
        assert not db.scalar(select(ResourceFact)) and not db.scalar(
            select(ResourceBatch)
        )


def test_selective_purge_preserves_new_owner_and_blocks_history(client, monkeypatch):
    from backend.selective_purge import PurgedRange

    old, sub = setup(client)
    upload(client, sub)
    new = client.post(
        "/api/v1/customers",
        json={
            "name": "Retained owner",
            "factor": "0.5",
            "effective_date": "2026-01-01",
        },
    ).json()
    tenant = client.post(
        "/api/v1/tenants",
        json={
            "id": "40000000-0000-4000-8000-000000000003",
            "customer_id": new["id"],
            "name": "Retained tenant",
        },
    ).json()
    assert (
        client.post(
            "/api/v1/subscriptions/" + sub["id"] + "/transfer",
            json={"tenant_id": tenant["id"], "effective_date": "2026-11-02"},
        ).status_code
        == 200
    )
    preview = client.post("/api/v1/customers/" + old["id"] + "/purge-preview").json()
    assert preview["facts"] == 1
    monkeypatch.setenv("PURGE_ENABLED", "true")
    response = client.post(
        "/api/v1/customers/" + old["id"] + "/purge",
        json={"challenge": preview["challenge"], "confirm": old["id"]},
    )
    assert response.status_code == 200, response.text
    assert Decimal(dash(client, customer_id=new["id"])["total"]) == Decimal("100")
    assert dash(client, customer_id=new["id"])["expected_days"] == 1
    assert upload(client, sub, reason="must not resurrect").status_code == 400
    with SessionLocal() as db:
        assert db.get(Customer, old["id"]) is None
        assert db.scalar(
            select(PurgedRange).where(PurgedRange.subscription_id == sub["id"])
        )
        assert all(f.day == "2026-11-02" for f in db.scalars(select(Fact)))
        assert all("2026-11-01" not in b.rows for b in db.scalars(select(Batch)))


def test_zero_alert_requires_seven_not_eight_complete_days(client):
    from datetime import date

    c, s = setup(client)

    def sample(db, start, end, cid, **kwargs):
        count = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        return {
            "coverage": "PARTIAL" if count == 8 else "COMPLETE_DECLARED",
            "trend": [{"value": None}] + [{"value": "0"}] * 7
            if count == 8
            else [{"value": "0"}] * 7,
        }

    with SessionLocal.begin() as db:
        with patch("backend.worker.calculate", side_effect=sample):
            evaluate_alerts(db)
        assert db.scalar(select(Alert).where(Alert.rule == "ZERO"))
        assert not db.scalar(select(Alert).where(Alert.rule == "SPIKE"))


def test_sync_failure_grouped_by_customer_across_tenants(client):
    from backend.worker import evaluate_sync
    from backend.operations import Job
    from backend.app import User

    c, s = setup(client)
    second = client.post(
        "/api/v1/tenants",
        json={
            "id": "50000000-0000-4000-8000-000000000001",
            "customer_id": c["id"],
            "name": "Second tenant",
        },
    ).json()
    with SessionLocal.begin() as db:
        actor = db.scalar(select(User).where(User.role == "admin")).id
        for day, tid, status in [
            ("2026-11-01", s["tenant_id"], "FAILED"),
            ("2026-11-01", second["id"], "SUCCEEDED"),
            ("2026-11-02", s["tenant_id"], "SUCCEEDED"),
            ("2026-11-02", second["id"], "FAILED"),
        ]:
            db.add(
                Job(
                    kind="daily",
                    scope_type="tenant",
                    scope_id=tid,
                    start=day,
                    end=day,
                    state=status,
                    actor=actor,
                    created=day + "T03:00:00+00:00",
                )
            )
        db.flush()
        evaluate_sync(db, c["id"])
        assert db.scalar(select(Alert).where(Alert.rule == "SYNC"))


def test_delete_current_owner_preserves_prior_owner_archive(client, monkeypatch):
    prior, s = setup(client)
    upload(client, s)
    current = client.post(
        "/api/v1/customers",
        json={
            "name": "Current erased owner",
            "factor": "0.5",
            "effective_date": "2026-01-01",
        },
    ).json()
    t = client.post(
        "/api/v1/tenants",
        json={
            "id": "60000000-0000-4000-8000-000000000001",
            "customer_id": current["id"],
            "name": "Current tenant",
        },
    ).json()
    client.post(
        "/api/v1/subscriptions/" + s["id"] + "/transfer",
        json={"tenant_id": t["id"], "effective_date": "2026-11-02"},
    )
    preview = client.post(
        "/api/v1/customers/" + current["id"] + "/purge-preview"
    ).json()
    monkeypatch.setenv("PURGE_ENABLED", "true")
    r = client.post(
        "/api/v1/customers/" + current["id"] + "/purge",
        json={"challenge": preview["challenge"], "confirm": current["id"]},
    )
    assert r.status_code == 200, r.text
    assert Decimal(dash(client, customer_id=prior["id"])["total"]) == Decimal("85")
    with SessionLocal() as db:
        sub = db.get(
            __import__("backend.app", fromlist=["Subscription"]).Subscription, s["id"]
        )
        assert sub and sub.active is False and sub.tenant_id == s["tenant_id"]
        assert db.get(Customer, current["id"]) is None


def test_worker_restart_recovers_checkpoint_without_waiting_for_lease(client):
    from backend.worker import recover_inflight
    from backend.operations import Job
    from backend.app import User

    _, s = setup(client)
    with SessionLocal.begin() as db:
        actor = db.scalar(select(User).where(User.role == "admin")).id
        j = Job(
            kind="backfill",
            scope_type="subscription",
            scope_id=s["id"],
            start="2026-11-01",
            end="2026-11-02",
            state="RUNNING",
            actor=actor,
            checkpoint='{"done":1,"published":["retained-version"]}',
            lease_until="2099-01-01T00:00:00+00:00",
        )
        db.add(j)
        db.flush()
        recover_inflight(db)
        assert (
            j.state == "RETRY_WAIT"
            and "retained-version" in j.checkpoint
            and '"done":1' in j.checkpoint
        )


def test_source_refresh_schedule_is_idempotent(client):
    from backend.worker import schedule
    from backend.operations import Job, put_setting

    _, s = setup(client)
    client.put(
        "/api/v1/tenants/" + s["tenant_id"] + "/credentials",
        json={
            "client_id": "30000000-0000-4000-8000-000000000001",
            "client_secret": "fixture",
        },
    )
    with SessionLocal.begin() as db:
        put_setting(
            db, "schedule", {"enabled": True, "utc_time": "23:59", "refresh_hours": 6}
        )
        schedule(db)
        db.flush()
        before = len(list(db.scalars(select(Job))))
        schedule(db)
        db.flush()
        assert len(list(db.scalars(select(Job)))) == before
        assert db.scalar(select(Job).where(Job.kind == "refresh"))


def test_successful_refresh_resolves_sync_alert(client):
    from backend.worker import emit, recover_sync_if_healthy
    from backend.operations import state

    c, s = setup(client)
    with SessionLocal.begin() as db:
        alert = emit(db, c["id"], "SYNC", {"day": "2026-11-01"})
        state(db, s["id"]).sync = "SUCCEEDED"
        state(db, s["tenant_id"]).sync = "SUCCEEDED"
        recover_sync_if_healthy(db, c["id"])
        assert alert.state == "RESOLVED"
