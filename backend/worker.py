"""Single elected worker; persistent jobs yield between partitions for daily priority."""

import csv
import json
import smtplib
import time
import logging
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage

import requests
from sqlalchemy import select, text
from backend.app import (
    SessionLocal,
    engine,
    URL,
    DATA,
    User,
    Tenant,
    Subscription,
    Customer,
    calculate,
    now,
    cipher,
)
from backend.operations import (
    ACTIVE,
    Job,
    Alert,
    Delivery,
    ExportTask,
    AzureFailure,
    state,
    setting,
    put_setting,
    enqueue,
    tenant_token,
    discover,
    query_cost,
    publish,
    Tombstone,
)


def emit(db, cid, rule, evidence):
    existing = db.scalar(
        select(Alert)
        .where(
            Alert.customer_id == cid,
            Alert.rule == rule,
            Alert.state.in_(["OPEN", "ACKNOWLEDGED", "MUTED", "CLOSED"]),
        )
        .order_by(Alert.created.desc())
    )
    if existing:
        existing.evidence = json.dumps(evidence)
        existing.updated = now()
        return existing
    key = cid + ":" + rule + ":" + evidence.get("day", date.today().isoformat())
    existing = db.scalar(select(Alert).where(Alert.event_key == key))
    if existing:
        return existing
    a = Alert(event_key=key, customer_id=cid, rule=rule, evidence=json.dumps(evidence))
    db.add(a)
    db.flush()
    queue_notifications(db, a)
    return a


def queue_notifications(db, a):
    config = setting(db, "notifications", {})
    for channel in ["email", "teams"]:
        if config.get(channel, {}).get("enabled"):
            delivery = Delivery(event_id=a.id, channel=channel)
            db.add(delivery)
            db.flush()
            put_setting(
                db,
                "delivery_payload:" + delivery.id,
                {
                    "rule": a.rule,
                    "customer_id": a.customer_id,
                    "state": a.state,
                    "evidence": json.loads(a.evidence),
                },
            )


def evaluate_alerts(db, customer_ids=None):
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    query = select(Customer).where(Customer.active == True)
    if customer_ids is not None:
        query = query.where(Customer.id.in_(customer_ids))
    for customer in db.scalars(query):
        rule = setting(
            db,
            "rule:" + customer.id,
            setting(db, "rule:", {"percent": "50", "amount": "100", "zero_days": 7}),
        )
        triggered = set()
        evaluated = set()

        def window(count):
            result = calculate(
                db,
                str(end - timedelta(days=count - 1)),
                str(end),
                customer.id,
                include_report=False,
            )
            values = [
                Decimal(p["value"]) if p["value"] is not None else None
                for p in result["trend"]
            ]
            return (
                values
                if result["coverage"] == "COMPLETE_DECLARED"
                and len(values) >= count
                and all(v is not None for v in values)
                else None
            )

        values = window(8)
        if values:
            evaluated.add("SPIKE")
            baseline = sum(values[-8:-1]) / 7
            current = values[-1]
            delta = abs(current - baseline)
            if (
                baseline != 0
                and delta / abs(baseline) * 100 > Decimal(rule["percent"])
                and delta > Decimal(rule["amount"])
            ):
                triggered.add("SPIKE")
                emit(
                    db,
                    customer.id,
                    "SPIKE",
                    {
                        "day": str(end),
                        "current": str(current),
                        "baseline": str(baseline),
                        "change_percent": str(
                            (current - baseline) / abs(baseline) * 100
                        ),
                    },
                )
        zero_days = int(rule["zero_days"])
        zero = window(zero_days)
        if zero:
            evaluated.add("ZERO")
            if all(v == 0 for v in zero[-zero_days:]):
                triggered.add("ZERO")
                emit(
                    db,
                    customer.id,
                    "ZERO",
                    {"day": str(end), "days": zero_days, "value": "0"},
                )
        for a in db.scalars(
            select(Alert).where(
                Alert.customer_id == customer.id,
                Alert.rule.in_(["SPIKE", "ZERO"]),
                Alert.state.in_(["OPEN", "ACKNOWLEDGED", "MUTED", "CLOSED"]),
            )
        ):
            if a.state == "MUTED" and a.muted_until and a.muted_until <= now():
                a.state = "OPEN"
            if a.rule in evaluated and a.rule not in triggered:
                was_suppressed = a.state in ["MUTED", "CLOSED"]
                a.state = "RESOLVED"
                a.updated = now()
                if not was_suppressed:
                    queue_notifications(db, a)


def evaluate_sync(db, cid):
    tenant_ids = list(db.scalars(select(Tenant.id).where(Tenant.customer_id == cid)))
    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.kind == "daily", Job.scope_id.in_(tenant_ids + [cid]))
            .order_by(Job.created.desc())
        )
    )
    grouped = {}
    for j in jobs:
        day = j.created[:10]
        scopes = grouped.setdefault(day, {})
        if j.scope_id not in scopes:
            scopes[j.scope_id] = j
    last = sorted(grouped, reverse=True)[:2]
    if any(j.state in ACTIVE for d in last for j in grouped[d].values()):
        return
    if len(last) == 2 and all(
        any(j.state == "FAILED" for j in grouped[d].values()) for d in last
    ):
        emit(
            db,
            cid,
            "SYNC",
            {
                "day": last[-1],
                "jobs": [
                    j.id
                    for d in last
                    for j in grouped[d].values()
                    if j.state == "FAILED"
                ],
            },
        )
    elif last and all(j.state == "SUCCEEDED" for j in grouped[last[0]].values()):
        for a in db.scalars(
            select(Alert).where(
                Alert.customer_id == cid,
                Alert.rule == "SYNC",
                Alert.state.in_(["OPEN", "ACKNOWLEDGED", "MUTED", "CLOSED"]),
            )
        ):
            suppressed = a.state in ["MUTED", "CLOSED"]
            a.state = "RESOLVED"
            a.updated = now()
            if not suppressed:
                queue_notifications(db, a)


def recover_sync_if_healthy(db, cid):
    from backend.operations import AzureState

    tenants = list(
        db.scalars(
            select(Tenant).where(Tenant.customer_id == cid, Tenant.active == True)
        )
    )
    if not tenants:
        return
    ids = [t.id for t in tenants]
    subs = list(
        db.scalars(
            select(Subscription).where(
                Subscription.tenant_id.in_(ids), Subscription.active == True
            )
        )
    )
    statuses = {
        s.id: s
        for s in db.scalars(
            select(AzureState).where(AzureState.id.in_(ids + [s.id for s in subs]))
        )
    }
    if not subs or any(
        x not in statuses or statuses[x].sync not in ["SUCCEEDED", "NO_DATA"]
        for x in ids + [s.id for s in subs]
    ):
        return
    for a in db.scalars(
        select(Alert).where(
            Alert.customer_id == cid,
            Alert.rule == "SYNC",
            Alert.state.in_(["OPEN", "ACKNOWLEDGED", "MUTED", "CLOSED"]),
        )
    ):
        muted = a.state in ["MUTED", "CLOSED"]
        a.state = "RESOLVED"
        a.updated = now()
        if not muted:
            queue_notifications(db, a)


def schedule(db):
    config = setting(
        db, "schedule", {"enabled": False, "utc_time": "02:00", "refresh_hours": 6}
    )
    if not config.get("enabled"):
        return
    current = datetime.now(timezone.utc)
    today = current.date().isoformat()
    owner = db.scalar(select(User).where(User.role == "admin").order_by(User.id))
    if not owner:
        return
    end = (current.date() - timedelta(days=1)).isoformat()
    start = (current.date() - timedelta(days=7)).isoformat()
    daily_key = "scheduled:" + today
    daily_due = current.strftime("%H:%M") >= config["utc_time"] and not setting(
        db, daily_key
    )
    interval = max(1, min(6, int(config.get("refresh_hours", 6))))
    refresh_key = (
        "refreshed:" + today + ":" + str(current.hour // interval) + ":" + str(interval)
    )
    refresh_due = not setting(db, refresh_key)
    if not daily_due and not refresh_due:
        return
    for tenant in db.scalars(select(Tenant).where(Tenant.active == True)):
        if not tenant.secret or not db.get(Customer, tenant.customer_id).active:
            continue
        if daily_due:
            enqueue(
                db,
                owner,
                "daily",
                "tenant",
                tenant.id,
                start=start,
                end=end,
                priority=0,
            )
        elif refresh_due:
            active_daily = db.scalar(
                select(Job.id).where(
                    Job.kind == "daily",
                    Job.scope_id == tenant.id,
                    Job.state.in_(ACTIVE),
                    Job.end == end,
                )
            )
            if not active_daily:
                enqueue(
                    db,
                    owner,
                    "refresh",
                    "tenant",
                    tenant.id,
                    start=start,
                    end=end,
                    priority=1,
                )
    if daily_due:
        put_setting(db, daily_key, True)
    if refresh_due:
        put_setting(db, refresh_key, True)


def prepare(job_id):
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        if job.cancel:
            job.state = "CANCELLED"
            return
        if job.kind == "resources":
            if job.scope_type != "subscription":
                raise AzureFailure("INVALID_RESOURCE_SCOPE")
            sub = db.get(Subscription, job.scope_id)
            if not sub or not sub.active:
                raise AzureFailure("SCOPE_INACTIVE")
            partitions = []
            cursor = date.fromisoformat(job.start)
            end = date.fromisoformat(job.end)
            while cursor <= end:
                part_end = min(end, cursor + timedelta(days=6))
                partitions.append(
                    {"sub": sub.id, "start": str(cursor), "end": str(part_end)}
                )
                cursor = part_end + timedelta(days=1)
            partitions.sort(key=lambda p: p["start"], reverse=True)
            job.checkpoint = json.dumps(
                {"partitions": partitions, "done": 0, "published": []}
            )
            job.progress = "0/" + str(len(partitions))
            return
        targets = []
        if job.scope_type == "tenant":
            targets = [db.get(Tenant, job.scope_id)]
        elif job.scope_type == "customer":
            targets = list(
                db.scalars(select(Tenant).where(Tenant.customer_id == job.scope_id))
            )
        else:
            sub = db.get(Subscription, job.scope_id)
            if sub:
                targets = [db.get(Tenant, sub.tenant_id)]
        ids = [
            t.id
            for t in targets
            if t and t.active and db.get(Customer, t.customer_id).active
        ]
    partitions = []
    for tid in ids:
        with SessionLocal() as db:
            tenant = db.get(Tenant, tid)
            try:
                token = tenant_token(tenant)
            except AzureFailure:
                with SessionLocal.begin() as out:
                    state(out, tid).auth = "FAILED"
                    state(out, tid).error = "SP_AUTH_FAILED"
                raise
        with SessionLocal.begin() as db:
            state(db, tid).auth = "SUCCEEDED"
            state(db, tid).error = ""
        found = discover(tenant, token)
        with SessionLocal.begin() as db:
            sstate = state(db, tid)
            sstate.discovery = "SUCCEEDED"
            sstate.last_seen = now()
            visible = {s["id"] for s in found}
            for old in db.scalars(
                select(Subscription).where(Subscription.tenant_id == tid)
            ):
                if old.id not in visible:
                    state(db, old.id).discovery = "NOT_SEEN"
            for info in found:
                if db.scalar(
                    select(Tombstone).where(Tombstone.object_id == info["id"])
                ):
                    continue
                sub = db.get(Subscription, info["id"])
                if sub and sub.tenant_id != tid:
                    raise AzureFailure("RESOURCE_OWNERSHIP_CONFLICT")
                if not sub:
                    sub = Subscription(id=info["id"], tenant_id=tid, name=info["name"])
                    db.add(sub)
                    db.flush()
                state(db, sub.id).discovery = "SUCCEEDED"
                state(db, sub.id).last_seen = now()
            subs = list(
                db.scalars(
                    select(Subscription).where(
                        Subscription.tenant_id == tid, Subscription.active == True
                    )
                )
            )
            if job.scope_type == "subscription":
                subs = [s for s in subs if s.id == job.scope_id]
            for sub in subs:
                if job.kind in ["verify", "daily", "sync", "refresh"]:
                    from backend.subscription_tiers import queue as queue_tier

                    owner = db.get(User, job.actor)
                    if owner:
                        queue_tier(db, owner, sub, force=job.kind in ["verify", "sync"])
                d = date.fromisoformat(job.start)
                end = date.fromisoformat(job.end)
                # Month partitions preserve upstream cumulative revisions; yield after each.
                while d <= end:
                    next_month = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
                    part_end = min(end, next_month - timedelta(days=1))
                    partitions.append(
                        {"sub": sub.id, "start": str(d), "end": str(part_end)}
                    )
                    d = part_end + timedelta(days=1)
    partitions.sort(key=lambda p: p["start"], reverse=True)
    with SessionLocal.begin() as db:
        j = db.get(Job, job_id)
        j.checkpoint = json.dumps(
            {"partitions": partitions, "done": 0, "published": []}
        )
        j.progress = "0/" + str(len(partitions))


def process_partition(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        checkpoint = json.loads(job.checkpoint)
    if job.kind == "tier":
        from backend.subscription_tiers import process as process_tier

        process_tier(job_id)
        return
    if "partitions" not in checkpoint:
        prepare(job_id)
        with SessionLocal() as db:
            checkpoint = json.loads(db.get(Job, job_id).checkpoint)
    index = checkpoint.get("done", 0)
    partitions = checkpoint.get("partitions", [])
    if index >= len(partitions):
        with SessionLocal.begin() as db:
            j = db.get(Job, job_id)
            j.state = "SUCCEEDED"
            j.error = ""
            j.updated = now()
        return
    part = partitions[index]
    with SessionLocal() as db:
        sub = db.get(Subscription, part["sub"])
        if not sub:
            raise AzureFailure("SCOPE_REMOVED")
        tenant = db.get(Tenant, sub.tenant_id)
        if (
            not sub.active
            or not tenant.active
            or not db.get(Customer, tenant.customer_id).active
        ):
            raise AzureFailure("SCOPE_INACTIVE")
        config = setting(db, "basis:" + tenant.id, {"verified": False, "evidence": ""})
        blob = setting(db, "blob:" + tenant.id)
        token = tenant_token(tenant)
        if job.kind == "resources":
            from backend.resource_costs import query_resources, publish_resources

            rows = query_resources(sub.id, token, part["start"], part["end"])
        elif blob:
            from backend.blob_source import read_export

            rows, complete, evidence = read_export(
                tenant, sub, blob, part["start"], part["end"]
            )
        else:
            rows, complete, evidence = None, False, ""
        if rows is None:
            rows = query_cost(sub.id, token, part["start"], part["end"])
            evidence = (
                "Cost Management ActualCost Query; successful paginated response, no zero-day completeness inference. "
                + config.get("evidence", "")
            )
        # Query success proves permission, not cost price basis or absence-as-zero.
    with SessionLocal.begin() as db:
        j = db.get(Job, job_id)
        if j.cancel:
            j.state = "PARTIAL_SUCCEEDED" if index else "CANCELLED"
            return
        sub = db.get(Subscription, part["sub"])
        if j.kind == "resources":
            from backend.resource_costs import (
                publish_resources,
                state as resource_state,
            )

            versions = publish_resources(db, sub, rows, config.get("verified", False))
            checkpoint["done"] = index + 1
            checkpoint["records_success"] = checkpoint.get("records_success", 0) + len(
                rows
            )
            checkpoint["published"].extend({"batch": v, **part} for v in versions)
            j.checkpoint = json.dumps(checkpoint)
            j.progress = f"{index + 1}/{len(partitions)}"
            j.attempts = 0
            j.error = ""
            j.updated = now()
            j.available = now()
            j.state = "SUCCEEDED" if index + 1 == len(partitions) else "QUEUED"
            previous = resource_state(db, sub.id)
            put_setting(
                db,
                "resource_state:" + sub.id,
                {
                    **previous,
                    "state": j.state
                    if j.state != "SUCCEEDED"
                    else "SUCCEEDED"
                    if checkpoint["records_success"]
                    else "NO_DATA",
                    "last_sync": now(),
                    "error": "",
                    "start": j.start,
                    "end": j.end,
                    "last_job_id": j.id,
                },
            )
            return
        b = publish(
            db,
            sub,
            rows,
            "Azure Blob Export" if blob and complete else "Azure Cost Management API",
            part["start"],
            part["end"],
            config.get("verified", False),
            complete,
            evidence,
        )
        if j.kind in ["verify", "daily", "sync", "refresh"] and not setting(
            db, "history_initialized:" + sub.id
        ):
            owner = db.get(User, j.actor)
            if owner:
                backfill = enqueue(
                    db, owner, "backfill", "subscription", sub.id, priority=100
                )
                put_setting(db, "history_initialized:" + sub.id, backfill.id)
        if j.kind in ["verify", "daily", "sync", "refresh"]:
            owner = db.get(User, j.actor)
            if owner:
                enqueue(
                    db,
                    owner,
                    "resources",
                    "subscription",
                    sub.id,
                    part["start"],
                    part["end"],
                    priority=20,
                )
        ss = state(db, sub.id)
        ss.cost = "SUCCEEDED"
        ss.sync = "SUCCEEDED" if rows else "NO_DATA"
        ss.last_sync = now()
        ss.error = ""
        ts = state(db, sub.tenant_id)
        ts.cost = "SUCCEEDED"
        ts.sync = ss.sync
        ts.last_sync = now()
        checkpoint["done"] = index + 1
        checkpoint["records_success"] = checkpoint.get("records_success", 0) + (
            b.row_count if b else 0
        )
        if b:
            checkpoint["published"].append({"batch": b.id, **part})
        j.checkpoint = json.dumps(checkpoint)
        j.progress = f"{index + 1}/{len(partitions)}"
        j.attempts = 0
        j.error = ""
        j.state = "QUEUED"
        j.available = now()
        j.updated = now()
        if index + 1 == len(partitions):
            j.state = "SUCCEEDED"
            evaluate_alerts(db, [db.get(Tenant, sub.tenant_id).customer_id])
            if j.kind == "daily":
                evaluate_sync(db, db.get(Tenant, sub.tenant_id).customer_id)
            else:
                recover_sync_if_healthy(db, db.get(Tenant, sub.tenant_id).customer_id)


def run_once():
    with SessionLocal.begin() as db:
        if URL.startswith("sqlite"):
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for muted in db.scalars(
            select(Alert).where(
                Alert.state == "MUTED",
                Alert.muted_until != "",
                Alert.muted_until <= now(),
            )
        ):
            muted.state = "OPEN"
            muted.updated = now()
        schedule(db)
        current = now()
        for stale in db.scalars(
            select(Job).where(Job.state == "RUNNING", Job.lease_until < current)
        ):
            stale.state = "RETRY_WAIT"
            stale.available = current
            stale.error = "WORKER_INTERRUPTED"
        job = db.scalar(
            select(Job)
            .where(Job.state.in_(["QUEUED", "RETRY_WAIT"]), Job.available <= current)
            .order_by(Job.priority, Job.created)
            .with_for_update(skip_locked=True)
        )
        if not job:
            return False
        if job.cancel:
            job.state = (
                "PARTIAL_SUCCEEDED"
                if json.loads(job.checkpoint).get("done")
                else "CANCELLED"
            )
            if job.kind == "tier":
                from backend.subscription_tiers import failure as tier_failure

                tier_failure(db, job, "")
            return True
        job.state = "RUNNING"
        job.lease_until = (
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat()
        job.updated = now()
        id = job.id
    try:
        process_partition(id)
    except Exception as error:
        # Never save HTTP bodies, tokens, SDK exception text, or credential-bearing URLs.
        code = (
            error.code if isinstance(error, AzureFailure) else "INTERNAL_WORKER_ERROR"
        )
        if code == "INTERNAL_WORKER_ERROR":
            # Exception messages/locals can contain SQL params, payloads and credentials.
            # Record only source locations and the exception class, never format_exception.
            frames = [
                {
                    "file": frame.filename.rsplit("/", 1)[-1],
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(error.__traceback__)
                if "/backend/" in frame.filename
            ]
            logging.error(
                "Worker job %s failed: %s %s",
                id,
                type(error).__name__,
                json.dumps(frames),
            )
        with SessionLocal.begin() as db:
            j = db.get(Job, id)
            if not j:
                return True
            j.error = code
            j.updated = now()
            j.attempts += 1
            checkpoint = json.loads(j.checkpoint)
            checkpoint["retry_total"] = checkpoint.get("retry_total", 0) + 1
            j.checkpoint = json.dumps(checkpoint)
            parts = checkpoint.get("partitions", [])
            idx = checkpoint.get("done", 0)
            if j.kind not in ["resources", "tier"] and idx < len(parts):
                sid = parts[idx]["sub"]
                ss = state(db, sid)
                ss.error = code
                ss.cost = "FAILED" if code == "COST_ACCESS_DENIED" else ss.cost
                ss.sync = "FAILED"
            if j.attempts <= 3:
                j.state = "RETRY_WAIT"
                delay = max(
                    30 * 2 ** (j.attempts - 1), getattr(error, "retry_after", 0)
                )
                j.available = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
            else:
                j.state = "FAILED"
                cp = json.loads(j.checkpoint)
                cp["failed_partition"] = cp.get("done", 0)
                j.checkpoint = json.dumps(cp)
                scope = db.get(Tenant, j.scope_id) if j.scope_type == "tenant" else None
                if scope and j.kind not in ["resources", "tier"]:
                    s = state(db, scope.id)
                    s.error = code
                    s.sync = "FAILED"
                    if j.kind == "daily":
                        evaluate_sync(db, scope.customer_id)
            if j.kind == "tier":
                from backend.subscription_tiers import failure as tier_failure

                tier_failure(db, j, code)
            if j.kind == "resources":
                from backend.resource_costs import state as resource_state

                previous = resource_state(db, j.scope_id)
                put_setting(
                    db,
                    "resource_state:" + j.scope_id,
                    {
                        **previous,
                        "state": j.state,
                        "error": code,
                        "available": j.available,
                        "last_job_id": j.id,
                    },
                )
    return True


def build_export(task_id):
    from backend.report_query import rows
    from openpyxl import Workbook

    root = DATA / "exports"
    root.mkdir(exist_ok=True)
    temporary = root / (task_id + ".tmp")
    with SessionLocal.begin() as db:
        task = db.get(ExportTask, task_id)
        if not task:
            return
        task.state = "RUNNING"
        query = json.loads(task.query)

    def safe(row):
        return [
            "'" + v
            if isinstance(v, str)
            and v.startswith(("=", "+", "-", "@", "\t", "\r"))
            and i < len(row) - 2
            else v
            for i, v in enumerate(row)
        ]

    try:
        with SessionLocal() as db:
            data = iter(rows(db, query))
            headers = next(data)
            if query["format"] == "csv":
                with temporary.open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    for row in data:
                        writer.writerow(safe(row))
            else:
                book = Workbook(write_only=True)
                sheet = book.create_sheet("ACR1")
                sheet.append(headers)
                count = 0
                for row in data:
                    if count and count % 1048575 == 0:
                        sheet = book.create_sheet("ACR" + str(count // 1048575 + 1))
                        sheet.append(headers)
                    sheet.append(safe(row))
                    count += 1
                book.save(temporary)
        with SessionLocal.begin() as db:
            task = db.scalar(
                select(ExportTask).where(ExportTask.id == task_id).with_for_update()
            )
            if not task:
                temporary.unlink(missing_ok=True)
                return
            temporary.replace(root / (task_id + "." + query["format"]))
            task.state = "SUCCEEDED"
            task.error = ""
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def deliver():
    with SessionLocal() as db:
        pending = list(
            db.scalars(
                select(Delivery)
                .where(Delivery.state == "QUEUED", Delivery.available <= now())
                .limit(10)
            )
        )
        config = setting(db, "notifications", {})
    for item in pending:
        try:
            with SessionLocal() as db:
                a = db.get(Alert, item.event_id)
            if not a or a.state in ["MUTED", "CLOSED"]:
                with SessionLocal.begin() as db:
                    db.get(Delivery, item.id).state = "SUPPRESSED"
                continue
            c = config.get(item.channel, {})
            if not c.get("enabled"):
                continue
            with SessionLocal() as db:
                snapshot = setting(
                    db,
                    "delivery_payload:" + item.id,
                    {
                        "rule": a.rule,
                        "customer_id": a.customer_id,
                        "state": a.state,
                        "evidence": json.loads(a.evidence),
                    },
                )
            payload = json.dumps(snapshot, ensure_ascii=False)
            if item.channel == "email":
                message = EmailMessage()
                message["Subject"] = "AzureLens " + a.rule
                message["From"] = c["from"]
                message["To"] = ", ".join(c["to"])
                message.set_content(payload)
                with smtplib.SMTP(
                    c["host"], int(c.get("port", 587)), timeout=20
                ) as smtp:
                    import ssl

                    smtp.starttls(context=ssl.create_default_context())
                    if c.get("username"):
                        smtp.login(
                            c["username"],
                            cipher().decrypt(c["password"].encode()).decode(),
                        )
                    smtp.send_message(message)
            else:
                url = cipher().decrypt(c["url"].encode()).decode()
                r = requests.post(
                    url, json={"text": payload}, timeout=20, allow_redirects=False
                )
                if not 200 <= r.status_code < 300:
                    raise ValueError()
            with SessionLocal.begin() as db:
                d = db.get(Delivery, item.id)
                d.state = "SENT"
                d.attempts += 1
        except Exception:
            with SessionLocal.begin() as db:
                d = db.get(Delivery, item.id)
                d.attempts += 1
                d.error = "NOTIFICATION_DELIVERY_FAILED"
                d.state = "FAILED" if d.attempts >= 4 else "QUEUED"
                d.available = (
                    datetime.now(timezone.utc) + timedelta(seconds=60 * 2**d.attempts)
                ).isoformat()


def recover_inflight(db):
    for report in db.scalars(select(ExportTask).where(ExportTask.state == "RUNNING")):
        report.state = "QUEUED"
        report.error = "WORKER_INTERRUPTED"
    for job in db.scalars(select(Job).where(Job.state == "RUNNING")):
        job.state = "RETRY_WAIT"
        job.available = now()
        job.error = "WORKER_INTERRUPTED"
        job.updated = now()


def main():
    # PostgreSQL advisory lock prevents two schedulers/workers publishing overlapping ranges.
    connection = engine.connect()
    if not URL.startswith("sqlite"):
        if not connection.execute(
            text("SELECT pg_try_advisory_lock(129731001)")
        ).scalar():
            raise SystemExit("Another worker holds the leader lock")
        connection.commit()
    else:
        import fcntl

        lock = (DATA / "worker.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with SessionLocal.begin() as db:
        recover_inflight(db)
    while True:
        if not URL.startswith("sqlite"):
            try:
                connection.execute(text("SELECT 1"))
                connection.commit()
            except Exception:
                raise SystemExit(
                    "Worker leader session lost; restarting safely"
                ) from None
        try:
            worked = run_once()
            with SessionLocal() as db:
                urgent = db.scalar(
                    select(Job.id)
                    .where(
                        Job.state.in_(["QUEUED", "RETRY_WAIT"]),
                        Job.available <= now(),
                        Job.priority <= 10,
                    )
                    .limit(1)
                )
                task = (
                    None
                    if urgent
                    else db.scalar(
                        select(ExportTask)
                        .where(ExportTask.state == "QUEUED")
                        .order_by(ExportTask.created)
                    )
                )
            if task:
                try:
                    build_export(task.id)
                except Exception:
                    with SessionLocal.begin() as db:
                        t = db.get(ExportTask, task.id)
                        if t:
                            t.state = "FAILED"
                            t.error = "EXPORT_FAILED"
            deliver()
            with SessionLocal.begin() as db:
                put_setting(db, "worker_heartbeat", now())
        except Exception:
            # Preserve the process and pending work; operational health exposes stale heartbeat.
            import logging

            logging.error("Worker iteration failed; pending work retained")
        time.sleep(2 if worked else 5)


if __name__ == "__main__":
    main()
