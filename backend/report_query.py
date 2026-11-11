"""Bounded-memory PostgreSQL report aggregation, with exact Decimal values."""

from sqlalchemy import text
from backend.app import calculate, now, URL


def rows(db, q):
    dimension = q["dimension"]
    granularity = q["granularity"]
    generated = now()
    headers = [
        "period",
        dimension + "_id",
        dimension,
        "currency",
        "discount",
        "discount_effective",
        "fx_rate",
        "fx_effective",
        "status",
        "coverage",
        "data_timestamp",
        "generated_at",
        "raw_amount",
        "acr_usd",
    ]
    yield headers
    if URL.startswith("sqlite"):
        from decimal import Decimal
        from sqlalchemy import select
        from backend.app import Tenant, Customer, Subscription, Batch

        grouped = {}
        tenants = {t.id: t for t in db.scalars(select(Tenant))}
        customers = {c.id: c for c in db.scalars(select(Customer))}
        subscriptions = {s.id: s for s in db.scalars(select(Subscription))}
        batches = {b.id: b for b in db.scalars(select(Batch))}
        report = calculate(
            db,
            q["start"],
            q["end"],
            q.get("customer_id", ""),
            q.get("tenant_id", ""),
            q.get("subscription_id", ""),
        )["report"]
        for r in report:
            if q.get("service") and r["service"] != q["service"]:
                continue
            cid = tenants[r["tenant_id"]].customer_id
            dim = {
                "customer": cid,
                "tenant": r["tenant_id"],
                "subscription": r["subscription_id"],
                "service": r["service"],
            }[dimension]
            period = r["usage_date"][:7] if granularity == "month" else r["usage_date"]
            key = (
                period,
                dim,
                r["currency"],
                r["discount"],
                r["discount_effective"],
                r["fx_rate"],
                r["fx_effective"],
                r["status"],
                r["coverage"],
            )
            a = grouped.setdefault(key, [Decimal(0), Decimal(0), ""])
            a[0] += Decimal(r["raw_amount"])
            a[2] = max(a[2], batches[r["batch_id"]].created)
            if r["acr_usd"]:
                a[1] += Decimal(r["acr_usd"])
        for key, values in grouped.items():
            label = {
                "customer": lambda: customers[key[1]].name,
                "tenant": lambda: tenants[key[1]].name,
                "subscription": lambda: subscriptions[key[1]].name,
                "service": lambda: key[1],
            }[dimension]()
            yield [
                key[0],
                key[1],
                label,
                *key[2:],
                values[2],
                generated,
                str(values[0]),
                str(values[1]) if key[-2] == "VERIFIED" else "",
            ]
        return
    key, label = {
        "customer": ("customer_id", "customer_name"),
        "tenant": ("tenant_id", "tenant_name"),
        "subscription": ("subscription_id", "subscription_name"),
        "service": ("service", "service"),
    }[dimension]
    period = "SUBSTRING(day,1,7)" if granularity == "month" else "day"
    sql = (
        """
WITH base AS (
 SELECT f.day,f.subscription_id,f.service,f.currency,CAST(f.amount AS NUMERIC) AS raw,
        s.name AS subscription_name,COALESCE(o.customer_id,t.customer_id) AS customer_id,
        COALESCE(o.tenant_id,s.tenant_id) AS tenant_id,c.name AS customer_name,h.name AS tenant_name,
        d.factor,d.day AS discount_day,
        CASE WHEN f.currency='USD' THEN '1' ELSE x.rate END AS rate,
        CASE WHEN f.currency='USD' THEN '' ELSE x.day END AS fx_day,
        CASE WHEN NOT b.verified THEN 'UNKNOWN_PRICE_BASIS' WHEN d.factor IS NULL THEN 'DISCOUNT_MISSING'
             WHEN f.currency<>'USD' AND x.rate IS NULL THEN 'FX_RATE_MISSING' ELSE 'VERIFIED' END AS status,
        CASE WHEN b.complete THEN 'COMPLETE_DECLARED' ELSE 'UNKNOWN' END AS coverage,b.created AS data_timestamp
 FROM facts f JOIN subscriptions s ON s.id=f.subscription_id JOIN tenants t ON t.id=s.tenant_id
 JOIN batches b ON b.id=f.batch_id
 LEFT JOIN LATERAL (SELECT customer_id,tenant_id FROM ownership_history o WHERE o.subscription_id=f.subscription_id AND o.day<=f.day ORDER BY o.day DESC LIMIT 1) o ON TRUE
 JOIN customers c ON c.id=COALESCE(o.customer_id,t.customer_id)
 JOIN tenants h ON h.id=COALESCE(o.tenant_id,s.tenant_id)
 LEFT JOIN LATERAL (SELECT factor,day FROM discounts d WHERE d.customer_id=c.id AND d.day<=f.day ORDER BY d.day DESC LIMIT 1) d ON TRUE
 LEFT JOIN LATERAL (SELECT rate,day FROM fx x WHERE x.currency=f.currency AND x.day<=f.day ORDER BY x.day DESC LIMIT 1) x ON TRUE
 WHERE f.day>=:start AND f.day<=:end AND (:cid='' OR c.id=:cid)
   AND (:tid='' OR h.id=:tid) AND (:sid='' OR s.id=:sid) AND (:service='' OR f.service=:service)
   AND NOT EXISTS (SELECT 1 FROM purged_ownership_ranges p WHERE p.subscription_id=f.subscription_id AND p.start<=f.day AND f.day<p."end")
)
SELECT """
        + period
        + " AS period,"
        + key
        + " AS dimension_id,"
        + label
        + """ AS dimension_name,currency,factor,discount_day,rate,fx_day,status,coverage,MAX(data_timestamp) AS data_timestamp,
       SUM(raw) AS raw_amount,SUM(CASE WHEN status='VERIFIED' THEN raw*CAST(factor AS NUMERIC)*CAST(rate AS NUMERIC) ELSE NULL END) AS acr_usd
FROM base GROUP BY """
        + period
        + ","
        + key
        + ("," + label if label != key else "")
        + """,currency,factor,discount_day,rate,fx_day,status,coverage
ORDER BY period,dimension_id,currency
"""
    )
    params = {
        "start": q["start"],
        "end": q["end"],
        "cid": q.get("customer_id", ""),
        "tid": q.get("tenant_id", ""),
        "sid": q.get("subscription_id", ""),
        "service": q.get("service", ""),
    }
    result = db.execute(
        text(sql), params, execution_options={"stream_results": True, "yield_per": 1000}
    )
    try:
        for r in result.mappings():
            yield [
                r["period"],
                r["dimension_id"],
                r["dimension_name"],
                r["currency"],
                r["factor"] or "",
                r["discount_day"] or "",
                r["rate"] or "",
                r["fx_day"] or "",
                r["status"],
                r["coverage"],
                r["data_timestamp"],
                generated,
                str(r["raw_amount"]),
                str(r["acr_usd"]) if r["acr_usd"] is not None else "",
            ]
    finally:
        result.close()
