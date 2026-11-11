import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="acr-test-")
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from backend.app import (
    app,
    SessionLocal,
    User,
    Fact,
    Batch,
    Tenant,
    hash_password,
    login_attempts,
)


@pytest.fixture
def client():
    from backend.app import DATA

    (DATA / "deletion-ledger.jsonl").unlink(missing_ok=True)
    with SessionLocal.begin() as db:
        from backend.app import Base, engine

        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        db.add(
            User(
                email="admin@test.local",
                name="Admin",
                password=hash_password("Test-password-123"),
                role="admin",
            )
        )
        db.add(
            User(
                email="member@test.local",
                name="Member",
                password=hash_password("Test-password-123"),
                role="member",
            )
        )
    login_attempts.clear()
    with TestClient(app, base_url="https://testserver") as c:
        assert (
            c.post(
                "/api/v1/auth/login",
                json={"email": "admin@test.local", "password": "Test-password-123"},
            ).status_code
            == 200
        )
        yield c


def setup(c):
    customer = c.post(
        "/api/v1/customers",
        json={
            "name": "Test customer",
            "factor": "0.85",
            "effective_date": "2026-01-01",
        },
    ).json()
    t = c.post(
        "/api/v1/tenants",
        json={
            "id": "10000000-0000-4000-8000-000000000001",
            "customer_id": customer["id"],
            "name": "Test tenant",
        },
    )
    assert t.status_code == 201, t.text
    s = c.post(
        "/api/v1/subscriptions",
        json={
            "id": "20000000-0000-4000-8000-000000000001",
            "tenant_id": t.json()["id"],
            "name": "Production",
        },
    )
    assert s.status_code == 201, s.text
    return customer, s.json()


def upload(c, s, rows="2026-11-01,Compute,USD,100\n2026-11-02,Compute,USD,200", **kw):
    data = {
        "subscription_id": s["id"],
        "start": "2026-11-01",
        "end": "2026-11-02",
        "verified": "true",
        "complete": "true",
        "evidence": "Price basis and full coverage checked against fixture",
        **kw,
    }
    content = (
        "subscription_id,usage_date,service,currency,amount\n"
        + "\n".join(s["id"] + "," + r for r in rows.splitlines())
        + "\n"
    )
    return c.post(
        "/api/v1/sync/imports",
        data=data,
        files={"file": ("cost.csv", content, "text/csv")},
    )


def dash(c, **kw):
    return c.get(
        "/api/v1/dashboard", params={"start": "2026-11-01", "end": "2026-11-02", **kw}
    ).json()


def test_full_workflow_and_idempotency(client):
    customer, s = setup(client)
    r = upload(client, s)
    assert r.status_code == 200, r.text
    assert Decimal(dash(client)["total"]) == Decimal("255")
    assert dash(client)["coverage"] == "COMPLETE_DECLARED"
    assert upload(client, s).json()["duplicate"] is True
    assert Decimal(dash(client)["total"]) == Decimal("255")
    assert (
        client.get("/api/v1/reports/export?start=2026-11-01&end=2026-11-02").text.count(
            "255"
        )
        == 0
    )  # rows are 85 + 170
    assert (
        "170.00"
        in client.get("/api/v1/reports/export?start=2026-11-01&end=2026-11-02").text
    )


def test_historical_discount_and_fx(client):
    customer, s = setup(client)
    assert (
        client.post(
            "/api/v1/customers/" + customer["id"] + "/discounts",
            json={"factor": "0.5", "effective_date": "2026-11-02"},
        ).status_code
        == 200
    )
    assert (
        upload(
            client, s, rows="2026-11-01,Compute,CNY,100\n2026-11-02,Compute,CNY,200"
        ).status_code
        == 200
    )
    assert dash(client)["total"] is None
    assert dash(client)["lifetime"] is None
    assert dash(client)["excluded"] == 2
    client.post(
        "/api/v1/settings/fx-rates",
        json={"currency": "CNY", "rate": "0.14", "effective_date": "2026-01-01"},
    )
    assert Decimal(dash(client)["total"]) == Decimal("25.9")
    client.post(
        "/api/v1/settings/fx-rates",
        json={"currency": "CNY", "rate": "0.15", "effective_date": "2026-11-02"},
    )
    assert Decimal(dash(client)["total"]) == Decimal("26.9")


def test_replacement_keeps_other_days_and_old_batch(client):
    _, s = setup(client)
    upload(client, s)
    replacement = "2026-11-01,Compute,USD,300"
    assert upload(client, s, rows=replacement, end="2026-11-01").status_code == 400
    assert (
        upload(
            client, s, rows=replacement, end="2026-11-01", reason="correct late cost"
        ).status_code
        == 200
    )
    assert Decimal(dash(client)["total"]) == Decimal("425")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 2
    assert (
        upload(
            client,
            s,
            rows=replacement.replace("300", "400"),
            end="2026-11-01",
            complete="false",
            reason="partial",
        ).status_code
        == 400
    )
    assert Decimal(dash(client)["total"]) == Decimal("425")


def test_atomic_bad_row_and_coverage(client):
    _, s = setup(client)
    assert (
        upload(
            client, s, rows="2026-11-01,Compute,USD,100\n2026-11-02,Compute,USD,nan"
        ).status_code
        == 400
    )
    assert dash(client)["total"] is None
    assert upload(client, s, rows="2026-11-01,Compute,USD,100").status_code == 400
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Fact)) == 0


def test_unknown_price_not_zero_and_explicit_zero(client):
    _, s = setup(client)
    assert upload(client, s, verified="false").status_code == 200
    assert dash(client)["total"] is None
    assert dash(client)["excluded"] == 2
    assert (
        upload(
            client,
            s,
            rows="2026-11-01,Compute,USD,0\n2026-11-02,Compute,USD,0",
            reason="verified zero",
        ).status_code
        == 200
    )
    assert Decimal(dash(client)["total"]) == 0
    assert dash(client)["coverage"] == "COMPLETE_DECLARED"


def test_filter_and_lifetime(client):
    customer, s = setup(client)
    upload(client, s)
    assert Decimal(dash(client, start="2026-11-02")["total"]) == Decimal("170")
    assert Decimal(dash(client, start="2026-11-02")["lifetime"]) == Decimal("255")
    assert dash(client, customer_id="does-not-exist")["total"] is None
    assert dash(client)["trend"][0]["value"] == "85.00"


def test_permissions_credentials_conflict_and_logout(client):
    customer, s = setup(client)
    tid = s["tenant_id"]
    r = client.put(
        "/api/v1/tenants/" + tid + "/credentials",
        json={
            "client_id": "30000000-0000-4000-8000-000000000001",
            "client_secret": "sensitive-test-value",
        },
    )
    assert r.status_code == 200
    assert "sensitive-test-value" not in client.get("/api/v1/tenants").text
    with SessionLocal() as db:
        assert "sensitive-test-value" not in db.get(Tenant, tid).secret
    assert (
        client.post(
            "/api/v1/tenants",
            json={"id": tid, "customer_id": customer["id"], "name": "Duplicate"},
        ).status_code
        == 409
    )
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/customers").status_code == 401
    client.post(
        "/api/v1/auth/login",
        json={"email": "member@test.local", "password": "Test-password-123"},
    )
    assert (
        client.put(
            "/api/v1/tenants/" + tid + "/credentials",
            json={"client_id": tid, "client_secret": "secret"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/settings/fx-rates",
            json={"currency": "CNY", "rate": "0.14", "effective_date": "2026-01-01"},
        ).status_code
        == 403
    )
    assert upload(client, s).status_code == 403


def test_security_and_ranges(client):
    assert (
        client.post(
            "/api/v1/customers", headers={"Origin": "https://attacker.example"}, json={}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/dashboard?start=2026-11-02&end=2026-11-01").status_code
        == 400
    )
    _, s = setup(client)
    assert upload(client, s, verified="true", evidence="").status_code == 400
    client.patch("/api/v1/tenants/" + s["tenant_id"], json={"active": False})
    assert upload(client, s).status_code == 400
    assert client.get("/api/v1/audit").headers["X-Trace-ID"]


def test_validation_never_echoes_secret(client):
    _, s = setup(client)
    secret = "sensitive-value-" * 400
    response = client.put(
        "/api/v1/tenants/" + s["tenant_id"] + "/credentials",
        json={"client_id": s["tenant_id"], "client_secret": secret},
    )
    assert response.status_code == 422
    assert "sensitive-value" not in response.text
    assert response.json()["detail"]["trace_id"]
