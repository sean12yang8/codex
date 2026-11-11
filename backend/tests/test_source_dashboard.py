"""Unverified costs remain visible, without changing ACR or mixing currencies."""

from decimal import Decimal
from sqlalchemy import select
from backend.app import SessionLocal, Fact
from backend.tests.test_workflow import setup, upload, dash


def currencies(payload):
    return {g["currency"]: g for g in payload["raw"]["currencies"]}


def test_unverified_raw_costs_separate_currencies_and_gaps(client):
    _, sub = setup(client)
    result = upload(
        client,
        sub,
        rows="2026-11-01,Compute,USD,100.123456789012345678901\n2026-11-01,Storage,EUR,200\n2026-11-02,Compute,USD,0",
        verified="false",
        complete="false",
    )
    assert result.status_code == 200, result.text
    d = client.get(
        "/api/v1/dashboard", params={"start": "2026-11-01", "end": "2026-11-03"}
    ).json()
    assert d["total"] is None and d["lifetime"] is None
    assert (
        d["received_days"] == 2 and d["complete_days"] == 0 and d["expected_days"] == 3
    )
    assert d["raw"]["records"] == 3
    assert d["raw"]["excluded_reasons"] == {"UNKNOWN_PRICE_BASIS": 3}
    groups = currencies(d)
    assert set(groups) == {"USD", "EUR"}
    assert Decimal(groups["USD"]["total"]) == Decimal("100.123456789012345678901")
    assert Decimal(groups["EUR"]["total"]) == Decimal("200")
    assert (
        groups["USD"]["trend"][1]["value"] == "0"
        and groups["USD"]["trend"][2]["value"] is None
    )
    assert groups["EUR"]["trend"][1]["value"] is None
    assert groups["USD"]["top"][0]["name"] == "Test customer"
    assert groups["EUR"]["services"][0]["name"] == "Storage"
    monthly = client.get(
        "/api/v1/dashboard",
        params={"start": "2026-11-01", "end": "2026-11-03", "granularity": "month"},
    ).json()
    assert currencies(monthly)["USD"]["trend"] == [
        {"day": "2026-11", "value": groups["USD"]["total"]}
    ]
    # A narrow interval must retain all-time totals and preserve an empty/unknown axis.
    narrow = client.get(
        "/api/v1/dashboard", params={"start": "2026-11-03", "end": "2026-11-03"}
    ).json()
    assert narrow["raw"]["records"] == 0 and currencies(narrow)["USD"]["total"] is None
    assert currencies(narrow)["USD"]["lifetime"] == groups["USD"]["lifetime"]
    # Repeated cached requests must not replace a null ACR with unverified costs.
    assert (
        client.get(
            "/api/v1/dashboard", params={"start": "2026-11-01", "end": "2026-11-03"}
        ).json()
        == d
    )


def test_raw_ownership_transfer_and_cache_invalidation(client, monkeypatch):
    old, sub = setup(client)
    upload(client, sub)
    new = client.post(
        "/api/v1/customers",
        json={
            "name": "Transferred owner",
            "factor": "0.5",
            "effective_date": "2026-01-01",
        },
    ).json()
    tenant = client.post(
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
            json={"tenant_id": tenant["id"], "effective_date": "2026-11-02"},
        ).status_code
        == 200
    )
    assert (
        Decimal(currencies(dash(client, customer_id=old["id"]))["USD"]["total"]) == 100
    )
    assert (
        Decimal(currencies(dash(client, tenant_id=tenant["id"]))["USD"]["total"]) == 200
    )
    assert (
        Decimal(currencies(dash(client, subscription_id=sub["id"]))["USD"]["total"])
        == 300
    )
    # An old owner's historical erasure must also remove it from source totals/cache.
    monkeypatch.setenv("PURGE_ENABLED", "true")
    preview = client.post("/api/v1/customers/" + old["id"] + "/purge-preview").json()
    assert (
        client.post(
            "/api/v1/customers/" + old["id"] + "/purge",
            json={"challenge": preview["challenge"], "confirm": old["id"]},
        ).status_code
        == 200
    )
    refreshed = dash(client)
    assert Decimal(currencies(refreshed)["USD"]["total"]) == 200
    assert refreshed["raw"]["records"] == 1
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Fact)))) == 1


def test_missing_fx_keeps_native_cost_visible(client):
    _, sub = setup(client)
    result = upload(
        client, sub, rows="2026-11-01,Compute,CNY,700\n2026-11-02,Compute,CNY,1400"
    )
    assert result.status_code == 200, result.text
    d = dash(client)
    assert d["total"] is None and d["raw"]["excluded_reasons"] == {"FX_RATE_MISSING": 2}
    assert Decimal(currencies(d)["CNY"]["total"]) == 2100
    assert (
        client.post(
            "/api/v1/settings/fx-rates",
            json={"currency": "CNY", "rate": "0.14", "effective_date": "2026-01-01"},
        ).status_code
        == 200
    )
    after = dash(client)
    assert Decimal(after["total"]) == Decimal("249.90")
    assert Decimal(currencies(after)["CNY"]["total"]) == 2100
    assert after["raw"]["excluded_reasons"] == {}


def test_provisional_acr_is_distinct_from_verified_metrics_and_exports(client):
    import csv
    import io
    from backend.app import Batch

    _, sub = setup(client)
    assert upload(client, sub, verified="false").status_code == 200
    d = dash(client)
    trial = d["estimate"]
    assert d["total"] is None and d["lifetime"] is None
    assert (
        trial["status"] == "UNVERIFIED"
        and trial["assumption"] == "PRE_CUSTOMER_DISCOUNT"
    )
    assert Decimal(trial["total"]) == Decimal("255") and Decimal(
        trial["lifetime"]
    ) == Decimal("255")
    assert (
        trial["records"] == 2
        and trial["pending_records"] == 2
        and trial["excluded"] == 0
    )
    assert [Decimal(p["value"]) for p in trial["trend"]] == [
        Decimal("85"),
        Decimal("170"),
    ]
    assert Decimal(trial["top"][0]["value"]) == Decimal("255")
    with SessionLocal() as db:
        assert db.scalar(select(Batch)).verified is False
    export = client.get("/api/v1/reports/export?start=2026-11-01&end=2026-11-02")
    for row in csv.DictReader(io.StringIO(export.text.lstrip("\ufeff"))):
        assert row["acr_usd"] == "" and row["status"] == "UNKNOWN_PRICE_BASIS"


def test_trial_combines_currencies_and_labels_current_discount_fallback(client):
    from backend.app import Discount

    _, sub = setup(client)
    assert (
        upload(
            client,
            sub,
            rows="2026-11-01,Compute,USD,100\n2026-11-02,Storage,CNY,700",
            verified="false",
        ).status_code
        == 200
    )
    d = dash(client)
    assert Decimal(d["estimate"]["total"]) == 85
    assert d["estimate"]["excluded_reasons"] == {"FX_RATE_MISSING": 1}
    client.post(
        "/api/v1/settings/fx-rates",
        json={"currency": "CNY", "rate": "0.14", "effective_date": "2026-01-01"},
    )
    d = dash(client)
    assert Decimal(d["estimate"]["total"]) == Decimal("168.30")
    assert d["total"] is None
    # A missing earlier discount uses the explicitly labeled current-rate assumption.
    with SessionLocal.begin() as db:
        db.scalar(select(Discount)).day = "2026-11-02"
    d = dash(client)
    assert Decimal(d["estimate"]["total"]) == Decimal("168.30")
    assert d["estimate"]["fallback_records"] == 1
    assert d["estimate"]["discount_policy"] == "HISTORICAL_WITH_CURRENT_FALLBACK"
    with SessionLocal.begin() as db:
        discount = db.scalar(select(Discount))
        db.add(
            Discount(customer_id=discount.customer_id, day="2026-11-01", factor="0.5")
        )
    d = dash(client)
    assert Decimal(d["estimate"]["total"]) == Decimal("133.30")
    assert d["estimate"]["fallback_records"] == 0


def test_unverified_zero_trial_does_not_emit_verified_zero_alert(client):
    from datetime import date, timedelta
    from backend.worker import evaluate_alerts
    from backend.operations import Alert

    c, sub = setup(client)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    rows = "\n".join(
        (start + timedelta(days=i)).isoformat() + ",Compute,USD,0" for i in range(7)
    )
    assert (
        upload(
            client, sub, rows=rows, start=str(start), end=str(end), verified="false"
        ).status_code
        == 200
    )
    d = client.get(
        "/api/v1/dashboard", params={"start": str(start), "end": str(end)}
    ).json()
    assert d["estimate"]["total"] == "0.00" or Decimal(d["estimate"]["total"]) == 0
    assert d["total"] is None
    with SessionLocal.begin() as db:
        evaluate_alerts(db, [c["id"]])
        assert not list(db.scalars(select(Alert)))


def test_discount_backfill_is_trial_only_even_when_price_basis_verified(client):
    from backend.app import Discount

    _, sub = setup(client)
    with SessionLocal.begin() as db:
        db.scalar(select(Discount)).day = "2026-11-02"
    assert upload(client, sub).status_code == 200
    d = dash(client)
    assert Decimal(d["total"]) == 170
    assert d["raw"]["excluded_reasons"] == {"DISCOUNT_MISSING": 1}
    assert Decimal(d["estimate"]["total"]) == 255
    assert (
        d["estimate"]["fallback_records"] == 1 and d["estimate"]["pending_records"] == 1
    )
    assert d["estimate"]["status"] == "UNVERIFIED"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Discount)))) == 1


def test_discount_save_reports_actual_window_and_rejects_duplicate_date(client):
    c, _ = setup(client)
    later = client.post(
        "/api/v1/customers/" + c["id"] + "/discounts",
        json={"factor": "1", "effective_date": "2026-11-10"},
    )
    assert later.status_code == 200
    assert (
        later.json()["version"]["factor"] == "1"
        and later.json()["history_preserved"] is True
    )
    backdated = client.post(
        "/api/v1/customers/" + c["id"] + "/discounts",
        json={"factor": "0.5", "effective_date": "2026-11-01"},
    )
    assert (
        backdated.status_code == 200
        and backdated.json()["effective_until"] == "2026-11-09"
    )
    duplicate = client.post(
        "/api/v1/customers/" + c["id"] + "/discounts",
        json={"factor": "0.9", "effective_date": "2026-11-10"},
    )
    assert (
        duplicate.status_code == 409
        and duplicate.json()["detail"]["code"] == "DISCOUNT_DATE_EXISTS"
    )
    customer = next(
        x for x in client.get("/api/v1/customers").json() if x["id"] == c["id"]
    )
    assert [(d["day"], d["factor"]) for d in customer["discounts"]] == [
        ("2026-01-01", "0.85"),
        ("2026-11-01", "0.5"),
        ("2026-11-10", "1"),
    ]
