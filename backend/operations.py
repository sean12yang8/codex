"""Durable operations, Azure adapters and administration. No secrets in API output."""

import csv
import hashlib
import io
import json
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from uuid import uuid4

import requests
from fastapi import Depends, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column
from backend.app import (
    Fx,
    Base,
    engine,
    SessionLocal,
    Customer,
    Tenant,
    Subscription,
    User,
    LoginSession,
    Batch,
    Coverage,
    Fact,
    Discount,
    DATA,
    db_session,
    current_user,
    admin,
    audit,
    now,
    fail,
    require,
    scalar,
    guid,
    cipher,
    dec,
    hash_password,
    calculate,
    valid_range,
)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AzureState(Base):
    __tablename__ = "azure_states"
    id: Mapped[str] = mapped_column(primary_key=True)
    auth: Mapped[str] = mapped_column(default="PENDING")
    discovery: Mapped[str] = mapped_column(default="PENDING")
    cost: Mapped[str] = mapped_column(default="PENDING")
    sync: Mapped[str] = mapped_column(default="PENDING")
    last_seen: Mapped[str] = mapped_column(default="")
    last_sync: Mapped[str] = mapped_column(default="")
    error: Mapped[str] = mapped_column(default="")


class Job(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str]
    scope_type: Mapped[str]
    scope_id: Mapped[str] = mapped_column(index=True)
    start: Mapped[str]
    end: Mapped[str]
    state: Mapped[str] = mapped_column(default="QUEUED", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[str] = mapped_column(Text, default="{}")
    created: Mapped[str] = mapped_column(default=now)
    updated: Mapped[str] = mapped_column(default=now)
    available: Mapped[str] = mapped_column(default=now)
    lease_until: Mapped[str] = mapped_column(default="")
    actor: Mapped[str]
    error: Mapped[str] = mapped_column(default="")
    cancel: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[str] = mapped_column(default="")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    event_key: Mapped[str] = mapped_column(unique=True)
    customer_id: Mapped[str]
    rule: Mapped[str]
    state: Mapped[str] = mapped_column(default="OPEN")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[str] = mapped_column(Text)
    created: Mapped[str] = mapped_column(default=now)
    updated: Mapped[str] = mapped_column(default=now)
    muted_until: Mapped[str] = mapped_column(default="")


class Delivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    event_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"))
    channel: Mapped[str]
    state: Mapped[str] = mapped_column(default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[str] = mapped_column(default=now)
    error: Mapped[str] = mapped_column(default="")


class Ownership(Base):
    __tablename__ = "ownership_history"
    __table_args__ = (UniqueConstraint("subscription_id", "day"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    day: Mapped[str]


class Tombstone(Base):
    __tablename__ = "delete_tombstones"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str]
    object_id: Mapped[str]
    created: Mapped[str] = mapped_column(default=now)


class ExportTask(Base):
    __tablename__ = "export_tasks"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    owner: Mapped[str]
    query: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(default="QUEUED")
    created: Mapped[str] = mapped_column(default=now)
    error: Mapped[str] = mapped_column(default="")


Base.metadata.create_all(engine)
ACTIVE = ["QUEUED", "RUNNING", "RETRY_WAIT"]


def setting(db, key, default=None):
    s = db.get(Setting, key)
    return json.loads(s.value) if s else default


def put_setting(db, key, value):
    s = db.get(Setting, key)
    if s:
        s.value = json.dumps(value, ensure_ascii=False)
    else:
        db.add(Setting(key=key, value=json.dumps(value, ensure_ascii=False)))


def state(db, id):
    s = db.get(AzureState, id)
    if not s:
        s = AzureState(id=id)
        db.add(s)
        db.flush()
    return s


def enqueue(db, user, kind, scope_type, scope_id, start=None, end=None, priority=10):
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    start = (
        start
        or (
            date.fromisoformat(end) - timedelta(days=6 if kind == "verify" else 364)
        ).isoformat()
    )
    valid_range(date.fromisoformat(start), date.fromisoformat(end))
    model = {"tenant": Tenant, "subscription": Subscription, "customer": Customer}.get(
        scope_type
    )
    if not model:
        fail("INVALID_SCOPE", "无效任务范围")
    obj = require(db, model, scope_id)
    if not obj.active:
        fail("INACTIVE", "对象已停用")
    # An identical active request is idempotent. One elected worker serializes overlap.
    existing = db.scalar(
        select(Job).where(
            Job.kind == kind,
            Job.scope_type == scope_type,
            Job.scope_id == scope_id,
            Job.start == start,
            Job.end == end,
            Job.state.in_(ACTIVE),
        )
    )
    if existing:
        return existing
    j = Job(
        kind=kind,
        scope_type=scope_type,
        scope_id=scope_id,
        start=start,
        end=end,
        priority=priority,
        actor=user.id,
    )
    db.add(j)
    db.flush()
    audit(db, user, "QUEUE_" + kind.upper(), j.id)
    return j


class AzureFailure(Exception):
    def __init__(self, code, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


def azure_retry_after(headers, status_code):
    """Honor standard and Cost Management retry hints; all durations are seconds."""
    delays = []
    for name, value in headers.items():
        name = name.lower()
        if name != "retry-after" and not (
            name.startswith("x-ms-ratelimit-microsoft.costmanagement-")
            and name.endswith("-retry-after")
        ):
            continue
        value = str(value).strip()
        if value.isdigit():
            delays.append(int(value))
        elif name == "retry-after":
            try:
                retry = parsedate_to_datetime(value)
                if retry.tzinfo is not None:
                    import math

                    delays.append(
                        math.ceil((retry - datetime.now(timezone.utc)).total_seconds())
                    )
            except (ValueError, TypeError, OverflowError):
                pass
    return max([0, *delays]) if delays else (60 if status_code == 429 else 0)


def azure_request(method, url, token=None, **kwargs):
    parsed = urlparse(url)
    # Pagination, redirects and bearer tokens must stay on the intended Azure endpoint.
    if parsed.scheme != "https" or parsed.hostname != "management.azure.com":
        raise AzureFailure("UNSAFE_AZURE_URL")
    headers = {"Authorization": "Bearer " + token}
    if "/providers/microsoft.costmanagement/" in parsed.path.lower():
        # A stable application identifier; never rotate it to evade quotas.
        headers["ClientType"] = "AzureLens"
    try:
        r = requests.request(
            method,
            url,
            headers=headers,
            timeout=(10, 60),
            allow_redirects=False,
            **kwargs,
        )
    except requests.RequestException:
        raise AzureFailure("AZURE_NETWORK_ERROR") from None
    if not r.ok:
        raise AzureFailure(
            "COST_ACCESS_DENIED"
            if r.status_code == 403
            else "AZURE_HTTP_" + str(r.status_code),
            azure_retry_after(r.headers, r.status_code),
        )
    try:
        return json.loads(r.text, parse_float=Decimal)
    except ValueError:
        raise AzureFailure("AZURE_INVALID_RESPONSE") from None


_token_cache = {}


def tenant_token(tenant, resource="https://management.azure.com/.default"):
    if not tenant.secret or not tenant.client_id:
        raise AzureFailure("SP_NOT_CONFIGURED")
    import time

    cache_key = (
        tenant.id,
        tenant.client_id,
        hashlib.sha256(tenant.secret.encode()).hexdigest(),
        resource,
    )
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        secret = cipher().decrypt(tenant.secret.encode()).decode()
        r = requests.post(
            f"https://login.microsoftonline.com/{guid(tenant.id)}/oauth2/v2.0/token",
            data={
                "client_id": tenant.client_id,
                "client_secret": secret,
                "scope": resource,
                "grant_type": "client_credentials",
            },
            timeout=(10, 30),
            allow_redirects=False,
        )
        if not r.ok:
            raise AzureFailure("SP_AUTH_FAILED")
        doc = r.json()
        token = doc["access_token"]
        _token_cache[cache_key] = (
            token,
            time.time() + max(0, min(int(doc.get("expires_in", 3600)), 3600) - 120),
        )
        return token
    except (requests.RequestException, KeyError, ValueError):
        raise AzureFailure("SP_AUTH_FAILED") from None


def discover(tenant, token):
    url = "https://management.azure.com/subscriptions?api-version=2022-12-01"
    result = []
    seen = set()
    while url:
        if url in seen or len(seen) > 100:
            raise AzureFailure("DISCOVERY_PAGINATION_INVALID")
        seen.add(url)
        page = azure_request("GET", url, token)
        for s in page.get("value", []):
            if s.get("tenantId", "").lower() != tenant.id.lower():
                continue
            result.append(
                {
                    "id": guid(s["subscriptionId"]),
                    "name": s.get("displayName", s["subscriptionId"]),
                    "status": s.get("state", "Unknown"),
                }
            )
        url = page.get("nextLink")
    return result


def query_cost(subscription, token, start, end):
    url = f"https://management.azure.com/subscriptions/{guid(subscription)}/providers/Microsoft.CostManagement/query?api-version=2023-03-01"
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start + "T00:00:00Z", "to": end + "T23:59:59Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ServiceName"}],
        },
    }
    result = []
    seen = set()
    while url:
        if url in seen or len(seen) > 200:
            raise AzureFailure("COST_PAGINATION_INVALID")
        seen.add(url)
        page = azure_request("POST", url, token, json=body)
        p = page.get("properties", {})
        columns = [c["name"] for c in p.get("columns", [])]
        amount = next(
            (
                c
                for c in ["Cost", "PreTaxCost", "CostInBillingCurrency"]
                if c in columns
            ),
            None,
        )
        if not amount or not {"UsageDate", "Currency", "ServiceName"} <= set(columns):
            raise AzureFailure("UNSUPPORTED_COST_SCHEMA")
        for values in p.get("rows", []):
            try:
                if len(values) != len(columns):
                    raise ValueError()
                row = dict(zip(columns, values))
                day = (
                    datetime.strptime(str(row["UsageDate"]), "%Y%m%d")
                    .date()
                    .isoformat()
                )
                raw_amount = str(dec(row[amount], max_scale=60))
            except HTTPException:
                raise AzureFailure("COST_INVALID_AMOUNT") from None
            except (ValueError, KeyError, TypeError):
                raise AzureFailure("COST_INVALID_ROW") from None
            if not start <= day <= end:
                raise AzureFailure("COST_OUTSIDE_COVERAGE")
            result.append(
                {
                    "subscription_id": subscription,
                    "usage_date": day,
                    "service": str(row["ServiceName"]),
                    "currency": str(row["Currency"]),
                    "amount": raw_amount,
                }
            )
        url = p.get("nextLink")
    return result


def normalize_csv(data):
    """Strict mappings: platform template, modern billing currency, legacy usage schema."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    schemas = [
        ["subscription_id", "usage_date", "service", "currency", "amount"],
        [
            "SubscriptionId",
            "Date",
            "ServiceName",
            "BillingCurrencyCode",
            "CostInBillingCurrency",
        ],
        ["SubscriptionId", "UsageDateTime", "MeterCategory", "Currency", "PreTaxCost"],
    ]
    columns = next((s for s in schemas if set(s) <= set(reader.fieldnames or [])), None)
    if not columns:
        raise AzureFailure("UNSUPPORTED_CSV_SCHEMA")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(schemas[0])
    for line, row in enumerate(reader, 2):
        try:
            values = [row[c] for c in columns]
            raw = values[1].strip()
            try:
                d = date.fromisoformat(raw[:10])
            except ValueError:
                d = datetime.strptime(raw, "%m/%d/%Y").date()
            values[0] = guid(values[0])
            values[1] = d.isoformat()
            values[4] = str(dec(values[4], max_scale=60))
            writer.writerow(values)
        except (ValueError, KeyError, TypeError, HTTPException):
            raise AzureFailure("CSV_INVALID_ROW_" + str(line)) from None
    return out.getvalue().encode()


def publish(
    db, sub, rows, source, start, end, verified=False, complete=False, evidence=""
):
    """Publish one complete declared scope transactionally, retaining immutable raw versions."""
    from backend.selective_purge import ranges, blocked

    exclusions = ranges(db, sub.id)
    rows = [r for r in rows if not blocked(exclusions, r["usage_date"])]
    if not rows:
        return None  # absence is never proof of zero
    db.scalar(select(Subscription).where(Subscription.id == sub.id).with_for_update())
    if db.scalar(select(Tombstone).where(Tombstone.object_id == sub.id)):
        raise AzureFailure("PURGED_SCOPE")
    dayset = {r["usage_date"] for r in rows}
    canonical = json.dumps(rows, sort_keys=True)
    digest = hashlib.sha256(
        (source + sub.id + canonical + str(verified) + str(complete)).encode()
    ).hexdigest()
    old = db.scalar(select(Batch).where(Batch.digest == digest))
    if old:
        return old
    # Unknown/partial snapshots may not destroy a validated complete published partition.
    oldcov = list(
        db.scalars(
            select(Coverage).where(
                Coverage.subscription_id == sub.id, Coverage.day.in_(dayset)
            )
        )
    )
    if not complete and any(db.get(Batch, c.batch_id).complete for c in oldcov):
        raise AzureFailure("INCOMPLETE_REPLACEMENT")
    b = Batch(
        digest=digest,
        filename=source,
        created=now(),
        start=start,
        end=end,
        subscription_id=sub.id,
        verified=verified,
        complete=complete,
        evidence=evidence,
        reason="Automatic validated source version",
        rows=canonical,
        row_count=len(rows),
    )
    db.add(b)
    db.flush()
    db.execute(delete(Fact).where(Fact.subscription_id == sub.id, Fact.day.in_(dayset)))
    db.execute(
        delete(Coverage).where(
            Coverage.subscription_id == sub.id, Coverage.day.in_(dayset)
        )
    )
    grouped = defaultdict(Decimal)
    for r in rows:
        if r["subscription_id"] != sub.id or not start <= r["usage_date"] <= end:
            raise AzureFailure("INVALID_SOURCE_SCOPE")
        grouped[(r["usage_date"], r["service"], r["currency"])] += dec(
            r["amount"], max_scale=60
        )
    for day in dayset:
        db.add(Coverage(subscription_id=sub.id, day=day, batch_id=b.id))
    for (day, service, currency), amount in grouped.items():
        db.add(
            Fact(
                subscription_id=sub.id,
                day=day,
                service=service,
                currency=currency,
                amount=str(amount),
                batch_id=b.id,
            )
        )
    return b


class JobBody(BaseModel):
    scope_type: str
    scope_id: str
    start: date
    end: date
    kind: str = Field(default="sync", pattern="^(sync|verify|backfill|daily)$")


class UserBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="member", pattern="^(admin|member)$")
    password: str = Field(min_length=12, max_length=200)


class UserUpdate(BaseModel):
    active: bool | None = None
    role: str | None = Field(default=None, pattern="^(admin|member)$")
    password: str | None = Field(default=None, min_length=12, max_length=200)


class ScheduleBody(BaseModel):
    utc_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True
    refresh_hours: int = Field(default=6, ge=1, le=6)


class BasisBody(BaseModel):
    verified: bool
    evidence: str = Field(min_length=5, max_length=2000)


class RuleBody(BaseModel):
    customer_id: str = ""
    percent: str = "50"
    amount: str = "100"
    zero_days: int = Field(default=7, ge=1, le=90)


class ActionBody(BaseModel):
    action: str = Field(pattern="^(read|ack|mute|close|reopen)$")
    mute_hours: int = Field(default=24, ge=1, le=8760)


def install(app):
    @app.post("/api/v1/customers/onboarding", status_code=201)
    def onboard(body: dict, db=Depends(db_session), user=Depends(current_user)):
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 150:
            fail("INVALID_NAME", "客户名称不正确")
        factor = dec(body.get("factor", "1"), Decimal(0), Decimal(1))
        try:
            effective = date.fromisoformat(body["effective_date"])
        except (KeyError, ValueError):
            fail("INVALID_DATE", "折扣生效日不正确")
        specs = body.get("tenants", [])
        if not isinstance(specs, list) or not 1 <= len(specs) <= 3:
            fail("TENANT_COUNT", "新建客户需要 1–3 组租户")
        if user.role != "admin":
            fail("FORBIDDEN", "包含 SP 凭据的新建流程需要管理员", 403)
        customer = Customer(name=name, notes=str(body.get("notes", ""))[:2000])
        db.add(customer)
        db.flush()
        db.add(
            Discount(customer_id=customer.id, day=str(effective), factor=str(factor))
        )
        jobs = []
        for index, spec in enumerate(specs):
            tid = guid(str(spec.get("id", "")))
            cid = guid(str(spec.get("client_id", "")))
            secret = spec.get("client_secret")
            if not isinstance(secret, str) or not 1 <= len(secret) <= 4000:
                fail("CREDENTIAL_REQUIRED", "Tenant ID、Client ID、Secret 必填")
            if db.get(Tenant, tid) or db.scalar(
                select(Tombstone).where(Tombstone.object_id == tid)
            ):
                fail("RESOURCE_OWNERSHIP_CONFLICT", "租户已纳管或已永久删除", 409)
            t = Tenant(
                id=tid,
                customer_id=customer.id,
                name=str(spec.get("name") or name + " Tenant " + str(index + 1))[:150],
                client_id=cid,
                secret=cipher().encrypt(secret.encode()).decode(),
            )
            db.add(t)
            db.flush()
            jobs.append(scalar(enqueue(db, user, "verify", "tenant", t.id)))
        audit(db, user, "ONBOARD_CUSTOMER", customer.id)
        return {"customer": scalar(customer), "jobs": jobs}

    @app.get("/api/v1/sync/jobs")
    def jobs(db=Depends(db_session), user=Depends(current_user)):
        return [
            {**scalar(j), "checkpoint": json.loads(j.checkpoint)}
            for j in db.scalars(select(Job).order_by(Job.created.desc()).limit(200))
        ]

    @app.post("/api/v1/sync/jobs", status_code=202)
    def create_job(body: JobBody, db=Depends(db_session), user=Depends(current_user)):
        return scalar(
            enqueue(
                db,
                user,
                body.kind,
                body.scope_type,
                body.scope_id,
                str(body.start),
                str(body.end),
                100 if body.kind == "backfill" else 0 if body.kind == "daily" else 10,
            )
        )

    @app.post("/api/v1/tenants/{id}/verify", status_code=202)
    def verify_tenant(id: str, db=Depends(db_session), user=Depends(current_user)):
        return scalar(enqueue(db, user, "verify", "tenant", id))

    @app.post("/api/v1/subscriptions/{id}/verify", status_code=202)
    def verify_sub(id: str, db=Depends(db_session), user=Depends(current_user)):
        return scalar(
            enqueue(
                db,
                user,
                "sync",
                "subscription",
                id,
                start=(date.today() - timedelta(days=7)).isoformat(),
            )
        )

    @app.post("/api/v1/sync/jobs/{id}/{action}")
    def job_action(
        id: str, action: str, db=Depends(db_session), user=Depends(current_user)
    ):
        j = require(db, Job, id)
        if action == "cancel" and j.state in ACTIVE:
            j.cancel = True
        elif action == "retry" and j.state in [
            "FAILED",
            "CANCELLED",
            "PARTIAL_SUCCEEDED",
        ]:
            j.state = "QUEUED"
            j.attempts = 0
            j.cancel = False
            j.error = ""
            j.available = now()
        else:
            fail("INVALID_JOB_ACTION", "当前状态不能执行此操作")
        audit(db, user, "JOB_" + action.upper(), id)
        return scalar(j)

    @app.get("/api/v1/azure/states")
    def states(db=Depends(db_session), user=Depends(current_user)):
        return [scalar(s) for s in db.scalars(select(AzureState))]

    @app.put("/api/v1/tenants/{id}/cost-basis")
    def basis(id: str, body: BasisBody, db=Depends(db_session), user=Depends(admin)):
        require(db, Tenant, id)
        put_setting(db, "basis:" + id, body.model_dump())
        audit(db, user, "SET_PRICE_BASIS", id)
        return {"ok": True}

    @app.get("/api/v1/tenants/{id}/cost-basis")
    def get_basis(id: str, db=Depends(db_session), user=Depends(current_user)):
        require(db, Tenant, id)
        return setting(db, "basis:" + id, {"verified": False, "evidence": ""})

    @app.get("/api/v1/settings/schedule")
    def get_schedule(db=Depends(db_session), user=Depends(current_user)):
        return setting(db, "schedule", {"enabled": False, "utc_time": "02:00"})

    @app.put("/api/v1/settings/schedule")
    def schedule(body: ScheduleBody, db=Depends(db_session), user=Depends(admin)):
        put_setting(db, "schedule", body.model_dump())
        audit(db, user, "SET_SCHEDULE", "global")
        return body

    @app.get("/api/v1/users")
    def users(db=Depends(db_session), user=Depends(admin)):
        return [
            {**scalar(u), "active": setting(db, "user:" + u.id, True)}
            for u in db.scalars(select(User))
        ]

    @app.post("/api/v1/users")
    def add_user(body: UserBody, db=Depends(db_session), user=Depends(admin)):
        if "@" not in body.email:
            fail("INVALID_EMAIL", "邮箱不正确")
        u = User(
            email=body.email.strip().lower(),
            name=body.name,
            role=body.role,
            password=hash_password(body.password),
        )
        db.add(u)
        db.flush()
        audit(db, user, "CREATE_USER", u.id)
        return scalar(u)

    @app.put("/api/v1/users/{id}")
    def update_user(
        id: str, body: UserUpdate, db=Depends(db_session), user=Depends(admin)
    ):
        u = require(db, User, id)
        if id == user.id and (body.active is False or body.role == "member"):
            fail("SELF_LOCKOUT", "不能停用或降级当前管理员")
        if body.active is not None:
            put_setting(db, "user:" + id, body.active)
        if body.role:
            u.role = body.role
        if body.password:
            u.password = hash_password(body.password)
        db.execute(delete(LoginSession).where(LoginSession.user_id == id))
        audit(db, user, "UPDATE_USER", id)
        return {"ok": True}

    @app.get("/api/v1/alerts")
    def alerts(db=Depends(db_session), user=Depends(current_user)):
        return [
            {
                **scalar(a),
                "evidence": json.loads(a.evidence),
                "deliveries": [
                    scalar(d)
                    for d in db.scalars(
                        select(Delivery).where(Delivery.event_id == a.id)
                    )
                ],
            }
            for a in db.scalars(select(Alert).order_by(Alert.created.desc()).limit(200))
        ]

    @app.post("/api/v1/alerts/{id}/actions")
    def alert_action(
        id: str, body: ActionBody, db=Depends(db_session), user=Depends(current_user)
    ):
        a = require(db, Alert, id)
        if body.action == "read":
            a.read = True
        else:
            a.state = {
                "ack": "ACKNOWLEDGED",
                "mute": "MUTED",
                "close": "CLOSED",
                "reopen": "OPEN",
            }[body.action]
        if body.action == "mute":
            a.muted_until = (
                datetime.now(timezone.utc) + timedelta(hours=body.mute_hours)
            ).isoformat()
        a.updated = now()
        audit(db, user, "ALERT_" + body.action.upper(), id)
        return scalar(a)

    @app.get("/api/v1/settings/alert-rules")
    def rules(db=Depends(db_session), user=Depends(current_user)):
        return [
            dict(key=s.key, **json.loads(s.value))
            for s in db.scalars(select(Setting).where(Setting.key.like("rule:%")))
        ]

    @app.put("/api/v1/settings/alert-rules")
    def set_rule(body: RuleBody, db=Depends(db_session), user=Depends(current_user)):
        dec(body.percent, Decimal(0))
        dec(body.amount, Decimal(0))
        if body.customer_id:
            require(db, Customer, body.customer_id)
        put_setting(db, "rule:" + body.customer_id, body.model_dump())
        audit(db, user, "SET_ALERT_RULE", body.customer_id)
        return body

    @app.get("/api/v1/reports/tasks")
    def exports(db=Depends(db_session), user=Depends(current_user)):
        return [
            scalar(t)
            for t in db.scalars(
                select(ExportTask)
                .where(ExportTask.owner == user.id)
                .order_by(ExportTask.created.desc())
                .limit(100)
            )
        ]

    @app.post("/api/v1/reports/tasks", status_code=202)
    def export_task(body: dict, db=Depends(db_session), user=Depends(current_user)):
        try:
            valid_range(
                date.fromisoformat(body["start"]), date.fromisoformat(body["end"])
            )
        except (KeyError, ValueError):
            fail("INVALID_RANGE", "日期不正确")
        if body.get("format") not in ["csv", "xlsx"] or body.get("granularity") not in [
            "day",
            "month",
        ]:
            fail("INVALID_EXPORT", "格式或粒度不正确")
        if body.get("dimension") not in [
            "customer",
            "tenant",
            "subscription",
            "service",
        ]:
            fail("INVALID_EXPORT", "维度不正确")
        task = ExportTask(owner=user.id, query=json.dumps(body))
        db.add(task)
        db.flush()
        return scalar(task)

    @app.get("/api/v1/reports/tasks/{id}/download")
    def download(id: str, db=Depends(db_session), user=Depends(current_user)):
        from fastapi.responses import FileResponse

        t = require(db, ExportTask, id)
        if t.owner != user.id:
            fail("FORBIDDEN", "文件不属于当前账号", 403)
        if t.state != "SUCCEEDED":
            fail("EXPORT_NOT_READY", "报表尚未完成")
        fmt = json.loads(t.query)["format"]
        p = DATA / "exports" / (t.id + "." + fmt)
        if not p.is_file():
            fail("FILE_MISSING", "文件不存在，请重新导出", 404)
        return FileResponse(p, filename="acr-report." + fmt)

    @app.get("/api/v1/settings/worker")
    def worker_health(db=Depends(db_session), user=Depends(current_user)):
        heartbeat = setting(db, "worker_heartbeat", "")
        return {
            "heartbeat": heartbeat,
            "ready": bool(
                heartbeat
                and heartbeat
                > (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            ),
        }

    @app.put("/api/v1/tenants/{id}/blob")
    def blob_config(id: str, body: dict, db=Depends(db_session), user=Depends(admin)):
        require(db, Tenant, id)
        if not re.fullmatch(
            r"[a-z0-9]{3,24}", body.get("account", "")
        ) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", body.get("container", "")
        ):
            fail("INVALID_STORAGE", "Storage Account 或 Container 格式不正确")
        prefix = body.get("prefix", "")
        if ".." in prefix or len(prefix) > 500:
            fail("INVALID_PREFIX", "路径前缀不正确")
        put_setting(
            db,
            "blob:" + id,
            {k: body.get(k, "") for k in ["account", "container", "prefix"]},
        )
        audit(db, user, "SET_BLOB", id)
        return {"ok": True}

    @app.get("/api/v1/tenants/{id}/blob")
    def get_blob(id: str, db=Depends(db_session), user=Depends(admin)):
        require(db, Tenant, id)
        return setting(db, "blob:" + id, {})

    @app.delete("/api/v1/tenants/{id}/blob")
    def remove_blob(id: str, db=Depends(db_session), user=Depends(admin)):
        s = db.get(Setting, "blob:" + id)
        if s:
            db.delete(s)
        audit(db, user, "REMOVE_BLOB", id)
        return {"ok": True}

    @app.get("/api/v1/settings/notifications")
    def notifications(db=Depends(db_session), user=Depends(admin)):
        value = setting(db, "notifications", {})
        for channel in ["email", "teams"]:
            for key in ["password", "url"]:
                if key in value.get(channel, {}):
                    value[channel][key] = ""
                    value[channel][key + "_configured"] = True
        return value

    @app.put("/api/v1/settings/notifications")
    def set_notifications(body: dict, db=Depends(db_session), user=Depends(admin)):
        old = setting(db, "notifications", {})
        new = {}
        for channel in ["email", "teams"]:
            c = body.get(channel, {})
            new[channel] = {"enabled": bool(c.get("enabled", False))}
            if channel == "email":
                for k in ["host", "port", "username", "from", "to"]:
                    new[channel][k] = c.get(k, old.get(channel, {}).get(k))
                if new[channel]["enabled"]:
                    if (
                        not isinstance(c.get("to"), list)
                        or not c["to"]
                        or any(
                            not isinstance(x, str)
                            or "@" not in x
                            or "\n" in x
                            or "\r" in x
                            for x in c["to"]
                        )
                    ):
                        fail("INVALID_EMAIL", "收件人需要有效邮箱列表")
                    if (
                        not isinstance(c.get("from"), str)
                        or "@" not in c["from"]
                        or any(x in c["from"] for x in ["\n", "\r"])
                    ):
                        fail("INVALID_EMAIL", "发件人不正确")
                    if not isinstance(c.get("host"), str) or not re.fullmatch(
                        r"[A-Za-z0-9.-]+", c["host"]
                    ):
                        fail("INVALID_SMTP", "SMTP 主机不正确")
                    if int(c.get("port", 587)) not in [25, 587, 2525]:
                        fail("INVALID_SMTP", "仅支持 STARTTLS SMTP 端口 25/587/2525")
            else:
                raw = c.get("url", "")
                if raw:
                    p = urlparse(raw)
                    host = p.hostname or ""
                    if p.scheme != "https" or not any(
                        host.endswith(suffix)
                        for suffix in [
                            ".webhook.office.com",
                            ".logic.azure.com",
                            ".environment.api.powerplatform.com",
                        ]
                    ):
                        fail(
                            "INVALID_TEAMS_URL", "需要受支持的 Teams HTTPS Webhook URL"
                        )
            key = "password" if channel == "email" else "url"
            raw = c.get(key)
            if raw:
                new[channel][key] = cipher().encrypt(raw.encode()).decode()
            elif old.get(channel, {}).get(key):
                new[channel][key] = old[channel][key]
        put_setting(db, "notifications", new)
        audit(db, user, "SET_NOTIFICATIONS", "global")
        return {"ok": True}

    @app.post("/api/v1/notifications/{id}/retry")
    def delivery_retry(id: str, db=Depends(db_session), user=Depends(current_user)):
        d = require(db, Delivery, id)
        d.state = "QUEUED"
        d.attempts = 0
        d.available = now()
        audit(db, user, "RETRY_DELIVERY", id)
        return {"ok": True}

    @app.get("/api/v1/settings/sso")
    def sso_settings(db=Depends(db_session), user=Depends(admin)):
        c = setting(db, "sso", {})
        c["secret_configured"] = bool(c.pop("secret", None))
        return c

    @app.put("/api/v1/settings/sso")
    def configure_sso(body: dict, db=Depends(db_session), user=Depends(admin)):
        for k in ["tenant_id", "client_id"]:
            guid(body.get(k, ""))
        origin = __import__("os").getenv("PUBLIC_ORIGIN", "")
        if not origin.startswith("https://"):
            fail("HTTPS_REQUIRED", "SSO 需要 HTTPS PUBLIC_ORIGIN")
        old = setting(db, "sso", {})
        secret = body.get("client_secret")
        secret = (
            cipher().encrypt(secret.encode()).decode()
            if secret
            else old.get("secret", "")
        )
        if not secret:
            fail("SECRET_REQUIRED", "需要 SSO 应用 Client Secret")
        put_setting(
            db,
            "sso",
            {
                "enabled": bool(body.get("enabled")),
                "tenant_id": body["tenant_id"],
                "client_id": body["client_id"],
                "secret": secret,
            },
        )
        audit(db, user, "CONFIGURE_SSO", "global")
        return {"ok": True}

    @app.get("/api/v1/auth/providers")
    def providers(db=Depends(db_session)):
        return {"password": True, "sso": bool(setting(db, "sso", {}).get("enabled"))}

    @app.get("/api/v1/auth/sso/login")
    def sso_login(response: Response, db=Depends(db_session)):
        import msal, os

        c = setting(db, "sso", {})
        if not c.get("enabled"):
            fail("SSO_DISABLED", "尚未启用 SSO")
        client = msal.ConfidentialClientApplication(
            c["client_id"],
            authority="https://login.microsoftonline.com/" + c["tenant_id"],
            client_credential=cipher().decrypt(c["secret"].encode()).decode(),
        )
        flow = client.initiate_auth_code_flow(
            scopes=["User.Read"],
            redirect_uri=os.environ["PUBLIC_ORIGIN"] + "/api/v1/auth/sso/callback",
        )
        nonce = secrets.token_urlsafe(32)
        put_setting(
            db,
            "sso_flow:" + hashlib.sha256(nonce.encode()).hexdigest(),
            {
                "flow": flow,
                "expires": (
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat(),
            },
        )
        r = RedirectResponse(flow["auth_uri"])
        r.set_cookie(
            "sso_flow", nonce, secure=True, httponly=True, samesite="lax", max_age=600
        )
        return r

    @app.get("/api/v1/auth/sso/callback")
    def callback(request: Request, db=Depends(db_session)):
        import msal

        key = (
            "sso_flow:"
            + hashlib.sha256(request.cookies.get("sso_flow", "").encode()).hexdigest()
        )
        stored = db.get(Setting, key)
        if not stored:
            fail("SSO_STATE_INVALID", "SSO 会话无效，请重新登录")
        record = json.loads(stored.value)
        db.delete(stored)
        if record["expires"] < now():
            fail("SSO_EXPIRED", "SSO 会话已过期")
        c = setting(db, "sso", {})
        client = msal.ConfidentialClientApplication(
            c["client_id"],
            authority="https://login.microsoftonline.com/" + c["tenant_id"],
            client_credential=cipher().decrypt(c["secret"].encode()).decode(),
        )
        try:
            result = client.acquire_token_by_auth_code_flow(
                record["flow"], dict(request.query_params)
            )
        except ValueError:
            fail("SSO_STATE_INVALID", "SSO 校验失败")
        claims = result.get("id_token_claims", {})
        if not claims or claims.get("tid") != c["tenant_id"]:
            fail("SSO_FAILED", "Microsoft 登录失败")
        email = (claims.get("preferred_username") or claims.get("email") or "").lower()
        user = db.scalar(select(User).where(User.email == email))
        if not user or not setting(db, "user:" + user.id, True):
            fail("UNAUTHORIZED_SSO_USER", "账号未经管理员授权", 403)
        subject = claims.get("oid")
        bound = setting(db, "sso_identity:" + user.id)
        if not subject or (bound and bound != {"tid": claims["tid"], "oid": subject}):
            fail("SSO_IDENTITY_CONFLICT", "外部身份与绑定账号不一致", 403)
        put_setting(
            db, "sso_identity:" + user.id, {"tid": claims["tid"], "oid": subject}
        )
        token = secrets.token_urlsafe(48)
        db.add(
            LoginSession(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user.id,
                expires=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
            )
        )
        audit(db, user, "SSO_LOGIN", user.id)
        r = RedirectResponse("/")
        r.delete_cookie("sso_flow")
        r.set_cookie(
            "acr_session",
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=28800,
        )
        return r

    @app.post("/api/v1/subscriptions/{id}/transfer")
    def transfer(
        id: str, body: dict, db=Depends(db_session), user=Depends(current_user)
    ):
        sub = db.scalar(
            select(Subscription).where(Subscription.id == id).with_for_update()
        ) or require(db, Subscription, id)
        target = require(db, Tenant, body.get("tenant_id", ""))
        try:
            day = date.fromisoformat(body["effective_date"])
        except (KeyError, ValueError):
            fail("INVALID_DATE", "需要归属生效 UTC 日期")
        if day > datetime.now(timezone.utc).date():
            fail("FUTURE_TRANSFER", "转移日期不能晚于今天 UTC")
        records = list(
            db.scalars(
                select(Ownership)
                .where(Ownership.subscription_id == id)
                .order_by(Ownership.day)
            )
        )
        if records and str(day) <= records[-1].day:
            fail("OWNERSHIP_OVERLAP", "转移日期必须晚于最后一次转移")
        if not records:
            old = require(db, Tenant, sub.tenant_id)
            db.add(
                Ownership(
                    subscription_id=id,
                    tenant_id=old.id,
                    customer_id=old.customer_id,
                    day="0001-01-01",
                )
            )
        if target.id == sub.tenant_id:
            fail("SAME_OWNER", "订阅已属于该租户")
        db.add(
            Ownership(
                subscription_id=id,
                tenant_id=target.id,
                customer_id=target.customer_id,
                day=str(day),
            )
        )
        from backend.selective_purge import ranges

        if any(r.end == "9999-12-31" for r in ranges(db, id)):
            fail(
                "ARCHIVED_HISTORY_READ_ONLY",
                "该订阅为永久删除后的历史归档，不允许重新纳管或改写归属",
                409,
            )
        sub.tenant_id = target.id
        audit(db, user, "TRANSFER_SUBSCRIPTION", id)
        return {"ok": True}

    @app.get("/api/v1/subscriptions/{id}/ownership")
    def history(id: str, db=Depends(db_session), user=Depends(current_user)):
        require(db, Subscription, id)
        return [
            scalar(o)
            for o in db.scalars(
                select(Ownership)
                .where(Ownership.subscription_id == id)
                .order_by(Ownership.day)
            )
        ]

    @app.get("/api/v1/costs/coverage")
    def coverage(
        start: date,
        end: date,
        customer_id: str = "",
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        valid_range(start, end)
        result = calculate(db, str(start), str(end), customer_id)
        result.pop("report")
        return result

    @app.get("/api/v1/catalog/{kind}")
    def catalog(
        kind: str,
        page: int = 1,
        page_size: int = 25,
        search: str = "",
        customer_id: str = "",
        tenant_id: str = "",
        db=Depends(db_session),
        user=Depends(current_user),
    ):
        from sqlalchemy import func, or_

        model = {
            "customers": Customer,
            "tenants": Tenant,
            "subscriptions": Subscription,
        }.get(kind)
        if not model:
            fail("NOT_FOUND", "资源不存在", 404)
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        q = select(model)
        if search:
            q = q.where(
                or_(
                    model.name.ilike("%" + search[:150] + "%"),
                    model.id.ilike("%" + search[:150] + "%"),
                )
            )
        if kind == "tenants" and customer_id:
            q = q.where(Tenant.customer_id == customer_id)
        if kind == "subscriptions":
            if tenant_id:
                q = q.where(Subscription.tenant_id == tenant_id)
            if customer_id:
                q = q.join(Tenant).where(Tenant.customer_id == customer_id)
        total = db.scalar(select(func.count()).select_from(q.subquery()))
        items = [
            scalar(x)
            for x in db.scalars(
                q.order_by(model.name, model.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ]
        if kind == "subscriptions":
            from backend.subscription_tiers import snapshots, view

            tiers = snapshots(db, [x["id"] for x in items])
            for x in items:
                x["tier"] = tiers.get(x["id"], view(None))
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @app.post("/api/v1/{kind}/{id}/purge-preview")
    def preview(kind: str, id: str, db=Depends(db_session), user=Depends(admin)):
        model = {
            "customers": Customer,
            "tenants": Tenant,
            "subscriptions": Subscription,
        }.get(kind)
        if not model:
            fail("INVALID_SCOPE", "无效对象")
        require(db, model, id)
        tids, sids = purge_scope(db, kind, id)
        count = len(
            list(db.scalars(select(Fact.id).where(Fact.subscription_id.in_(sids))))
        )
        from backend.selective_purge import preview_history_count

        count += preview_history_count(db, kind, id, sids)
        from backend.resource_costs import ResourceFact, ResourceBatch

        resource_records = db.scalar(
            select(__import__("sqlalchemy").func.count())
            .select_from(ResourceFact)
            .where(ResourceFact.subscription_id.in_(sids))
        ) + preview_history_count(db, kind, id, sids, ResourceFact)
        resource_batches = db.scalar(
            select(__import__("sqlalchemy").func.count())
            .select_from(ResourceBatch)
            .where(ResourceBatch.subscription_id.in_(sids))
        ) + preview_history_count(db, kind, id, sids, ResourceBatch)
        token = secrets.token_urlsafe(32)
        put_setting(
            db,
            "purge_challenge:" + hashlib.sha256(token.encode()).hexdigest(),
            {
                "actor": user.id,
                "kind": kind,
                "id": id,
                "expires": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        )
        return {
            "challenge": token,
            "tenants": len(tids),
            "subscriptions": len(sids),
            "facts": count,
            "all_exports_invalidated": True,
            "resource_cost_records": resource_records,
            "resource_cost_batches": resource_batches,
        }

    @app.post("/api/v1/{kind}/{id}/purge")
    def purge(
        kind: str, id: str, body: dict, db=Depends(db_session), user=Depends(admin)
    ):
        if __import__("os").getenv("PURGE_ENABLED") != "true":
            fail("PURGE_NOT_APPROVED", "永久删除未启用；须先完成备份恢复演练", 403)
        key = (
            "purge_challenge:"
            + hashlib.sha256(str(body.get("challenge", "")).encode()).hexdigest()
        )
        record = setting(db, key)
        if (
            not record
            or record["actor"] != user.id
            or record["kind"] != kind
            or record["id"] != id
            or record["expires"] < now()
            or body.get("confirm") != id
        ):
            fail("INVALID_CHALLENGE", "删除确认无效或已过期")
        # Ledger is outside PostgreSQL and fsynced BEFORE deletion. Old DB restores
        # are replayed against it before serving traffic. Unknown/missing ledger blocks restore.
        tids, sids = purge_scope(db, kind, id)
        append_tombstone(kind, id)
        for tid in tids:
            append_tombstone("tenants", tid)
        for sid in sids:
            append_tombstone("subscriptions", sid)
        purge_data(db, kind, id)
        db.delete(db.get(Setting, key))
        audit(db, user, "PERMANENT_DELETE", id)
        return {"deleted": True, "id": id}


def ownership_for(db, sub, day):
    record = db.scalar(
        select(Ownership)
        .where(Ownership.subscription_id == sub.id, Ownership.day <= day)
        .order_by(Ownership.day.desc())
        .limit(1)
    )
    if record:
        return record.customer_id, record.tenant_id
    t = db.get(Tenant, sub.tenant_id)
    return t.customer_id, t.id


def purge_scope(db, kind, id):
    tids = (
        [id]
        if kind == "tenants"
        else list(db.scalars(select(Tenant.id).where(Tenant.customer_id == id)))
        if kind == "customers"
        else []
    )
    sids = (
        [id]
        if kind == "subscriptions"
        else list(
            db.scalars(select(Subscription.id).where(Subscription.tenant_id.in_(tids)))
        )
    )
    # Historical owners are part of a customer's scope as well: prevent retained raw PII.
    if kind in ["customers", "tenants"] and sids:
        all_history = list(
            db.scalars(select(Ownership).where(Ownership.subscription_id.in_(sids)))
        )
        shared = {
            h.subscription_id
            for h in all_history
            if (h.customer_id != id if kind == "customers" else h.tenant_id != id)
        }
        sids = [sid for sid in sids if sid not in shared]
    return tids, sids


def append_tombstone(kind, id):
    import os, fcntl

    path = DATA / "deletion-ledger.jsonl"
    with path.open("a") as f:
        os.chmod(path, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps({"kind": kind, "id": id, "created": now()}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def purge_data(db, kind, id):
    db.execute(delete(DashboardCache))
    tids, sids = purge_scope(db, kind, id)
    affected_alerts = (
        {id}
        if kind == "customers"
        else (
            {
                t.customer_id
                for t in db.scalars(select(Tenant).where(Tenant.id.in_(tids)))
            }
            | {
                h.customer_id
                for h in db.scalars(
                    select(Ownership).where(Ownership.subscription_id.in_(sids))
                )
            }
            | set(
                db.scalars(
                    select(Tenant.customer_id)
                    .join(Subscription, Subscription.tenant_id == Tenant.id)
                    .where(Subscription.id.in_(sids))
                )
            )
        )
    )
    from backend.selective_purge import erase_transferred, PurgedRange
    from backend import resource_costs

    erase_transferred(db, kind, id, sids)
    resource_costs.purge(db, sids)
    db.execute(delete(PurgedRange).where(PurgedRange.subscription_id.in_(sids)))
    db.execute(delete(Coverage).where(Coverage.subscription_id.in_(sids)))
    db.execute(delete(Fact).where(Fact.subscription_id.in_(sids)))
    db.execute(delete(Batch).where(Batch.subscription_id.in_(sids)))
    db.execute(delete(Ownership).where(Ownership.subscription_id.in_(sids)))
    ids = sids + tids + [id]
    db.execute(delete(Job).where(Job.scope_id.in_(ids)))
    for value in ids:
        s = db.get(AzureState, value)
        if s:
            db.delete(s)
    customer_ids = list(affected_alerts)
    alerts = list(db.scalars(select(Alert).where(Alert.customer_id.in_(customer_ids))))
    for a in alerts:
        delivery_ids = list(
            db.scalars(select(Delivery.id).where(Delivery.event_id == a.id))
        )
        db.execute(
            delete(Setting).where(
                Setting.key.in_(["delivery_payload:" + x for x in delivery_ids])
            )
        )
        db.execute(delete(Delivery).where(Delivery.event_id == a.id))
        db.delete(a)
    for key in ["basis:", "blob:", "rule:", "history_initialized:"]:
        db.execute(delete(Setting).where(Setting.key.in_([key + x for x in ids])))
    # Export files can contain mixed-customer data, so invalidate all to avoid leakage.
    db.execute(delete(ExportTask))
    for p in (DATA / "exports").glob("*") if (DATA / "exports").exists() else []:
        p.unlink()
    db.execute(delete(Subscription).where(Subscription.id.in_(sids)))
    if tids:
        db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
    if kind == "customers":
        db.execute(delete(Discount).where(Discount.customer_id == id))
        db.execute(delete(Customer).where(Customer.id == id))
    for child_kind, child_ids in [("tenants", tids), ("subscriptions", sids)]:
        for child_id in child_ids:
            if not db.scalar(
                select(Tombstone).where(
                    Tombstone.kind == child_kind, Tombstone.object_id == child_id
                )
            ):
                db.add(Tombstone(kind=child_kind, object_id=child_id))
    if not db.scalar(
        select(Tombstone).where(Tombstone.kind == kind, Tombstone.object_id == id)
    ):
        db.add(Tombstone(kind=kind, object_id=id))


def replay_tombstones():
    p = DATA / "deletion-ledger.jsonl"
    if not p.exists():
        return
    with SessionLocal.begin() as db:
        for line in p.read_text().splitlines():
            row = json.loads(line)
            if not db.scalar(
                select(Tombstone).where(
                    Tombstone.kind == row["kind"], Tombstone.object_id == row["id"]
                )
            ):
                purge_data(db, row["kind"], row["id"])


class DashboardCache(Base):
    __tablename__ = "dashboard_cache"
    key: Mapped[str] = mapped_column(primary_key=True)
    revision: Mapped[str]
    payload: Mapped[str] = mapped_column(Text)


Base.metadata.create_all(engine)

# Invalidate materialized query responses in the same transaction as finance writes.
from sqlalchemy import event as sa_event, text as sql_text
from sqlalchemy.orm import Session as OrmSession


@sa_event.listens_for(OrmSession, "before_flush")
def financial_revision(session, context, instances):
    types = (
        Fact,
        Batch,
        Coverage,
        Discount,
        Fx,
        Ownership,
        Customer,
        Tenant,
        Subscription,
    )
    if any(
        isinstance(obj, types)
        for obj in list(session.new) + list(session.dirty) + list(session.deleted)
    ):
        session.connection().execute(
            sql_text(
                "INSERT INTO settings (key,value) VALUES ('data_revision','1') ON CONFLICT (key) DO UPDATE SET value=CAST(CAST(settings.value AS INTEGER)+1 AS TEXT)"
            )
        )


import threading

_dashboard_lock = threading.Lock()


def dashboard_cached(db, query, compute):
    key = hashlib.sha256(
        (
            "provisional-acr-v1:"
            + datetime.now(timezone.utc).date().isoformat()
            + ":"
            + json.dumps(query, sort_keys=True)
        ).encode()
    ).hexdigest()
    # One API process is used in Compose. Serialize cold cache generation to avoid
    # 20 simultaneous dashboard scans competing for the same CPU and DB snapshot.
    revision = str(setting(db, "data_revision", 0))
    cached = db.get(DashboardCache, key)
    if cached and cached.revision == revision:
        return json.loads(cached.payload)
    with _dashboard_lock:
        db.expire_all()
        revision = str(setting(db, "data_revision", 0))
        cached = db.get(DashboardCache, key)
        if cached and cached.revision == revision:
            return json.loads(cached.payload)
        result = compute()
        result.pop("report", None)
        after = str(setting(db, "data_revision", 0))
        if after != revision:
            db.expire_all()
            result = compute()
            result.pop("report", None)
            revision = after
        if cached:
            cached.revision = revision
            cached.payload = json.dumps(result)
        else:
            db.add(
                DashboardCache(key=key, revision=revision, payload=json.dumps(result))
            )
        db.flush()
        db.commit()
        return result


def dashboard_view(db, start, end, cid="", tid="", sid="", granularity="day"):
    result = calculate(db, start, end, cid, tid, sid, include_report=False)
    from backend.source_costs import summary

    result["raw"] = summary(db, start, end, cid, tid, sid, granularity)
    result["estimate"] = result["raw"].pop("estimate")
    if granularity == "month":
        grouped = {}
        for p in result["trend"]:
            key = p["day"][:7]
            v = grouped.setdefault(key, {"amount": Decimal(0), "known": False})
            if p["value"] is not None:
                v["amount"] += Decimal(p["value"])
                v["known"] = True
        result["trend"] = [
            {"day": k, "value": str(v["amount"]) if v["known"] else None}
            for k, v in grouped.items()
        ]
    result["comparison"] = None
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    previous_end = a - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    compare_end = previous_start + timedelta(days=(b - a).days)
    if (
        a.day == 1
        and compare_end <= previous_end
        and db.scalar(
            select(Coverage.id)
            .where(
                Coverage.day >= str(previous_start), Coverage.day <= str(compare_end)
            )
            .limit(1)
        )
    ):
        previous = calculate(
            db,
            str(previous_start),
            str(compare_end),
            cid,
            tid,
            sid,
            include_report=False,
        )
        if (
            result["coverage"] == "COMPLETE_DECLARED"
            and previous["coverage"] == "COMPLETE_DECLARED"
            and previous["total"] is not None
            and Decimal(previous["total"]) != 0
            and result["total"] is not None
        ):
            result["comparison"] = str(
                (Decimal(result["total"]) - Decimal(previous["total"]))
                / abs(Decimal(previous["total"]))
                * 100
            )
    return result


def sql_financial(db, start, end, cid="", tid="", sid=""):
    """PostgreSQL exact NUMERIC aggregation; one materialized snapshot for all KPIs.

    GROUPING SETS avoids transferring and hydrating every historical fact in Python.
    LATERAL effective-date lookups use the unique history indexes, preserving
    discount, FX and ownership semantics. No double/binary float arithmetic.
    """
    query = sql_text("""
WITH amounts AS MATERIALIZED (
 SELECT f.day, f.subscription_id, f.service,
        COALESCE(o.customer_id,t.customer_id) AS customer_id,
        CASE WHEN b.verified AND d.factor IS NOT NULL
                   AND (f.currency='USD' OR x.rate IS NOT NULL)
          THEN CAST(f.amount AS NUMERIC)*CAST(d.factor AS NUMERIC)*
               CASE WHEN f.currency='USD' THEN 1::NUMERIC ELSE CAST(x.rate AS NUMERIC) END
          ELSE NULL END AS value
 FROM facts f JOIN subscriptions s ON s.id=f.subscription_id
 JOIN tenants t ON t.id=s.tenant_id JOIN batches b ON b.id=f.batch_id
 LEFT JOIN LATERAL (SELECT h.customer_id,h.tenant_id FROM ownership_history h
     WHERE h.subscription_id=f.subscription_id AND h.day<=f.day ORDER BY h.day DESC LIMIT 1) o ON TRUE
 LEFT JOIN LATERAL (SELECT factor FROM discounts d
     WHERE d.customer_id=COALESCE(o.customer_id,t.customer_id) AND d.day<=f.day ORDER BY d.day DESC LIMIT 1) d ON TRUE
 LEFT JOIN LATERAL (SELECT rate FROM fx x WHERE x.currency=f.currency AND x.day<=f.day ORDER BY x.day DESC LIMIT 1) x ON TRUE
 WHERE (:cid='' OR COALESCE(o.customer_id,t.customer_id)=:cid)
   AND (:tid='' OR COALESCE(o.tenant_id,s.tenant_id)=:tid)
   AND (:sid='' OR f.subscription_id=:sid)
   AND NOT EXISTS (SELECT 1 FROM purged_ownership_ranges p WHERE p.subscription_id=f.subscription_id AND p.start<=f.day AND f.day<p."end")
), selected AS (SELECT * FROM amounts WHERE day>=:start AND day<=:end)
SELECT 'lifetime' AS bucket,MIN(day) AS day,NULL::TEXT AS customer_id,
       NULL::TEXT AS subscription_id,NULL::TEXT AS service,SUM(value) AS amount,
       COUNT(value) AS known,COUNT(*)-COUNT(value) AS unknown FROM amounts
UNION ALL
SELECT CASE WHEN GROUPING(day)=0 THEN 'day' WHEN GROUPING(customer_id)=0 THEN 'customer'
            WHEN GROUPING(subscription_id)=0 THEN 'subscription' WHEN GROUPING(service)=0 THEN 'service'
            ELSE 'total' END AS bucket,
       day,customer_id,subscription_id,service,SUM(value),COUNT(value),COUNT(*)-COUNT(value)
FROM selected GROUP BY GROUPING SETS ((),(day),(customer_id),(subscription_id),(service))
""")
    rows = list(
        db.execute(
            query, dict(start=start, end=end, cid=cid, tid=tid, sid=sid)
        ).mappings()
    )
    result = {
        "lifetime": Decimal(0),
        "lifetime_rows": 0,
        "first": None,
        "total": Decimal(0),
        "verified_rows": 0,
        "excluded": 0,
        "daily": defaultdict(Decimal),
        "top": defaultdict(Decimal),
        "subs": defaultdict(Decimal),
        "services": defaultdict(Decimal),
    }
    for r in rows:
        if r["bucket"] == "lifetime":
            result.update(
                lifetime=r["amount"] or Decimal(0),
                lifetime_rows=r["known"],
                first=r["day"],
            )
        elif r["bucket"] == "total":
            result.update(
                total=r["amount"] or Decimal(0),
                verified_rows=r["known"],
                excluded=r["unknown"],
            )
        elif r["known"]:
            field, key = {
                "day": ("daily", "day"),
                "customer": ("top", "customer_id"),
                "subscription": ("subs", "subscription_id"),
                "service": ("services", "service"),
            }[r["bucket"]]
            result[field][r[key]] = r["amount"]
    return result
