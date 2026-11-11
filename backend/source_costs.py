"""Native source costs and explicitly provisional USD estimates; neither attests price basis."""

from collections import defaultdict
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import select, text
from backend.app import URL, Customer, days


def sqlite_rows(db, start, end, cid, tid, sid, granularity):
    from backend.app import Fact, Tenant, Subscription, Batch, Discount, Fx
    from backend.operations import Ownership
    from backend.selective_purge import PurgedRange, blocked

    tenants = {t.id: t for t in db.scalars(select(Tenant))}
    subs = {s.id: s for s in db.scalars(select(Subscription))}
    batches = {b.id: b for b in db.scalars(select(Batch))}
    history, purged, discounts, rates = (defaultdict(list) for _ in range(4))
    for o in db.scalars(select(Ownership).order_by(Ownership.day)):
        history[o.subscription_id].append(o)
    for p in db.scalars(select(PurgedRange)):
        purged[p.subscription_id].append(p)
    for d in db.scalars(select(Discount).order_by(Discount.day)):
        discounts[d.customer_id].append(d)
    for x in db.scalars(select(Fx).order_by(Fx.day)):
        rates[x.currency].append(x)
    groups = {}
    for f in db.scalars(select(Fact)):
        sub = subs[f.subscription_id]
        if (sid and sub.id != sid) or blocked(purged[sub.id], f.day):
            continue
        owners = [o for o in history[sub.id] if o.day <= f.day]
        owner_tid = owners[-1].tenant_id if owners else sub.tenant_id
        owner_cid = (
            owners[-1].customer_id if owners else tenants[sub.tenant_id].customer_id
        )
        if (cid and owner_cid != cid) or (tid and owner_tid != tid):
            continue
        ds = [d for d in discounts[owner_cid] if d.day <= f.day]
        xs = [x for x in rates[f.currency] if x.day <= f.day]
        current = [
            d
            for d in discounts[owner_cid]
            if d.day <= datetime.now(timezone.utc).date().isoformat()
        ]
        fallback = not ds and bool(current)
        estimate_discounts = ds or current
        estimate_status = (
            "DISCOUNT_MISSING"
            if not estimate_discounts
            else "FX_RATE_MISSING"
            if f.currency != "USD" and not xs
            else "READY"
        )
        verified = batches[f.batch_id].verified
        status = (
            "UNKNOWN_PRICE_BASIS"
            if not verified
            else "DISCOUNT_MISSING"
            if not ds
            else "FX_RATE_MISSING"
            if f.currency != "USD" and not xs
            else "VERIFIED"
        )
        amount = Decimal(f.amount)
        estimate = (
            amount
            * Decimal(estimate_discounts[-1].factor)
            * (Decimal(1) if f.currency == "USD" else Decimal(xs[-1].rate))
            if estimate_status == "READY"
            else None
        )
        keys = [("lifetime", None)]
        if start <= f.day <= end:
            period = f.day[:7] if granularity == "month" else f.day
            keys += [
                ("total", None),
                ("period", period),
                ("customer", owner_cid),
                ("service", f.service),
                ("status", status),
                ("estimate_status", estimate_status),
            ]
        for bucket, key in keys:
            entry = groups.setdefault(
                (bucket, f.currency, key),
                dict(
                    bucket=bucket,
                    currency=f.currency,
                    key=key,
                    amount=Decimal(0),
                    count=0,
                    estimate=Decimal(0),
                    estimate_count=0,
                    pending_count=0,
                    fallback_count=0,
                ),
            )
            entry["amount"] += amount
            entry["count"] += 1
            if estimate is not None:
                entry["estimate"] += estimate
                entry["estimate_count"] += 1
                entry["pending_count"] += int(not verified or fallback)
                entry["fallback_count"] += int(fallback)
    return list(groups.values())


def summary(db, start, end, cid="", tid="", sid="", granularity="day"):
    if URL.startswith("sqlite"):
        rows = sqlite_rows(db, start, end, cid, tid, sid, granularity)
    else:
        period = "SUBSTRING(day,1,7)" if granularity == "month" else "day"
        statement = text(
            """
WITH source AS MATERIALIZED (
 SELECT f.day,f.currency,f.service,CAST(f.amount AS NUMERIC) AS raw,b.verified,
        COALESCE(o.customer_id,t.customer_id) AS customer_id,
        CASE WHEN NOT b.verified THEN 'UNKNOWN_PRICE_BASIS'
             WHEN d.factor IS NULL THEN 'DISCOUNT_MISSING'
             WHEN f.currency<>'USD' AND x.rate IS NULL THEN 'FX_RATE_MISSING'
             ELSE 'VERIFIED' END AS status,
        (d.factor IS NULL AND cd.factor IS NOT NULL) AS discount_fallback,
        CASE WHEN COALESCE(d.factor,cd.factor) IS NULL THEN 'DISCOUNT_MISSING'
             WHEN f.currency<>'USD' AND x.rate IS NULL THEN 'FX_RATE_MISSING' ELSE 'READY' END AS estimate_status,
        CASE WHEN COALESCE(d.factor,cd.factor) IS NOT NULL AND (f.currency='USD' OR x.rate IS NOT NULL)
             THEN CAST(f.amount AS NUMERIC)*CAST(COALESCE(d.factor,cd.factor) AS NUMERIC)*
                  CASE WHEN f.currency='USD' THEN 1::NUMERIC ELSE CAST(x.rate AS NUMERIC) END
             ELSE NULL END AS estimate
 FROM facts f JOIN subscriptions s ON s.id=f.subscription_id JOIN tenants t ON t.id=s.tenant_id
 JOIN batches b ON b.id=f.batch_id
 LEFT JOIN LATERAL (SELECT customer_id,tenant_id FROM ownership_history o WHERE o.subscription_id=f.subscription_id AND o.day<=f.day ORDER BY o.day DESC LIMIT 1) o ON TRUE
 LEFT JOIN LATERAL (SELECT factor FROM discounts d WHERE d.customer_id=COALESCE(o.customer_id,t.customer_id) AND d.day<=f.day ORDER BY d.day DESC LIMIT 1) d ON TRUE
 LEFT JOIN LATERAL (SELECT factor FROM discounts cd WHERE cd.customer_id=COALESCE(o.customer_id,t.customer_id) AND cd.day<=:today ORDER BY cd.day DESC LIMIT 1) cd ON TRUE
 LEFT JOIN LATERAL (SELECT rate FROM fx x WHERE x.currency=f.currency AND x.day<=f.day ORDER BY x.day DESC LIMIT 1) x ON TRUE
 WHERE (:cid='' OR COALESCE(o.customer_id,t.customer_id)=:cid)
   AND (:tid='' OR COALESCE(o.tenant_id,s.tenant_id)=:tid) AND (:sid='' OR s.id=:sid)
   AND NOT EXISTS (SELECT 1 FROM purged_ownership_ranges p WHERE p.subscription_id=f.subscription_id AND p.start<=f.day AND f.day<p."end")
), selected AS (SELECT *,"""
            + period
            + """ AS period FROM source WHERE day>=:start AND day<=:end)
SELECT 'lifetime' AS bucket,currency,NULL::TEXT AS key,SUM(raw) AS amount,COUNT(*) AS count,
       SUM(estimate) AS estimate,COUNT(estimate) AS estimate_count,COUNT(estimate) FILTER (WHERE NOT verified OR discount_fallback) AS pending_count,COUNT(estimate) FILTER (WHERE discount_fallback) AS fallback_count
FROM source GROUP BY currency
UNION ALL
SELECT CASE WHEN GROUPING(period)=0 THEN 'period' WHEN GROUPING(customer_id)=0 THEN 'customer'
            WHEN GROUPING(service)=0 THEN 'service' WHEN GROUPING(status)=0 THEN 'status'
            WHEN GROUPING(estimate_status)=0 THEN 'estimate_status' ELSE 'total' END,
       currency,COALESCE(period,customer_id,service,status,estimate_status),SUM(raw),COUNT(*),
       SUM(estimate),COUNT(estimate),COUNT(estimate) FILTER (WHERE NOT verified OR discount_fallback),COUNT(estimate) FILTER (WHERE discount_fallback)
FROM selected GROUP BY GROUPING SETS ((currency),(currency,period),(currency,customer_id),(currency,service),(currency,status),(currency,estimate_status))
"""
        )
        rows = list(
            db.execute(
                statement,
                dict(
                    start=start,
                    end=end,
                    cid=cid,
                    tid=tid,
                    sid=sid,
                    today=datetime.now(timezone.utc).date().isoformat(),
                ),
            ).mappings()
        )

    axis = sorted({d[:7] if granularity == "month" else d for d in days(start, end)})
    names = {c.id: c.name for c in db.scalars(select(Customer))}
    currencies, reasons = {}, defaultdict(int)
    estimate = dict(
        total=Decimal(0),
        lifetime=Decimal(0),
        records=0,
        lifetime_records=0,
        pending_records=0,
        pending_lifetime_records=0,
        excluded=0,
        fallback_records=0,
        fallback_lifetime_records=0,
    )
    estimate_daily, estimate_top, estimate_services = (
        defaultdict(Decimal) for _ in range(3)
    )
    estimate_reasons = defaultdict(int)
    for r in rows:
        group = currencies.setdefault(
            r["currency"],
            {
                "currency": r["currency"],
                "total": None,
                "lifetime": None,
                "records": 0,
                "days": {},
                "top": [],
                "services": [],
            },
        )
        bucket, key = r["bucket"], r["key"]
        if bucket == "lifetime":
            group["lifetime"] = str(r["amount"])
            estimate["lifetime"] += r["estimate"] or Decimal(0)
            estimate["lifetime_records"] += r["estimate_count"]
            estimate["pending_lifetime_records"] += r["pending_count"]
            estimate["fallback_lifetime_records"] += r["fallback_count"]
        elif bucket == "total":
            group["total"], group["records"] = str(r["amount"]), r["count"]
            estimate["total"] += r["estimate"] or Decimal(0)
            estimate["records"] += r["estimate_count"]
            estimate["pending_records"] += r["pending_count"]
            estimate["fallback_records"] += r["fallback_count"]
            estimate["excluded"] += r["count"] - r["estimate_count"]
        elif bucket == "period":
            group["days"][key] = str(r["amount"])
            if r["estimate_count"]:
                estimate_daily[key] += r["estimate"]
        elif bucket == "customer":
            group["top"].append(
                {"id": key, "name": names.get(key, key), "value": str(r["amount"])}
            )
            if r["estimate_count"]:
                estimate_top[key] += r["estimate"]
        elif bucket == "service":
            group["services"].append({"name": key, "value": str(r["amount"])})
            if r["estimate_count"]:
                estimate_services[key] += r["estimate"]
        elif bucket == "status" and key != "VERIFIED":
            reasons[key] += r["count"]
        elif bucket == "estimate_status" and key != "READY":
            estimate_reasons[key] += r["count"]
    for group in currencies.values():
        group["trend"] = [{"day": d, "value": group["days"].get(d)} for d in axis]
        del group["days"]
        group["top"] = sorted(
            group["top"], key=lambda r: (-Decimal(r["value"]), r["id"])
        )[:5]
        group["services"].sort(key=lambda r: (-Decimal(r["value"]), r["name"]))
    estimate["total"] = str(estimate["total"]) if estimate["records"] else None
    estimate["lifetime"] = (
        str(estimate["lifetime"]) if estimate["lifetime_records"] else None
    )
    estimate["status"] = (
        "UNVERIFIED"
        if estimate["pending_lifetime_records"]
        else "VERIFIED"
        if estimate["lifetime_records"]
        else "UNAVAILABLE"
    )
    estimate["assumption"] = "PRE_CUSTOMER_DISCOUNT"
    estimate["discount_policy"] = "HISTORICAL_WITH_CURRENT_FALLBACK"
    estimate["excluded_reasons"] = dict(estimate_reasons)
    estimate["trend"] = [
        {"day": d, "value": str(estimate_daily[d]) if d in estimate_daily else None}
        for d in axis
    ]
    estimate["top"] = [
        {"id": k, "name": names.get(k, k), "value": str(v)}
        for k, v in sorted(estimate_top.items(), key=lambda item: (-item[1], item[0]))[
            :5
        ]
    ]
    estimate["services"] = [
        {"name": k, "value": str(v)}
        for k, v in sorted(
            estimate_services.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return {
        "records": sum(g["records"] for g in currencies.values()),
        "currencies": sorted(currencies.values(), key=lambda g: g["currency"]),
        "excluded_reasons": dict(reasons),
        "estimate": estimate,
    }
