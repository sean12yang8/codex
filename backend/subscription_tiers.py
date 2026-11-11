"""Read-only Azure OpenAI quota tiers, independent from financial source state."""

from datetime import datetime, timedelta, timezone
import re

from fastapi import Depends
from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column

from backend.app import (
    Base,
    engine,
    SessionLocal,
    Subscription,
    Tenant,
    Customer,
    current_user,
    db_session,
    require,
    fail,
    now,
    guid,
)
from backend.operations import AzureFailure, azure_request, tenant_token, enqueue, Job

API_VERSION = "2025-12-01"


class SubscriptionTier(Base):
    __tablename__ = "subscription_tiers"
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(default="")
    state: Mapped[str] = mapped_column(default="NOT_SYNCED")
    checked_at: Mapped[str] = mapped_column(default="")
    last_success: Mapped[str] = mapped_column(default="")
    assignment_date: Mapped[str] = mapped_column(default="")
    error: Mapped[str] = mapped_column(default="")
    job_id: Mapped[str] = mapped_column(default="")


Base.metadata.create_all(engine)


def view(row):
    return (
        dict(
            name=row.name or None,
            state=row.state,
            checked_at=row.checked_at,
            last_success=row.last_success,
            assignment_date=row.assignment_date,
            error=row.error,
            job_id=row.job_id,
        )
        if row
        else dict(
            name=None, state="NOT_SYNCED", checked_at="", last_success="", error=""
        )
    )


def snapshots(db, ids=None):
    q = select(SubscriptionTier)
    if ids is not None:
        q = q.where(SubscriptionTier.subscription_id.in_(ids))
    return {r.subscription_id: view(r) for r in db.scalars(q)}


def read_tier(sid, token):
    sid = guid(sid)
    path = (
        f"/subscriptions/{sid}/providers/Microsoft.CognitiveServices/quotaTiers/default"
    )
    try:
        doc = azure_request(
            "GET",
            "https://management.azure.com" + path + "?api-version=" + API_VERSION,
            token,
        )
    except AzureFailure as e:
        raise AzureFailure(
            "TIER_ACCESS_DENIED"
            if e.code == "COST_ACCESS_DENIED"
            else "TIER_NOT_AVAILABLE"
            if e.code == "AZURE_HTTP_404"
            else e.code,
            e.retry_after,
        ) from None
    if not isinstance(doc, dict):
        raise AzureFailure("TIER_INVALID_RESPONSE")
    if str(doc.get("id", "")).rstrip("/").lower() != path.lower():
        raise AzureFailure("TIER_SCOPE_MISMATCH")
    if str(doc.get("type", "")).lower() != "microsoft.cognitiveservices/quotatiers":
        raise AzureFailure("TIER_INVALID_RESPONSE")
    props = doc.get("properties")
    name = props.get("currentTierName") if isinstance(props, dict) else None
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", name
    ):
        raise AzureFailure("TIER_INVALID_RESPONSE")
    assigned = props.get("assignmentDate", "")
    if not isinstance(assigned, str) or len(assigned) > 64:
        raise AzureFailure("TIER_INVALID_RESPONSE")
    return dict(name=name, assignment_date=assigned)


def entry(db, sid):
    row = db.get(SubscriptionTier, sid)
    if row is None:
        row = SubscriptionTier(subscription_id=sid)
        db.add(row)
    return row


def queue(db, user, sub, force=False):
    row = db.get(SubscriptionTier, sub.id)
    if not force and row and row.checked_at:
        if datetime.fromisoformat(row.checked_at) > datetime.now(
            timezone.utc
        ) - timedelta(minutes=15):
            return None
    day = datetime.now(timezone.utc).date().isoformat()
    job = enqueue(db, user, "tier", "subscription", sub.id, day, day, priority=15)
    row = entry(db, sub.id)
    row.state = job.state
    row.job_id = job.id
    row.error = ""
    return job


def failure(db, job, code):
    if db.get(Subscription, job.scope_id) is None:
        return
    row = entry(db, job.scope_id)
    row.state = job.state
    row.checked_at = now()
    row.error = code
    row.job_id = job.id


def process(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        sub = db.get(Subscription, job.scope_id)
        if job.scope_type != "subscription" or sub is None:
            raise AzureFailure("SCOPE_REMOVED")
        tenant = db.get(Tenant, sub.tenant_id)
        if (
            not sub.active
            or not tenant.active
            or not db.get(Customer, tenant.customer_id).active
        ):
            raise AzureFailure("SCOPE_INACTIVE")
        if not tenant.secret:
            raise AzureFailure("TIER_CREDENTIALS_MISSING")
        tenant_id = tenant.id
        result = read_tier(sub.id, tenant_token(tenant))
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        if job.cancel:
            job.state = "CANCELLED"
            failure(db, job, "")
            return
        sub = db.get(Subscription, job.scope_id)
        if sub is None:
            return
        tenant = db.get(Tenant, sub.tenant_id)
        if (
            sub.tenant_id != tenant_id
            or not sub.active
            or not tenant.active
            or not db.get(Customer, tenant.customer_id).active
        ):
            raise AzureFailure("SCOPE_CHANGED")
        row = entry(db, sub.id)
        row.name = result["name"]
        row.assignment_date = result["assignment_date"]
        row.state = "SUCCEEDED"
        row.checked_at = row.last_success = now()
        row.error = ""
        row.job_id = job.id
        job.state = "SUCCEEDED"
        job.updated = now()
        job.progress = "1/1"
        job.error = ""
        job.attempts = 0


def install(app):
    @app.post("/api/v1/subscriptions/{id}/tier/refresh", status_code=202)
    def refresh(id: str, db=Depends(db_session), user=Depends(current_user)):
        sub = require(db, Subscription, id)
        tenant = require(db, Tenant, sub.tenant_id)
        if (
            not sub.active
            or not tenant.active
            or not require(db, Customer, tenant.customer_id).active
        ):
            fail("INACTIVE", "客户、租户或订阅已停用")
        if not tenant.secret:
            fail("TIER_CREDENTIALS_MISSING", "请先配置租户凭据")
        job = queue(db, user, sub, force=True)
        return dict(job_id=job.id, state=job.state)
