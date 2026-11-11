"""Scoped subscription/resource aggregates with the existing historical pricing rules."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, text
from backend.app import URL, Fact, Batch, Customer, Tenant, Subscription, Discount, Fx


def scoped_rows(
    db, start, end, cid="", tid="", sid="", resource=False, key="", service=""
):
    from backend.operations import Ownership
    from backend.selective_purge import PurgedRange, blocked

    if resource:
        from backend.resource_costs import ResourceFact, ResourceBatch

        model, batch_model = ResourceFact, ResourceBatch
    else:
        model, batch_model = Fact, Batch
    if not URL.startswith("sqlite"):
        table, batches = (
            ("resource_cost_facts", "resource_cost_batches")
            if resource
            else ("facts", "batches")
        )
        resource_fields = (
            "f.resource_key,f.resource_id,"
            if resource
            else "''::TEXT AS resource_key,''::TEXT AS resource_id,"
        )
        where = " AND (:key='' OR f.resource_key=:key)" if resource else ""
        statement = text(
            """SELECT f.subscription_id,COALESCE(o.customer_id,t.customer_id) AS customer_id,
 COALESCE(o.tenant_id,s.tenant_id) AS tenant_id,f.day,f.service,f.currency,"""
            + resource_fields
            + """
 CAST(f.amount AS NUMERIC) AS raw,b.verified,b.created,
 d.factor AS historical_factor,COALESCE(d.factor,cd.factor) AS factor,
 CASE WHEN f.currency='USD' THEN '1' ELSE x.rate END AS rate,
 (d.factor IS NULL AND cd.factor IS NOT NULL) AS fallback,
 CASE WHEN b.verified AND d.factor IS NOT NULL AND (f.currency='USD' OR x.rate IS NOT NULL)
 THEN CAST(f.amount AS NUMERIC)*CAST(d.factor AS NUMERIC)*CASE WHEN f.currency='USD' THEN 1 ELSE CAST(x.rate AS NUMERIC) END ELSE NULL END AS verified_usd,
 CASE WHEN COALESCE(d.factor,cd.factor) IS NOT NULL AND (f.currency='USD' OR x.rate IS NOT NULL)
 THEN CAST(f.amount AS NUMERIC)*CAST(COALESCE(d.factor,cd.factor) AS NUMERIC)*CASE WHEN f.currency='USD' THEN 1 ELSE CAST(x.rate AS NUMERIC) END ELSE NULL END AS trial_usd
 FROM """
            + table
            + """ f JOIN """
            + batches
            + """ b ON b.id=f.batch_id JOIN subscriptions s ON s.id=f.subscription_id JOIN tenants t ON t.id=s.tenant_id
 LEFT JOIN LATERAL (SELECT customer_id,tenant_id FROM ownership_history o WHERE o.subscription_id=f.subscription_id AND o.day<=f.day ORDER BY o.day DESC LIMIT 1) o ON TRUE
 LEFT JOIN LATERAL (SELECT factor FROM discounts d WHERE d.customer_id=COALESCE(o.customer_id,t.customer_id) AND d.day<=f.day ORDER BY d.day DESC LIMIT 1) d ON TRUE
 LEFT JOIN LATERAL (SELECT factor FROM discounts cd WHERE cd.customer_id=COALESCE(o.customer_id,t.customer_id) AND cd.day<=:today ORDER BY cd.day DESC LIMIT 1) cd ON TRUE
 LEFT JOIN LATERAL (SELECT rate FROM fx x WHERE x.currency=f.currency AND x.day<=f.day ORDER BY x.day DESC LIMIT 1) x ON TRUE
 WHERE f.day>=:start AND f.day<=:end AND (:cid='' OR COALESCE(o.customer_id,t.customer_id)=:cid)
 AND (:tid='' OR COALESCE(o.tenant_id,s.tenant_id)=:tid) AND (:sid='' OR s.id=:sid)
 AND (:service='' OR f.service=:service)"""
            + where
            + """
 AND NOT EXISTS(SELECT 1 FROM purged_ownership_ranges p WHERE p.subscription_id=f.subscription_id AND p.start<=f.day AND f.day<p."end")"""
        )
        return statement, dict(
            start=start,
            end=end,
            cid=cid,
            tid=tid,
            sid=sid,
            key=key,
            service=service,
            today=datetime.now(timezone.utc).date().isoformat(),
        )
    customers = {t.id: t.customer_id for t in db.scalars(select(Tenant))}
    subs = {s.id: s for s in db.scalars(select(Subscription))}
    batches = {b.id: b for b in db.scalars(select(batch_model))}
    history, purged, discounts, rates = (defaultdict(list) for _ in range(4))
    for o in db.scalars(select(Ownership).order_by(Ownership.day)):
        history[o.subscription_id].append(o)
    for p in db.scalars(select(PurgedRange)):
        purged[p.subscription_id].append(p)
    for d in db.scalars(select(Discount).order_by(Discount.day)):
        discounts[d.customer_id].append(d)
    for x in db.scalars(select(Fx).order_by(Fx.day)):
        rates[x.currency].append(x)
    result = []
    today = datetime.now(timezone.utc).date().isoformat()
    for f in db.scalars(select(model).where(model.day >= start, model.day <= end)):
        if (
            (sid and f.subscription_id != sid)
            or (service and f.service != service)
            or (resource and key and f.resource_key != key)
            or blocked(purged[f.subscription_id], f.day)
        ):
            continue
        sub = subs[f.subscription_id]
        h = [o for o in history[sub.id] if o.day <= f.day]
        owner_cid = h[-1].customer_id if h else customers[sub.tenant_id]
        owner_tid = h[-1].tenant_id if h else sub.tenant_id
        if (cid and owner_cid != cid) or (tid and owner_tid != tid):
            continue
        ds = [d for d in discounts[owner_cid] if d.day <= f.day]
        current = [d for d in discounts[owner_cid] if d.day <= today]
        xs = [x for x in rates[f.currency] if x.day <= f.day]
        factor = (ds or current)[-1].factor if ds or current else None
        rate = "1" if f.currency == "USD" else xs[-1].rate if xs else None
        raw = Decimal(f.amount)
        trial = (
            raw * Decimal(factor) * Decimal(rate)
            if factor is not None and rate is not None
            else None
        )
        b = batches[f.batch_id]
        result.append(
            dict(
                subscription_id=sub.id,
                customer_id=owner_cid,
                tenant_id=owner_tid,
                day=f.day,
                service=f.service,
                currency=f.currency,
                resource_key=f.resource_key if resource else "",
                resource_id=f.resource_id if resource else "",
                raw=raw,
                verified=b.verified,
                created=b.created,
                historical_factor=ds[-1].factor if ds else None,
                factor=factor,
                rate=rate,
                fallback=not ds and bool(current),
                verified_usd=trial if b.verified and ds else None,
                trial_usd=trial,
            )
        )
    return result


def aggregate(db, start, end, cid="", tid="", sid="", resource=False, service=""):
    source = scoped_rows(db, start, end, cid, tid, sid, resource, service=service)
    if isinstance(source, tuple):
        statement, params = source
        sql = (
            """WITH costs AS ("""
            + statement.text
            + """) SELECT subscription_id,customer_id,tenant_id,resource_key,MIN(resource_id) AS resource_id,currency,
 SUM(raw) AS raw,SUM(verified_usd) AS verified_usd,SUM(trial_usd) AS trial_usd,COUNT(*) AS records,ARRAY_AGG(DISTINCT day) AS days,
 COUNT(trial_usd) AS trial_records,COUNT(trial_usd) FILTER(WHERE NOT verified OR fallback) AS pending_records,
 MAX(created) AS last_sync,MIN(day) AS first_day,MAX(day) AS last_day,ARRAY_AGG(DISTINCT service) AS services
 FROM costs GROUP BY subscription_id,customer_id,tenant_id,resource_key,currency"""
        )
        rows = list(db.execute(text(sql), params).mappings())
    else:
        groups = {}
        for r in source:
            k = tuple(
                r[n]
                for n in [
                    "subscription_id",
                    "customer_id",
                    "tenant_id",
                    "resource_key",
                    "currency",
                ]
            )
            g = groups.setdefault(
                k,
                {
                    **{
                        n: r[n]
                        for n in [
                            "subscription_id",
                            "customer_id",
                            "tenant_id",
                            "resource_key",
                            "resource_id",
                            "currency",
                        ]
                    },
                    "raw": Decimal(0),
                    "verified_usd": None,
                    "trial_usd": None,
                    "records": 0,
                    "trial_records": 0,
                    "pending_records": 0,
                    "days": set(),
                    "services": set(),
                    "last_sync": "",
                    "first_day": r["day"],
                    "last_day": r["day"],
                },
            )
            g["raw"] += r["raw"]
            g["records"] += 1
            g["days"].add(r["day"])
            g["services"].add(r["service"])
            g["last_sync"] = max(g["last_sync"], r["created"])
            g["first_day"] = min(g["first_day"], r["day"])
            g["last_day"] = max(g["last_day"], r["day"])
            for name in ["verified_usd", "trial_usd"]:
                if r[name] is not None:
                    g[name] = (g[name] or Decimal(0)) + r[name]
            if r["trial_usd"] is not None:
                g["trial_records"] += 1
                g["pending_records"] += int(not r["verified"] or r["fallback"])
        rows = []
        for g in groups.values():
            rows.append(g)
    cids = {r["customer_id"] for r in rows}
    tids = {r["tenant_id"] for r in rows}
    sids = {r["subscription_id"] for r in rows}
    names = {
        c.id: c.name for c in db.scalars(select(Customer).where(Customer.id.in_(cids)))
    }
    tenants = {t.id: t for t in db.scalars(select(Tenant).where(Tenant.id.in_(tids)))}
    subs = {
        s.id: s
        for s in db.scalars(select(Subscription).where(Subscription.id.in_(sids)))
    }
    items = {}
    for r in rows:
        k = (r["subscription_id"], r["customer_id"], r["tenant_id"], r["resource_key"])
        item = items.setdefault(
            k,
            dict(
                subscription_id=k[0],
                customer_id=k[1],
                tenant_id=k[2],
                resource_key=k[3],
                resource_id=r["resource_id"],
                subscription_name=subs[k[0]].name,
                customer_name=names.get(k[1], k[1]),
                tenant_name=tenants[k[2]].name,
                raw_costs=[],
                trial_usd=None,
                verified_usd=None,
                records=0,
                trial_records=0,
                pending_records=0,
                received_days=0,
                days=set(),
                last_sync="",
                first_day=r["first_day"],
                last_day=r["last_day"],
                services=set(),
            ),
        )
        item["raw_costs"].append(dict(currency=r["currency"], amount=str(r["raw"])))
        for n in ["trial_usd", "verified_usd"]:
            if r[n] is not None:
                item[n] = (item[n] or Decimal(0)) + r[n]
        for n in ["records", "trial_records", "pending_records"]:
            item[n] += r[n]
        item["days"].update(r["days"])
        item["last_sync"] = max(item["last_sync"], r["last_sync"])
        item["first_day"] = min(item["first_day"], r["first_day"])
        item["last_day"] = max(item["last_day"], r["last_day"])
        item["services"].update(r["services"])
    for item in items.values():
        item["received_days"] = len(item["days"])
        item["days"] = sorted(item["days"])
        for n in ["trial_usd", "verified_usd"]:
            item[n] = str(item[n]) if item[n] is not None else None
        item["raw_costs"].sort(key=lambda c: c["currency"])
        item["services"] = sorted(item["services"])
    return list(items.values())
