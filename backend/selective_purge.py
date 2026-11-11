"""Erase transferred historical scope without deleting the current owner's costs."""

import hashlib
import json
from collections import defaultdict
from uuid import uuid4
from sqlalchemy import ForeignKey, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column
from backend.app import Base, engine, Batch, Fact, Coverage, now, Tenant, Subscription
from backend.operations import Ownership


class PurgedRange(Base):
    __tablename__ = "purged_ownership_ranges"
    __table_args__ = (
        UniqueConstraint("subscription_id", "start", "end", "scope_hash"),
    )
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    start: Mapped[str]
    end: Mapped[str]
    scope_hash: Mapped[str]


Base.metadata.create_all(engine)


def ranges(db, sid):
    return list(
        db.scalars(select(PurgedRange).where(PurgedRange.subscription_id == sid))
    )


def blocked(values, day):
    return any(r.start <= day < r.end for r in values)


def erase_transferred(db, kind, id, full_sub_ids):
    if kind not in ["customers", "tenants"]:
        return
    predicate = (
        Ownership.customer_id == id
        if kind == "customers"
        else Ownership.tenant_id == id
    )
    affected = set(
        db.scalars(select(Ownership.subscription_id).where(predicate))
    ) - set(full_sub_ids)
    fingerprint = hashlib.sha256((kind + ":" + id).encode()).hexdigest()
    for sid in affected:
        history = list(
            db.scalars(
                select(Ownership)
                .where(Ownership.subscription_id == sid)
                .order_by(Ownership.day)
            )
        )
        intervals = []
        for i, h in enumerate(history):
            match = h.customer_id == id if kind == "customers" else h.tenant_id == id
            if match:
                intervals.append(
                    (
                        h.day,
                        history[i + 1].day if i + 1 < len(history) else "9999-12-31",
                    )
                )

        def erase_day(day):
            return any(a <= day < b for a, b in intervals)

        for a, b in intervals:
            if not db.scalar(
                select(PurgedRange).where(
                    PurgedRange.subscription_id == sid,
                    PurgedRange.start == a,
                    PurgedRange.end == b,
                    PurgedRange.scope_hash == fingerprint,
                )
            ):
                db.add(
                    PurgedRange(
                        subscription_id=sid, start=a, end=b, scope_hash=fingerprint
                    )
                )
        active = list(db.scalars(select(Fact).where(Fact.subscription_id == sid)))
        for fact in active:
            if erase_day(fact.day):
                db.delete(fact)
        from backend import resource_costs

        resource_costs.purge(db, [sid], intervals)
        for cov in db.scalars(select(Coverage).where(Coverage.subscription_id == sid)):
            if erase_day(cov.day):
                db.delete(cov)
        db.flush()
        for source in list(
            db.scalars(select(Batch).where(Batch.subscription_id == sid))
        ):
            raw = json.loads(source.rows)
            owned = [r for r in raw if erase_day(r["usage_date"])]
            if not owned and raw:
                continue
            kept = defaultdict(list)
            for row in raw:
                if not erase_day(row["usage_date"]):
                    kept[row["usage_date"]].append(row)
            if not raw:
                # Legacy synthetic batches may have facts but no raw payload.
                for f in db.scalars(select(Fact).where(Fact.batch_id == source.id)):
                    if not erase_day(f.day):
                        kept[f.day].append(
                            {
                                "subscription_id": sid,
                                "usage_date": f.day,
                                "service": f.service,
                                "currency": f.currency,
                                "amount": f.amount,
                            }
                        )
            for day, records in kept.items():
                digest = hashlib.sha256(
                    ("retained:" + source.id + ":" + day + ":" + fingerprint).encode()
                ).hexdigest()
                new = Batch(
                    digest=digest,
                    filename="retained-cost-after-erasure",
                    created=now(),
                    start=day,
                    end=day,
                    subscription_id=sid,
                    verified=source.verified,
                    complete=source.complete,
                    evidence="Retained scope; prior immutable digest " + source.digest,
                    reason="Selective erasure of transferred historical scope",
                    rows=json.dumps(records),
                    row_count=len(records),
                )
                db.add(new)
                db.flush()
                db.execute(
                    update(Fact)
                    .where(Fact.batch_id == source.id, Fact.day == day)
                    .values(batch_id=new.id)
                )
                db.execute(
                    update(Coverage)
                    .where(Coverage.batch_id == source.id, Coverage.day == day)
                    .values(batch_id=new.id)
                )
            db.delete(source)
            db.flush()
        current = db.get(Subscription, sid)
        current_tenant = db.get(Tenant, current.tenant_id)
        removing_current = (
            current_tenant.customer_id == id
            if kind == "customers"
            else current_tenant.id == id
        )
        if removing_current:
            retained = [
                h
                for h in history
                if (h.customer_id != id if kind == "customers" else h.tenant_id != id)
            ]
            if retained:
                current.tenant_id = retained[-1].tenant_id
                current.active = False
                from backend.operations import AzureState

                status = db.get(AzureState, sid)
                if status:
                    status.cost = status.sync = "ARCHIVED"
                    status.error = ""
        # Remove foreign keys to the deleted owner; purged ranges permanently block
        # reinterpretation of those dates as belonging to the current customer.
        for h in history:
            if h.customer_id == id if kind == "customers" else h.tenant_id == id:
                db.delete(h)
    db.flush()


def preview_history_count(db, kind, id, full_sub_ids, model=Fact):
    if kind not in ["customers", "tenants"]:
        return 0
    predicate = (
        Ownership.customer_id == id
        if kind == "customers"
        else Ownership.tenant_id == id
    )
    affected = set(
        db.scalars(select(Ownership.subscription_id).where(predicate))
    ) - set(full_sub_ids)
    total = 0
    for sid in affected:
        history = list(
            db.scalars(
                select(Ownership)
                .where(Ownership.subscription_id == sid)
                .order_by(Ownership.day)
            )
        )
        for day in db.scalars(select(model.day).where(model.subscription_id == sid)):
            records = [h for h in history if h.day <= day]
            if records and (
                records[-1].customer_id == id
                if kind == "customers"
                else records[-1].tenant_id == id
            ):
                total += 1
    return total
