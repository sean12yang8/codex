"""Independent, immutable resource cost snapshots. Never added to canonical totals."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Text, UniqueConstraint, delete, select, event, text
from sqlalchemy.orm import Mapped, mapped_column, Session as OrmSession
from backend.app import (
    Base,
    engine,
    Subscription,
    Tenant,
    Customer,
    db_session,
    current_user,
    require,
    fail,
    now,
    dec,
    audit,
    valid_range,
    days,
)
from backend.operations import (
    AzureFailure,
    azure_request,
    setting,
    enqueue,
    AzureState,
    Setting,
    dashboard_cached,
)


class ResourceBatch(Base):
    __tablename__ = "resource_cost_batches"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    day: Mapped[str] = mapped_column(index=True)
    digest: Mapped[str] = mapped_column(unique=True)
    created: Mapped[str] = mapped_column(default=now)
    verified: Mapped[bool] = mapped_column(default=False)
    rows: Mapped[str] = mapped_column(Text)


class ResourceFact(Base):
    __tablename__ = "resource_cost_facts"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("resource_cost_batches.id"), index=True
    )
    day: Mapped[str] = mapped_column(index=True)
    resource_key: Mapped[str] = mapped_column(index=True)
    resource_id: Mapped[str] = mapped_column(Text)
    service: Mapped[str]
    currency: Mapped[str]
    amount: Mapped[str]


class ResourceCoverage(Base):
    __tablename__ = "resource_cost_coverage"
    __table_args__ = (UniqueConstraint("subscription_id", "day"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    day: Mapped[str] = mapped_column(index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("resource_cost_batches.id"))


Base.metadata.create_all(engine)


@event.listens_for(OrmSession, "before_flush")
def resource_revision(session, context, instances):
    if any(
        isinstance(o, (ResourceFact, ResourceBatch, ResourceCoverage))
        for o in list(session.new) + list(session.dirty) + list(session.deleted)
    ):
        session.connection().execute(
            text(
                "INSERT INTO settings (key,value) VALUES ('resource_revision','1') ON CONFLICT (key) DO UPDATE SET value=CAST(CAST(settings.value AS INTEGER)+1 AS TEXT)"
            )
        )


def resource_identity(resource_id):
    raw = (resource_id or "").strip()
    parts = raw.strip("/").split("/")
    lower = [p.lower() for p in parts]
    allocated = bool(
        raw.startswith("/")
        and len(parts) >= 6
        and lower[0] == "subscriptions"
        and "providers" in lower
        and lower.index("providers") + 3 < len(parts)
    )
    if not allocated:
        return dict(
            resource_id=raw,
            resource_key=hashlib.sha256(raw.lower().encode()).hexdigest(),
            name="未分配到资源的费用",
            resource_group="—",
            resource_type="未分配",
            allocated=False,
        )
    provider = lower.index("providers")
    group = (
        parts[lower.index("resourcegroups") + 1] if "resourcegroups" in lower else "—"
    )
    resource_type = "/".join([parts[provider + 1], *parts[provider + 2 :: 2]])
    return dict(
        resource_id=raw,
        resource_key=hashlib.sha256(raw.lower().encode()).hexdigest(),
        name=parts[-1],
        resource_group=group,
        resource_type=resource_type,
        allocated=True,
    )


def query_resources(subscription, token, start, end):
    url = f"https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start + "T00:00:00Z", "to": end + "T23:59:59Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": "ServiceName"},
            ],
        },
    }
    rows = []
    seen = set()
    while url:
        if url in seen or len(seen) >= 200:
            raise AzureFailure("RESOURCE_COST_PAGINATION_INVALID")
        seen.add(url)
        try:
            doc = azure_request("POST", url, token, json=body)
        except AzureFailure as error:
            if error.code == "AZURE_HTTP_400":
                raise AzureFailure("RESOURCE_DIMENSION_UNAVAILABLE") from None
            raise
        p = doc.get("properties", {})
        columns = [c["name"] for c in p.get("columns", [])]
        if not columns and not p.get("rows"):
            url = p.get("nextLink")
            continue
        amount = next(
            (
                c
                for c in ["Cost", "PreTaxCost", "CostInBillingCurrency"]
                if c in columns
            ),
            None,
        )
        if not amount or not {
            "UsageDate",
            "ResourceId",
            "ServiceName",
            "Currency",
        } <= set(columns):
            raise AzureFailure("UNSUPPORTED_RESOURCE_COST_SCHEMA")
        for values in p.get("rows", []):
            try:
                if len(values) != len(columns):
                    raise ValueError()
                r = dict(zip(columns, values))
                day = (
                    datetime.strptime(str(r["UsageDate"]), "%Y%m%d").date().isoformat()
                )
                if not start <= day <= end:
                    raise ValueError()
                identity = resource_identity(str(r["ResourceId"] or ""))
                currency = str(r["Currency"])
                service = str(r["ServiceName"])
                if (
                    not re.fullmatch("[A-Z]{3}", currency)
                    or not service
                    or len(service) > 150
                    or len(identity["resource_id"]) > 4096
                ):
                    raise ValueError()
                rows.append(
                    dict(
                        subscription_id=subscription,
                        usage_date=day,
                        resource_id=identity["resource_id"],
                        service=service,
                        currency=currency,
                        amount=str(dec(r[amount], max_scale=60)),
                    )
                )
            except Exception as error:
                if isinstance(error, AzureFailure):
                    raise
                raise AzureFailure("RESOURCE_COST_INVALID_ROW") from None
        if len(rows) > 200000:
            raise AzureFailure("RESOURCE_COST_ROW_LIMIT")
        url = p.get("nextLink")
    return rows


def publish_resources(db, sub, rows, verified=False):
    from backend.selective_purge import ranges, blocked

    exclusions = ranges(db, sub.id)
    grouped = defaultdict(list)
    db.scalar(select(Subscription).where(Subscription.id == sub.id).with_for_update())
    for r in rows:
        if r["subscription_id"] != sub.id:
            raise AzureFailure("INVALID_SOURCE_SCOPE")
        if not blocked(exclusions, r["usage_date"]):
            grouped[r["usage_date"]].append(r)
    published = []
    for day, records in grouped.items():
        records = sorted(
            records,
            key=lambda r: (
                r["resource_id"].lower(),
                r["service"],
                r["currency"],
                r["amount"],
            ),
        )
        raw = json.dumps(records, sort_keys=True)
        digest = hashlib.sha256(
            (sub.id + day + raw + str(verified)).encode()
        ).hexdigest()
        existing = db.scalar(
            select(ResourceBatch).where(ResourceBatch.digest == digest)
        )
        active = db.scalar(
            select(ResourceCoverage).where(
                ResourceCoverage.subscription_id == sub.id, ResourceCoverage.day == day
            )
        )
        if existing and active and active.batch_id == existing.id:
            published.append(existing.id)
            continue
        # A previously seen snapshot may be reactivated only after a successful explicit query.
        batch = existing or ResourceBatch(
            subscription_id=sub.id, day=day, digest=digest, verified=verified, rows=raw
        )
        if not existing:
            db.add(batch)
            db.flush()
        db.execute(
            delete(ResourceFact).where(
                ResourceFact.subscription_id == sub.id, ResourceFact.day == day
            )
        )
        db.execute(
            delete(ResourceCoverage).where(
                ResourceCoverage.subscription_id == sub.id, ResourceCoverage.day == day
            )
        )
        grouped_costs = defaultdict(Decimal)
        identities = {}
        for r in records:
            identity = resource_identity(r["resource_id"])
            key = (identity["resource_key"], r["service"], r["currency"])
            grouped_costs[key] += dec(r["amount"], max_scale=60)
            identities[identity["resource_key"]] = identity["resource_id"]
        for (key, service, currency), amount in grouped_costs.items():
            db.add(
                ResourceFact(
                    subscription_id=sub.id,
                    batch_id=batch.id,
                    day=day,
                    resource_key=key,
                    resource_id=identities[key],
                    service=service,
                    currency=currency,
                    amount=str(amount),
                )
            )
        db.add(ResourceCoverage(subscription_id=sub.id, day=day, batch_id=batch.id))
        published.append(batch.id)
    return published


def purge(db, sids, intervals=None):
    if not sids:
        return
    condition = ResourceFact.subscription_id.in_(sids)
    if intervals:
        from sqlalchemy import or_, and_

        condition = condition & or_(
            *[and_(ResourceFact.day >= a, ResourceFact.day < b) for a, b in intervals]
        )
    db.execute(delete(ResourceFact).where(condition))
    for model in [ResourceCoverage, ResourceBatch]:
        cond = model.subscription_id.in_(sids)
        if intervals:
            from sqlalchemy import or_, and_

            cond = cond & or_(
                *[and_(model.day >= a, model.day < b) for a, b in intervals]
            )
        db.execute(delete(model).where(cond))
    db.execute(
        delete(Setting).where(
            Setting.key.in_(["resource_state:" + sid for sid in sids])
        )
    )


def state(db, sid):
    return setting(
        db,
        "resource_state:" + sid,
        {"state": "NOT_COLLECTED", "last_sync": None, "error": ""},
    )


class ResourceSyncBody(BaseModel):
    start: date
    end: date


def install(app):
    @app.get("/api/v1/subscriptions/consumption")
    def subscription_consumption(
        start: date,
        end: date,
        customer_id: str = "",
        tenant_id: str = "",
        subscription_id: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 25,
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        from backend.cost_views import aggregate

        valid_range(start, end)
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        q = dict(
            view="subscription-consumption-v2",
            start=str(start),
            end=str(end),
            cid=customer_id,
            tid=tenant_id,
            sid=subscription_id,
            page=page,
            page_size=page_size,
            search=search,
        )

        def compute():
            items = aggregate(
                db, str(start), str(end), customer_id, tenant_id, subscription_id
            )
            keys = {
                (r["subscription_id"], r["customer_id"], r["tenant_id"]) for r in items
            }
            tenants = {t.id: t for t in db.scalars(select(Tenant))}
            customers = {c.id: c.name for c in db.scalars(select(Customer))}
            for sub in db.scalars(select(Subscription)):
                t = tenants[sub.tenant_id]
                if (
                    (subscription_id and sub.id != subscription_id)
                    or (customer_id and t.customer_id != customer_id)
                    or (tenant_id and t.id != tenant_id)
                    or (sub.id, t.customer_id, t.id) in keys
                ):
                    continue
                items.append(
                    dict(
                        subscription_id=sub.id,
                        subscription_name=sub.name,
                        customer_id=t.customer_id,
                        customer_name=customers[t.customer_id],
                        tenant_id=t.id,
                        tenant_name=t.name,
                        raw_costs=[],
                        trial_usd=None,
                        verified_usd=None,
                        records=0,
                        received_days=0,
                        last_sync=None,
                        days=[],
                        services=[],
                        pending_records=0,
                        trial_records=0,
                    )
                )
            needle = search.lower().strip()
            items = [
                i
                for i in items
                if not needle
                or needle
                in (
                    i["subscription_name"]
                    + i["subscription_id"]
                    + i["customer_name"]
                    + i["tenant_name"]
                ).lower()
            ]
            items.sort(
                key=lambda i: (
                    i["customer_name"],
                    i["tenant_name"],
                    i["subscription_name"],
                    i["subscription_id"],
                )
            )
            selected = items[(page - 1) * page_size : page * page_size]
            return dict(
                items=selected, total=len(items), page=page, page_size=page_size
            )

        payload = dashboard_cached(db, q, compute)
        selected = payload["items"]
        states = {
            s.id: s
            for s in db.scalars(
                select(AzureState).where(
                    AzureState.id.in_([r["subscription_id"] for r in selected])
                )
            )
        }
        for item in selected:
            s = states.get(item["subscription_id"])
            item["sync_state"] = s.sync if s else "PENDING"
            item["last_sync"] = max(
                filter(None, [item["last_sync"], s.last_sync if s else None]),
                default=None,
            )
            item["expected_days"] = len(days(str(start), str(end)))
            item.pop("days", None)
            item.pop("resource_id", None)
            item.pop("resource_key", None)
        return payload

    @app.get("/api/v1/resource-costs")
    def resource_list(
        start: date,
        end: date,
        subscription_id: str,
        customer_id: str = "",
        tenant_id: str = "",
        search: str = "",
        service: str = "",
        page: int = 1,
        page_size: int = 25,
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        from backend.cost_views import aggregate

        valid_range(start, end)
        require(db, Subscription, subscription_id)
        q = dict(
            view="resources-v1",
            resource_revision=str(setting(db, "resource_revision", 0)),
            start=str(start),
            end=str(end),
            cid=customer_id,
            tid=tenant_id,
            sid=subscription_id,
            service=service,
        )
        payload = dashboard_cached(
            db,
            q,
            lambda: {
                "items": aggregate(
                    db,
                    str(start),
                    str(end),
                    customer_id,
                    tenant_id,
                    subscription_id,
                    True,
                    service,
                )
            },
        )
        items = payload["items"]
        canonical = aggregate(
            db,
            str(start),
            str(end),
            customer_id,
            tenant_id,
            subscription_id,
            False,
            service,
        )
        source_totals = defaultdict(Decimal)
        canonical_totals = defaultdict(Decimal)
        source_days = {d for row in items for d in row["days"]}
        canonical_days = {d for row in canonical for d in row["days"]}
        for row in items:
            for cost in row["raw_costs"]:
                source_totals[cost["currency"]] += Decimal(cost["amount"])
        for row in canonical:
            for cost in row["raw_costs"]:
                canonical_totals[cost["currency"]] += Decimal(cost["amount"])
        aligned = (
            source_days == canonical_days
            and source_days
            and all(
                abs(source_totals[c] - canonical_totals[c]) <= Decimal("0.00000001")
                for c in set(source_totals) | set(canonical_totals)
            )
        )
        reconciliation = (
            "MATCHED"
            if aligned
            else "PARTIAL"
            if source_days != canonical_days
            else "DIFFERENT_SNAPSHOT"
            if source_days
            else "NOT_COLLECTED"
        )
        needle = search.lower().strip()
        for i in items:
            i.update(resource_identity(i["resource_id"]))
        items = [
            i
            for i in items
            if not needle
            or needle
            in (
                i["name"] + i["resource_group"] + i["resource_type"] + i["resource_id"]
            ).lower()
        ]
        items.sort(
            key=lambda i: (
                not i["allocated"],
                i["resource_group"].lower(),
                i["name"].lower(),
                i["resource_key"],
            )
        )
        totals = defaultdict(Decimal)
        for i in items:
            for c in i["raw_costs"]:
                totals[c["currency"]] += Decimal(c["amount"])
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        return dict(
            items=items[(page - 1) * page_size : page * page_size],
            total=len(items),
            page=page,
            page_size=page_size,
            state=state(db, subscription_id),
            totals=[
                {"currency": c, "amount": str(v)} for c, v in sorted(totals.items())
            ],
            services=sorted({s for i in items for s in i["services"]}),
            quantity_available=False,
            reconciliation=reconciliation,
            canonical_totals=[
                {"currency": c, "amount": str(v)}
                for c, v in sorted(canonical_totals.items())
            ],
            source_totals=[
                {"currency": c, "amount": str(v)}
                for c, v in sorted(source_totals.items())
            ],
        )

    @app.get("/api/v1/resource-costs/{resource_key}/details")
    def resource_detail(
        resource_key: str,
        start: date,
        end: date,
        subscription_id: str,
        customer_id: str = "",
        tenant_id: str = "",
        service: str = "",
        page: int = 1,
        page_size: int = 25,
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        from backend.cost_views import scoped_rows

        valid_range(start, end)
        require(db, Subscription, subscription_id)
        if not re.fullmatch("[a-f0-9]{64}", resource_key):
            fail("INVALID_RESOURCE_KEY", "资源标识无效")
        source = scoped_rows(
            db,
            str(start),
            str(end),
            customer_id,
            tenant_id,
            subscription_id,
            True,
            key=resource_key,
            service=service,
        )
        rows = (
            list(db.execute(*source).mappings())
            if isinstance(source, tuple)
            else source
        )
        rows.sort(key=lambda r: (r["day"], r["service"], r["currency"]), reverse=True)
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        items = []
        for row in rows[(page - 1) * page_size : page * page_size]:
            item = dict(row)
            for k in ["raw", "trial_usd", "verified_usd"]:
                item[k] = str(item[k]) if item[k] is not None else None
            item["pending"] = not item["verified"] or item["fallback"]
            items.append(item)
        return dict(items=items, total=len(rows), page=page, page_size=page_size)

    @app.post("/api/v1/subscriptions/{id}/resource-costs/sync", status_code=202)
    def resource_sync(
        id: str,
        body: ResourceSyncBody,
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        valid_range(body.start, body.end)
        if body.end < body.start:
            fail("EMPTY_RANGE", "当前没有完整的 UTC 日期可采集")
        sub = require(db, Subscription, id)
        tenant = require(db, Tenant, sub.tenant_id)
        if not tenant.active or not require(db, Customer, tenant.customer_id).active:
            fail("INACTIVE", "客户或租户已停用")
        job = enqueue(
            db,
            user,
            "resources",
            "subscription",
            id,
            str(body.start),
            str(body.end),
            priority=20,
        )
        audit(db, user, "QUEUE_RESOURCE_COSTS", job.id)
        return {"job_id": job.id, "state": job.state}
