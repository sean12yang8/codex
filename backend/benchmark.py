"""Isolated synthetic acceptance workload; never run against a production DB."""

import os, tempfile, time, json, statistics, platform, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

if os.environ.get("BENCHMARK_ISOLATED") != "yes":
    raise SystemExit("Set BENCHMARK_ISOLATED=yes; use a dedicated database")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="azurelens-benchmark-"))
from fastapi.testclient import TestClient
from sqlalchemy import insert
from backend.app import (
    app,
    engine,
    User,
    Customer,
    Discount,
    Tenant,
    Subscription,
    Batch,
    Fact,
    Coverage,
    hash_password,
)

from backend.subscription_tiers import SubscriptionTier

N = 10020
C = 1002
D = 8
start = (datetime.now(timezone.utc).date() - timedelta(days=D)).isoformat()
end = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
customers = [
    {
        "id": str(uuid.uuid4()),
        "name": "Benchmark " + str(i),
        "notes": "synthetic",
        "active": True,
    }
    for i in range(C)
]
tenants = [
    {
        "id": str(uuid.uuid4()),
        "customer_id": c["id"],
        "name": c["name"],
        "client_id": "",
        "secret": "",
        "active": True,
    }
    for c in customers
]
subs = [
    {
        "id": str(uuid.uuid4()),
        "tenant_id": tenants[i // 10]["id"],
        "name": "Synthetic " + str(i),
        "active": True,
    }
    for i in range(N)
]
base = {
    "verified": True,
    "complete": True,
    "filename": "synthetic.csv",
    "created": datetime.now(timezone.utc).isoformat(),
    "start": start,
    "end": end,
    "evidence": "synthetic fixture only",
    "reason": "",
    "rows": "[]",
    "row_count": D * 2,
}
batches = [
    {
        **base,
        "id": str(uuid.uuid4()),
        "digest": str(uuid.uuid4()),
        "subscription_id": s["id"],
    }
    for s in subs
]
print("Seeding dedicated synthetic database...", flush=True)
with engine.begin() as db:
    if db.execute(
        __import__("sqlalchemy")
        .select(__import__("sqlalchemy").func.count())
        .select_from(Customer)
    ).scalar():
        raise SystemExit("Benchmark requires an empty database")
    db.execute(
        insert(User),
        [
            {
                "id": str(uuid.uuid4()),
                "email": "benchmark@local.test",
                "name": "Benchmark",
                "password": hash_password("Benchmark-Test-Password"),
                "role": "admin",
            }
        ],
    )
    db.execute(insert(Customer), customers)
    db.execute(insert(Tenant), tenants)
    db.execute(insert(Subscription), subs)
    db.execute(
        insert(SubscriptionTier),
        [
            dict(
                subscription_id=s["id"],
                name="Tier 1",
                state="SUCCEEDED",
                checked_at=datetime.now(timezone.utc).isoformat(),
                last_success=datetime.now(timezone.utc).isoformat(),
                assignment_date="",
                error="",
                job_id="",
            )
            for s in subs
        ],
    )
    db.execute(
        insert(Discount),
        [
            {
                "id": str(uuid.uuid4()),
                "customer_id": c["id"],
                "day": "2020-01-01",
                "factor": "0.85",
            }
            for c in customers
        ],
    )
    db.execute(insert(Batch), batches)
    facts = []
    coverage = []
    for i, s in enumerate(subs):
        for offset in range(D):
            day = (
                (datetime.fromisoformat(start) + timedelta(days=offset))
                .date()
                .isoformat()
            )
            coverage.append(
                {
                    "id": str(uuid.uuid4()),
                    "subscription_id": s["id"],
                    "day": day,
                    "batch_id": batches[i]["id"],
                }
            )
            for service in ["Compute", "Storage"]:
                facts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "subscription_id": s["id"],
                        "day": day,
                        "service": service,
                        "currency": "USD",
                        "amount": "100",
                        "batch_id": batches[i]["id"],
                    }
                )
        if len(facts) >= 10000:
            db.execute(insert(Fact), facts)
            db.execute(insert(Coverage), coverage)
            facts = []
            coverage = []
    if facts:
        db.execute(insert(Fact), facts)
        db.execute(insert(Coverage), coverage)
with TestClient(app, base_url="https://testserver") as client:
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "email": "benchmark@local.test",
                "password": "Benchmark-Test-Password",
            },
        ).status_code
        == 200
    )
    q = f"/api/v1/dashboard?start={start}&end={end}"
    t = time.perf_counter()
    r = client.get(q)
    assert r.status_code == 200
    cold = time.perf_counter() - t
    assert r.json()["subscriptions"] == N and r.json()["total"] is not None
    assert r.json()["raw"]["records"] == N * D * 2
    assert Decimal(r.json()["estimate"]["total"]) == Decimal(r.json()["total"])
    assert r.json()["estimate"]["pending_records"] == 0
    assert Decimal(r.json()["raw"]["currencies"][0]["total"]) == Decimal(
        N * D * 2 * 100
    )
    consumption_path = q.replace("/dashboard?", "/subscriptions/consumption?")
    t = time.perf_counter()
    consumption = client.get(consumption_path)
    cold_consumption = time.perf_counter() - t
    assert consumption.status_code == 200 and consumption.json()["total"] == N
    catalog_path = q.replace("/dashboard?", "/catalog/subscriptions?")
    t = time.perf_counter()
    catalog = client.get(catalog_path)
    cold_catalog = time.perf_counter() - t
    assert catalog.status_code == 200 and catalog.json()["total"] == N
    assert all(item["tier"]["name"] == "Tier 1" for item in catalog.json()["items"])
    timings = []
    errors = []

    def hit(i):
        path = q if i % 10 < 7 else q + "&customer_id=" + customers[(i % 3)]["id"]
        begin = time.perf_counter()
        res = client.get(path)
        companion = client.get(
            path.replace("/dashboard?", "/subscriptions/consumption?")
        )
        if companion.status_code != 200:
            errors.append(companion.status_code)
        tier_catalog = client.get(
            path.replace("/dashboard?", "/catalog/subscriptions?")
        )
        if tier_catalog.status_code != 200:
            errors.append(tier_catalog.status_code)
        elif not all(
            item["tier"]["name"] == "Tier 1" for item in tier_catalog.json()["items"]
        ):
            errors.append("TIER_METADATA_MISSING")
        duration = time.perf_counter() - begin
        if res.status_code != 200:
            errors.append(res.status_code)
        return ("dashboard" if i % 10 < 7 else "customer_detail", duration)

    # Warm the three selected customer scopes, then measure the declared 70/30 mix.
    for c in customers[:3]:
        assert client.get(q + "&customer_id=" + c["id"]).status_code == 200
        assert (
            client.get(consumption_path + "&customer_id=" + c["id"]).status_code == 200
        )
        assert client.get(catalog_path + "&customer_id=" + c["id"]).status_code == 200
    with ThreadPoolExecutor(max_workers=20) as pool:
        timings = list(pool.map(hit, range(100)))
    report = {
        "subscriptions": N,
        "customers": C,
        "facts": N * D * 2,
        "coverage_rows": N * D,
        "concurrent_users": 20,
        "requests": 100,
        "mix": "70% dashboard / 30% three customer scopes; each action reads dashboard + paged subscription consumption + tier catalog, warmed financial caches",
        "http_requests": 300,
        "cold_subscription_consumption_seconds": round(cold_consumption, 3),
        "cold_tier_catalog_seconds": round(cold_catalog, 3),
        "cold_dashboard_seconds": round(cold, 3),
        "errors": errors,
        "machine": platform.platform(),
        "cpu_count": os.cpu_count(),
        "database": "PostgreSQL"
        if not str(engine.url).startswith("sqlite")
        else "SQLite",
    }
    for kind in ["dashboard", "customer_detail"]:
        values = sorted(v for k, v in timings if k == kind)
        report[kind] = {
            "p50": round(statistics.median(values), 3),
            "p95": round(values[int(len(values) * 0.95) - 1], 3),
            "p99": round(values[int(len(values) * 0.99) - 1], 3),
        }
    print(json.dumps(report, indent=2), flush=True)
    if errors or report["dashboard"]["p95"] > 3 or report["customer_detail"]["p95"] > 5:
        raise SystemExit(1)
