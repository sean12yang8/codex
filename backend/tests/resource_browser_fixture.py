import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from backend.app import (
    SessionLocal,
    User,
    Customer,
    Tenant,
    Subscription,
    Discount,
    Batch,
    Fact,
    now,
)
from backend.resource_costs import publish_resources

with SessionLocal.begin() as db:
    assert db.scalar(select(User).where(User.email == "demo@azure-lens.local")), (
        "Local demo environment only"
    )
    cid, tid, sid, empty, bid = [str(uuid4()) for _ in range(5)]
    day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    db.add(
        Customer(
            id=cid, name="资源费用浏览器验收", notes="Synthetic local fixture only"
        )
    )
    db.flush()
    db.add(Tenant(id=tid, customer_id=cid, name="费用测试租户"))
    db.add(Discount(customer_id=cid, day=day, factor="0.85"))
    db.flush()
    db.add_all(
        [
            Subscription(id=sid, tenant_id=tid, name="资源费用测试订阅"),
            Subscription(id=empty, tenant_id=tid, name="资源无数据订阅"),
        ]
    )
    db.flush()
    db.add(
        Batch(
            id=bid,
            subscription_id=sid,
            digest=str(uuid4()),
            filename="browser-resource-fixture.csv",
            created=now(),
            start=day,
            end=day,
            verified=False,
            complete=False,
            evidence="Local fixture only",
            reason="",
            rows="[]",
            row_count=1,
        )
    )
    db.flush()
    db.add(
        Fact(
            subscription_id=sid,
            batch_id=bid,
            day=day,
            service="Compute",
            currency="USD",
            amount="300",
        )
    )
    db.flush()
    rid = f"/subscriptions/{sid}/resourceGroups/rg-costs/providers/Microsoft.Compute/virtualMachines/vm-cost-detail"
    rows = [
        dict(
            subscription_id=sid,
            usage_date=day,
            resource_id=r,
            service="Compute",
            currency="USD",
            amount=a,
        )
        for r, a in [(rid, "290"), ("", "10")]
    ]
    publish_resources(db, db.get(Subscription, sid), rows)
print(json.dumps(dict(cid=cid, tid=tid, sid=sid, empty=empty, day=day)))
