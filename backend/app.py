"""AzureLens first vertical slice. All accounting arithmetic stays in Decimal."""

import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext, DefaultContext
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DefaultContext.prec = 80
getcontext().prec = 80

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("DATA_DIR", ROOT / ".data"))
DATA.mkdir(parents=True, exist_ok=True)
URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA}/acr.db")
engine = create_engine(
    URL,
    **(
        {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if URL.startswith("sqlite")
        else {}
    ),
)
if URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_setup(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")


SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    password: Mapped[str]
    role: Mapped[str] = mapped_column(default="admin")


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires: Mapped[str]


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str]
    notes: Mapped[str] = mapped_column(default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Discount(Base):
    __tablename__ = "discounts"
    __table_args__ = (UniqueConstraint("customer_id", "day"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    day: Mapped[str]
    factor: Mapped[str]


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    name: Mapped[str]
    client_id: Mapped[str] = mapped_column(default="")
    secret: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Fx(Base):
    __tablename__ = "fx"
    __table_args__ = (UniqueConstraint("currency", "day"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    currency: Mapped[str]
    day: Mapped[str]
    rate: Mapped[str]


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    digest: Mapped[str] = mapped_column(unique=True)
    filename: Mapped[str]
    created: Mapped[str]
    start: Mapped[str]
    end: Mapped[str]
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"))
    verified: Mapped[bool]
    complete: Mapped[bool]
    evidence: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    rows: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int]


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    day: Mapped[str] = mapped_column(index=True)
    service: Mapped[str]
    currency: Mapped[str]
    amount: Mapped[str]
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"))


class Coverage(Base):
    __tablename__ = "coverage"
    __table_args__ = (UniqueConstraint("subscription_id", "day"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    day: Mapped[str]
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"))


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid4()))
    created: Mapped[str]
    actor: Mapped[str]
    action: Mapped[str]
    target: Mapped[str]


Base.metadata.create_all(engine)


def now():
    return datetime.now(timezone.utc).isoformat()


def fail(code, message, status=400):
    raise HTTPException(status, {"code": code, "message": message})


def audit(db, user, action, target):
    db.add(Audit(created=now(), actor=user.email, action=action, target=target))


def hash_password(password):
    salt = secrets.token_hex(16)
    return (
        salt
        + ":"
        + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    )


def password_ok(password, hashed):
    salt, expected = hashed.split(":")
    return hmac.compare_digest(
        expected,
        hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex(),
    )


def cipher():
    key = os.getenv("CREDENTIAL_KEY")
    if not key:
        path = DATA / "credential.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        except FileExistsError:
            pass
        key = path.read_bytes()
    return Fernet(key)


def db_session():
    with SessionLocal() as db:
        try:
            if URL.startswith("sqlite"):
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except IntegrityError:
            db.rollback()
            fail("CONFLICT", "记录已存在或数据冲突，请刷新后重试", 409)
        except Exception:
            db.rollback()
            raise


def current_user(request: Request, db: Session = Depends(db_session)):
    token = request.cookies.get("acr_session", "")
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    if not session or session.expires < now():
        fail("UNAUTHORIZED", "请先登录", 401)
    user = db.get(User, session.user_id)
    if not user:
        fail("UNAUTHORIZED", "账号不存在", 401)
    from backend.operations import setting

    if not setting(db, "user:" + user.id, True):
        fail("USER_DISABLED", "账号已停用", 403)
    return user


def admin(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
):
    if user.role != "admin":
        audit(db, user, "DENY_ADMIN", request.method + " " + request.url.path[:500])
        # Persist the denial before the request dependency rolls back on HTTPException.
        # No protected operation has run at this point.
        db.commit()
        fail("FORBIDDEN", "需要管理员权限", 403)
    return user


def require(db, model, id):
    item = db.get(model, id)
    if item is None:
        fail("NOT_FOUND", "记录不存在", 404)
    return item


def dec(value, lower=None, upper=None, *, max_scale=12):
    try:
        result = Decimal(str(value))
        if (
            not result.is_finite()
            or abs(result) > Decimal("1e15")
            or result.as_tuple().exponent < -max_scale
        ):
            raise ValueError()
        if lower is not None and result < lower:
            raise ValueError()
        if upper is not None and result > upper:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        fail("INVALID_AMOUNT", f"金额/系数无效（最多 {max_scale} 位小数）")


def guid(value):
    try:
        return str(UUID(value))
    except ValueError:
        fail("INVALID_ID", "请输入有效的 Azure UUID")


def days(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def scalar(model):
    return {
        c.name: getattr(model, c.name)
        for c in model.__table__.columns
        if c.name not in ("secret", "password", "rows", "digest")
    }


app = FastAPI(title="AzureLens API", version="0.2.0")


@app.middleware("http")
async def security(request, call_next):
    trace = str(uuid4())
    request.state.trace_id = trace
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        allowed = os.getenv("PUBLIC_ORIGIN", str(request.base_url).rstrip("/"))
        if origin and origin != allowed:
            return Response(
                json.dumps(
                    {"detail": {"code": "ORIGIN_REJECTED", "message": "请求来源不匹配"}}
                ),
                403,
                media_type="application/json",
            )
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "REQUEST_FAILED", "message": str(exc.detail)}
    )
    return JSONResponse(
        {"detail": {**detail, "trace_id": getattr(request.state, "trace_id", "")}},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo invalid secret/password field contents in API validation responses.
    return JSONResponse(
        {
            "detail": {
                "code": "INVALID_INPUT",
                "message": "输入格式不正确，请检查必填项、日期和字段长度",
                "trace_id": getattr(request.state, "trace_id", ""),
                "fields": [".".join(str(x) for x in e["loc"]) for e in exc.errors()],
            }
        },
        status_code=422,
    )


class LoginBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


# Bounded per-process limiter; deployment is deliberately one API process in this slice.
login_attempts = {}


@app.get("/api/v1/health")
def health():
    with SessionLocal() as db:
        connected = db.scalar(
            select(operations.AzureState.id)
            .where(operations.AzureState.auth == "SUCCEEDED")
            .limit(1)
        )
    return {"status": "ok", "version": "0.2.0", "azure_connected": bool(connected)}


@app.post("/api/v1/auth/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Session = Depends(db_session),
):
    key = request.client.host
    attempts = login_attempts.get(key, [])
    attempts = [t for t in attempts if t > time.time() - 300]
    if len(attempts) >= 10:
        fail("RATE_LIMIT", "登录尝试过多，请 5 分钟后再试", 429)
    if len(login_attempts) > 10000:
        login_attempts.clear()
    login_attempts[key] = attempts + [time.time()]
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    valid = password_ok(
        body.password, user.password if user else hash_password("dummy-password")
    )
    from backend.operations import setting

    if not user or not valid or not setting(db, "user:" + user.id, True):
        fail("INVALID_LOGIN", "邮箱或密码错误", 401)
    login_attempts.pop(key, None)
    token = secrets.token_urlsafe(48)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        )
    )
    response.set_cookie(
        "acr_session",
        token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE") == "true",
        samesite="strict",
        max_age=28800,
    )
    audit(db, user, "LOGIN", user.id)
    return scalar(user)


@app.get("/api/v1/auth/me")
def me(user: User = Depends(current_user)):
    return scalar(user)


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(db_session)):
    session = db.get(
        LoginSession,
        hashlib.sha256(request.cookies.get("acr_session", "").encode()).hexdigest(),
    )
    if session:
        db.delete(session)
    response.delete_cookie("acr_session")
    return {"ok": True}


class CustomerBody(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    notes: str = Field(default="", max_length=2000)
    factor: str = "1"
    effective_date: date


class EditBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    notes: str | None = Field(default=None, max_length=2000)
    active: bool | None = None


@app.get("/api/v1/customers")
def customers(db: Session = Depends(db_session), user: User = Depends(current_user)):
    tenants = list(db.scalars(select(Tenant)))
    subs = list(db.scalars(select(Subscription)))
    by_customer = defaultdict(list)
    for d in db.scalars(select(Discount).order_by(Discount.day)):
        by_customer[d.customer_id].append(d)
    tenant_ids = defaultdict(list)
    sub_counts = defaultdict(int)
    for t in tenants:
        tenant_ids[t.customer_id].append(t.id)
    tenant_owner = {t.id: t.customer_id for t in tenants}
    for sub in subs:
        sub_counts[tenant_owner[sub.tenant_id]] += 1
    result = []
    for c in db.scalars(select(Customer).order_by(Customer.name)):
        discounts = by_customer[c.id]
        current = [
            d
            for d in discounts
            if d.day <= datetime.now(timezone.utc).date().isoformat()
        ]
        ts = tenant_ids[c.id]
        result.append(
            {
                **scalar(c),
                "discounts": [scalar(d) for d in discounts],
                "factor": current[-1].factor if current else None,
                "tenants": len(ts),
                "subscriptions": sub_counts[c.id],
            }
        )
    return result


@app.post("/api/v1/customers", status_code=201)
def create_customer(
    body: CustomerBody,
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    factor = dec(body.factor, Decimal("0"), Decimal("1"))
    c = Customer(name=body.name.strip(), notes=body.notes)
    if not c.name:
        fail("INVALID_NAME", "名称不能为空")
    db.add(c)
    db.flush()
    db.add(
        Discount(
            customer_id=c.id, day=body.effective_date.isoformat(), factor=str(factor)
        )
    )
    audit(db, user, "CREATE_CUSTOMER", c.id)
    return scalar(c)


@app.patch("/api/v1/{kind}/{id}")
def edit_entity(
    kind: str,
    id: str,
    body: EditBody,
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    model = {
        "customers": Customer,
        "tenants": Tenant,
        "subscriptions": Subscription,
    }.get(kind)
    if not model:
        fail("NOT_FOUND", "不支持的资源", 404)
    obj = require(db, model, id)
    if (
        model is Subscription
        and body.active is True
        and any(r.end == "9999-12-31" for r in selective_purge.ranges(db, id))
    ):
        fail(
            "ARCHIVED_HISTORY_READ_ONLY",
            "该订阅只保留其他客户的历史费用，不允许重新启用",
        )
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "notes" and model != Customer:
            continue
        if k == "name" and not v.strip():
            fail("INVALID_NAME", "名称不能为空")
        setattr(obj, k, v.strip() if isinstance(v, str) else v)
    audit(db, user, "UPDATE_" + kind.upper(), id)
    return scalar(obj)


class DiscountBody(BaseModel):
    factor: str
    effective_date: date


@app.post("/api/v1/customers/{id}/discounts")
def add_discount(
    id: str,
    body: DiscountBody,
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    db.scalar(select(Customer).where(Customer.id == id).with_for_update()) or require(
        db, Customer, id
    )
    factor = dec(body.factor, Decimal("0"), Decimal("1"))
    existing = db.scalar(
        select(Discount).where(
            Discount.customer_id == id, Discount.day == body.effective_date.isoformat()
        )
    )
    if existing:
        fail(
            "DISCOUNT_DATE_EXISTS",
            f"{existing.day} 已有系数 {existing.factor} 的折扣版本；新增操作不会覆盖历史，请选择其他生效日期。",
            409,
        )
    version = Discount(
        customer_id=id, day=body.effective_date.isoformat(), factor=str(factor)
    )
    db.add(version)
    db.flush()
    next_version = db.scalar(
        select(Discount)
        .where(Discount.customer_id == id, Discount.day > version.day)
        .order_by(Discount.day)
    )
    audit(db, user, "ADD_DISCOUNT", id)
    return {
        "ok": True,
        "version": scalar(version),
        "effective_until": str(date.fromisoformat(next_version.day) - timedelta(days=1))
        if next_version
        else None,
        "history_preserved": True,
    }


class TenantBody(BaseModel):
    id: str
    customer_id: str
    name: str = Field(min_length=1, max_length=150)
    client_id: str = ""
    client_secret: str = Field(default="", max_length=4000)


@app.get("/api/v1/tenants")
def tenants(db: Session = Depends(db_session), user: User = Depends(current_user)):
    states = {s.id: s for s in db.scalars(select(operations.AzureState))}
    return [
        {
            **scalar(t),
            "credential_configured": bool(t.secret),
            "last_sync": states[t.id].last_sync if t.id in states else "",
            "sync_status": states[t.id].sync if t.id in states else "PENDING",
            "auth_status": (
                (states.get(t.id).auth if states.get(t.id) else "NOT_VERIFIED")
                if t.secret
                else "NOT_CONFIGURED"
            ),
        }
        for t in db.scalars(select(Tenant).order_by(Tenant.name))
    ]


@app.post("/api/v1/tenants", status_code=201)
def create_tenant(
    body: TenantBody,
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    require(db, Customer, body.customer_id)
    if db.scalar(
        select(operations.Tombstone).where(operations.Tombstone.object_id == body.id)
    ):
        fail("PURGED_SCOPE", "该对象已永久删除，禁止重新导入")
    if body.client_id or body.client_secret:
        if user.role != "admin":
            fail("FORBIDDEN", "仅管理员可以配置凭据", 403)
        if not body.client_id or not body.client_secret:
            fail("CREDENTIAL_INCOMPLETE", "Client ID 和 Secret 必须一起提供")
        guid(body.client_id)
    t = Tenant(
        id=guid(body.id),
        customer_id=body.customer_id,
        name=body.name,
        client_id=body.client_id,
        secret=cipher().encrypt(body.client_secret.encode()).decode()
        if body.client_secret
        else "",
    )
    db.add(t)
    db.flush()
    audit(db, user, "CREATE_TENANT", t.id)
    if t.secret:
        operations.enqueue(db, user, "verify", "tenant", t.id)
    return scalar(t)


class CredentialBody(BaseModel):
    client_id: str
    client_secret: str = Field(min_length=1, max_length=4000)


@app.put("/api/v1/tenants/{id}/credentials")
def credential(
    id: str,
    body: CredentialBody,
    db: Session = Depends(db_session),
    user: User = Depends(admin),
):
    t = require(db, Tenant, id)
    t.client_id = guid(body.client_id)
    t.secret = cipher().encrypt(body.client_secret.encode()).decode()
    audit(db, user, "ROTATE_CREDENTIAL", id)
    verification = operations.state(db, id)
    verification.auth = verification.discovery = verification.cost = (
        verification.sync
    ) = "PENDING"
    verification.error = ""
    for substate in db.scalars(
        select(operations.AzureState)
        .join(Subscription, Subscription.id == operations.AzureState.id)
        .where(Subscription.tenant_id == id)
    ):
        substate.cost = "PENDING"
        substate.error = ""
    if t.active and require(db, Customer, t.customer_id).active:
        operations.enqueue(db, user, "verify", "tenant", id)
    return {"ok": True, "auth_status": "NOT_VERIFIED"}


class SubBody(BaseModel):
    id: str
    tenant_id: str
    name: str = Field(min_length=1, max_length=150)


@app.get("/api/v1/subscriptions")
def subscriptions(
    db: Session = Depends(db_session), user: User = Depends(current_user)
):
    tenant_map = {t.id: t for t in db.scalars(select(Tenant))}
    archived = {
        r.subscription_id
        for r in db.scalars(
            select(selective_purge.PurgedRange).where(
                selective_purge.PurgedRange.end == "9999-12-31"
            )
        )
    }
    states = {s.id: s for s in db.scalars(select(operations.AzureState))}
    tiers = subscription_tiers.snapshots(db)
    return [
        {
            **scalar(s),
            "tier": tiers.get(s.id, subscription_tiers.view(None)),
            "customer_id": tenant_map[s.tenant_id].customer_id,
            "archived": s.id in archived,
            "last_sync": states[s.id].last_sync if s.id in states else "",
            "access_status": (
                states.get(s.id).cost if states.get(s.id) else "PENDING_VERIFICATION"
            ),
        }
        for s in db.scalars(select(Subscription).order_by(Subscription.name))
    ]


@app.post("/api/v1/subscriptions", status_code=201)
def create_sub(
    body: SubBody, db: Session = Depends(db_session), user: User = Depends(current_user)
):
    require(db, Tenant, body.tenant_id)
    if db.scalar(
        select(operations.Tombstone).where(operations.Tombstone.object_id == body.id)
    ):
        fail("PURGED_SCOPE", "该对象已永久删除，禁止重新导入")
    s = Subscription(id=guid(body.id), tenant_id=body.tenant_id, name=body.name)
    db.add(s)
    db.flush()
    audit(db, user, "CREATE_SUBSCRIPTION", s.id)
    return scalar(s)


class FxBody(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: str
    effective_date: date


@app.get("/api/v1/settings/fx-rates")
def fx_list(db: Session = Depends(db_session), user: User = Depends(current_user)):
    return [scalar(f) for f in db.scalars(select(Fx).order_by(Fx.currency, Fx.day))]


@app.post("/api/v1/settings/fx-rates")
def fx_add(
    body: FxBody, db: Session = Depends(db_session), user: User = Depends(admin)
):
    if body.currency == "USD":
        fail("FIXED_RATE", "USD → USD 固定为 1")
    rate = dec(body.rate, Decimal("0.000000000001"))
    db.add(
        Fx(currency=body.currency, rate=str(rate), day=body.effective_date.isoformat())
    )
    audit(db, user, "ADD_FX", body.currency)
    return {"ok": True}


HEADERS = ["subscription_id", "usage_date", "service", "currency", "amount"]


@app.get("/api/v1/sync/template")
def template(user: User = Depends(current_user)):
    return Response(
        ",".join(HEADERS) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="acr-template.csv"'},
    )


@app.get("/api/v1/sync/batches")
def batches(db: Session = Depends(db_session), user: User = Depends(current_user)):
    return [
        scalar(b)
        for b in db.scalars(select(Batch).order_by(Batch.created.desc()).limit(100))
    ]


@app.post("/api/v1/sync/imports")
async def import_csv(
    file: UploadFile = File(...),
    subscription_id: str = Form(...),
    start: date = Form(...),
    end: date = Form(...),
    verified: bool = Form(False),
    complete: bool = Form(False),
    evidence: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    if verified and user.role != "admin":
        fail("FORBIDDEN", "成本基数验证仅管理员可声明", 403)
    if (verified or complete) and not evidence.strip():
        fail("EVIDENCE_REQUIRED", "声明基数已验证或完整覆盖时，必须填写核实依据")
    if len(evidence) > 2000 or len(reason) > 2000:
        fail("INVALID_INPUT", "说明过长")
    sub = db.scalar(
        select(Subscription).where(Subscription.id == subscription_id).with_for_update()
    )
    if not sub:
        fail("NOT_FOUND", "订阅不存在", 404)
    tenant = require(db, Tenant, sub.tenant_id)
    if (
        not sub.active
        or not tenant.active
        or not require(db, Customer, tenant.customer_id).active
    ):
        fail("INACTIVE", "不能向已停用对象导入数据")
    if end < start or (end - start).days > 366:
        fail("INVALID_RANGE", "日期范围必须在 367 天以内")
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        fail("FILE_TOO_LARGE", "当前版本支持最大 10 MB CSV")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if reader.fieldnames != HEADERS:
            try:
                data = operations.normalize_csv(data)
                reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
            except operations.AzureFailure:
                fail("UNSUPPORTED_SCHEMA", "无法识别或校验 CSV 格式")
        rows = []
        grouped = defaultdict(Decimal)
        for line, row in enumerate(reader, 2):
            if len(rows) >= 50000:
                fail("ROW_LIMIT", "当前版本每次最多 50,000 行")
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError()
                day = date.fromisoformat(row["usage_date"])
                if day.isoformat() != row["usage_date"] or not start <= day <= end:
                    raise ValueError()
                if guid(row["subscription_id"]) != subscription_id:
                    raise ValueError()
                service, currency = row["service"].strip(), row["currency"].strip()
                if (
                    not service
                    or len(service) > 150
                    or len(currency) != 3
                    or not currency.isascii()
                    or not currency.isupper()
                    or not currency.isalpha()
                ):
                    raise ValueError()
                amount = dec(row["amount"], max_scale=60)
                rows.append({**row, "amount": str(amount)})
                grouped[(day.isoformat(), service, currency)] += amount
            except (ValueError, HTTPException):
                fail(
                    "INVALID_CSV_ROW",
                    f"第 {line} 行格式、订阅、日期或金额不正确；整批未入账",
                )
        if not rows:
            fail("EMPTY_CSV", "CSV 为空。零消费请明确填写金额为 0 的记录")
    except (UnicodeDecodeError, csv.Error):
        fail("INVALID_CSV", "需要 UTF-8 编码的标准 CSV")
    # Require explicit zero rows for every claimed complete day: no absence-as-zero inference.
    excluded_history = selective_purge.ranges(db, subscription_id)
    if any(selective_purge.blocked(excluded_history, r["usage_date"]) for r in rows):
        fail("PURGED_HISTORY", "消费日期属于已永久删除的历史归属，拒绝重新导入")
    present_days = {r["usage_date"] for r in rows}
    if complete and present_days != set(days(start.isoformat(), end.isoformat())):
        fail("COVERAGE_INCOMPLETE", "完整批次需要覆盖区间内每一天（含显式零金额记录）")
    digest = hashlib.sha256(
        data
        + json.dumps(
            [subscription_id, str(start), str(end), verified, complete, evidence],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    existing = db.scalar(select(Batch).where(Batch.digest == digest))
    if existing:
        return {"duplicate": True, "batch": scalar(existing)}
    old = list(
        db.scalars(
            select(Coverage).where(
                Coverage.subscription_id == subscription_id,
                Coverage.day.in_(present_days),
            )
        )
    )
    if old and not complete:
        fail("INCOMPLETE_REPLACEMENT", "覆盖已有日期必须声明完整并提供该日全部服务数据")
    if old and not reason.strip():
        fail(
            "REPLACEMENT_REASON_REQUIRED",
            "当前日期已有有效数据，请填写修正原因后再导入",
        )
    b = Batch(
        digest=digest,
        filename=Path(file.filename or "import.csv").name[:200],
        created=now(),
        start=str(start),
        end=str(end),
        subscription_id=subscription_id,
        verified=verified,
        complete=complete,
        evidence=evidence,
        reason=reason,
        rows=json.dumps(rows),
        row_count=len(rows),
    )
    db.add(b)
    db.flush()
    for record in db.scalars(
        select(Fact).where(
            Fact.subscription_id == subscription_id, Fact.day.in_(present_days)
        )
    ):
        db.delete(record)
    for coverage in old:
        db.delete(coverage)
    db.flush()
    for day in present_days:
        db.add(Coverage(subscription_id=subscription_id, day=day, batch_id=b.id))
    for (day, service, currency), amount in grouped.items():
        db.add(
            Fact(
                subscription_id=subscription_id,
                day=day,
                service=service,
                currency=currency,
                amount=str(amount),
                batch_id=b.id,
            )
        )
    audit(db, user, "IMPORT_CSV", b.id)
    return {"duplicate": False, "batch": scalar(b)}


def calculate(
    db,
    start,
    end,
    customer_id="",
    tenant_id="",
    subscription_id="",
    include_report=True,
):
    customers = {c.id: c for c in db.scalars(select(Customer))}
    tenants = {t.id: t for t in db.scalars(select(Tenant))}
    subs = {
        s.id: s
        for s in db.scalars(select(Subscription))
        if (not subscription_id or s.id == subscription_id)
    }
    purged_ranges = defaultdict(list)
    for r in db.scalars(select(selective_purge.PurgedRange)):
        purged_ranges[r.subscription_id].append(r)
    histories = defaultdict(list)
    for o in db.scalars(
        select(operations.Ownership).order_by(operations.Ownership.day)
    ):
        histories[o.subscription_id].append(o)

    def owner(sid, day):
        if selective_purge.blocked(purged_ranges[sid], day):
            return None, None
        records = [o for o in histories[sid] if o.day <= day]
        if records:
            return records[-1].customer_id, records[-1].tenant_id
        tid = subs[sid].tenant_id
        return tenants[tid].customer_id, tid

    def scoped(sid, day):
        cid, tid = owner(sid, day)
        return (
            cid is not None
            and (not customer_id or cid == customer_id)
            and (not tenant_id or tid == tenant_id)
        )

    if customer_id or tenant_id:
        subs = {
            sid: sub
            for sid, sub in subs.items()
            if (
                not customer_id
                or tenants[sub.tenant_id].customer_id == customer_id
                or any(o.customer_id == customer_id for o in histories[sid])
            )
            and (
                not tenant_id
                or sub.tenant_id == tenant_id
                or any(o.tenant_id == tenant_id for o in histories[sid])
            )
        }
    discounts = defaultdict(list)
    for d in db.scalars(select(Discount).order_by(Discount.day)):
        discounts[d.customer_id].append(d)
    rates = defaultdict(list)
    for f in db.scalars(select(Fx).order_by(Fx.day)):
        rates[f.currency].append(f)
    batches = {
        b.id: b
        for b in db.execute(
            select(
                Batch.id,
                Batch.subscription_id,
                Batch.verified,
                Batch.complete,
                Batch.created,
            )
        )
    }
    daily, top, services = (
        defaultdict(Decimal),
        defaultdict(Decimal),
        defaultdict(Decimal),
    )
    sub_totals = defaultdict(Decimal)
    lifetime, total = Decimal(0), Decimal(0)
    excluded, first, report = 0, None, []
    verified_rows = 0
    lifetime_rows = 0
    fast = (
        operations.sql_financial(
            db, start, end, customer_id, tenant_id, subscription_id
        )
        if not include_report and not URL.startswith("sqlite")
        else None
    )
    if fast:
        lifetime, lifetime_rows, first = (
            fast["lifetime"],
            fast["lifetime_rows"],
            fast["first"],
        )
        total, verified_rows, excluded = (
            fast["total"],
            fast["verified_rows"],
            fast["excluded"],
        )
        daily, top, sub_totals, services = (
            fast["daily"],
            fast["top"],
            fast["subs"],
            fast["services"],
        )
    fact_query = (
        select(
            Fact.subscription_id,
            Fact.day,
            Fact.service,
            Fact.currency,
            Fact.amount,
            Fact.batch_id,
        )
        .where(Fact.subscription_id.in_(list(subs)))
        .order_by(Fact.day)
    )
    for f in [] if fast else db.execute(fact_query):
        if f.subscription_id not in subs:
            continue
        cid, historical_tid = owner(f.subscription_id, f.day)
        if cid is None:
            continue
        if customer_id and cid != customer_id:
            continue
        if tenant_id and historical_tid != tenant_id:
            continue
        first = min(first, f.day) if first else f.day
        ds = [d for d in discounts[cid] if d.day <= f.day]
        fx = [r for r in rates[f.currency] if r.day <= f.day]
        status = "VERIFIED"
        if not batches[f.batch_id].verified:
            status = "UNKNOWN_PRICE_BASIS"
        elif not ds:
            status = "DISCOUNT_MISSING"
        elif f.currency != "USD" and not fx:
            status = "FX_RATE_MISSING"
        value = None
        if status == "VERIFIED":
            value = (
                Decimal(f.amount)
                * Decimal(ds[-1].factor)
                * (Decimal(1) if f.currency == "USD" else Decimal(fx[-1].rate))
            )
            lifetime += value
            lifetime_rows += 1
        if not start <= f.day <= end:
            continue
        if value is None:
            excluded += 1
        else:
            verified_rows += 1
            total += value
            daily[f.day] += value
            top[cid] += value
            sub_totals[f.subscription_id] += value
            services[f.service] += value
        if include_report:
            report.append(
                {
                    "customer": customers[cid].name,
                    "tenant_id": historical_tid,
                    "subscription_id": f.subscription_id,
                    "usage_date": f.day,
                    "service": f.service,
                    "currency": f.currency,
                    "raw_amount": f.amount,
                    "discount": ds[-1].factor if ds else "",
                    "discount_effective": ds[-1].day if ds else "",
                    "fx_rate": "1"
                    if f.currency == "USD"
                    else fx[-1].rate
                    if fx
                    else "",
                    "fx_effective": fx[-1].day if fx and f.currency != "USD" else "",
                    "acr_usd": str(value) if value is not None else "",
                    "status": status,
                    "coverage": "COMPLETE_DECLARED"
                    if batches[f.batch_id].complete
                    else "UNKNOWN",
                    "batch_id": f.batch_id,
                }
            )
    cv = {
        (c.subscription_id, c.day): c
        for c in db.execute(
            select(Coverage.subscription_id, Coverage.day, Coverage.batch_id).where(
                Coverage.day >= start, Coverage.day <= end
            )
        )
        if c.subscription_id in subs and scoped(c.subscription_id, c.day)
    }
    selected_days = days(start, end)
    expected = sum(1 for sid in subs for day in selected_days if scoped(sid, day))
    selected_subs = {sid for sid in subs if any(scoped(sid, d) for d in selected_days)}
    complete_count = sum(batches[c.batch_id].complete for c in cv.values())
    state = (
        "NO_DATA"
        if not cv
        else "COMPLETE_DECLARED"
        if expected and complete_count == expected and not excluded
        else "PARTIAL"
    )
    return {
        "total": str(total) if verified_rows else None,
        "lifetime": str(lifetime) if lifetime_rows else None,
        "first_date": first,
        "excluded": excluded,
        "coverage": state,
        "complete_days": complete_count,
        "received_days": len(cv),
        "expected_days": expected,
        "customers": (1 if customer_id in customers else 0)
        if customer_id
        else len({owner(sid, end)[0] for sid in selected_subs})
        if tenant_id or subscription_id
        else len(customers),
        "subscriptions": len(selected_subs),
        "last_sync": max(
            (b.created for b in batches.values() if b.subscription_id in selected_subs),
            default=None,
        ),
        "trend": [
            {"day": d, "value": str(daily[d]) if d in daily else None}
            for d in days(start, end)
        ],
        "top": [
            {"id": cid, "name": customers[cid].name, "value": str(v)}
            for cid, v in sorted(top.items(), key=lambda item: item[1], reverse=True)[
                :5
            ]
        ],
        "customer_totals": {cid: str(v) for cid, v in top.items()},
        "subscription_totals": {sid: str(v) for sid, v in sub_totals.items()},
        "services": [
            {"name": k, "value": str(v)}
            for k, v in sorted(services.items(), key=lambda item: item[1], reverse=True)
        ],
        "report": report,
    }


def valid_range(start, end):
    if (
        start.day == 1
        and start == datetime.now(timezone.utc).date()
        and end == start - timedelta(days=1)
    ):
        return  # UTC month has no completed days yet.
    if end < start or (end - start).days > 366:
        fail("INVALID_RANGE", "查询区间应在 367 天以内")


@app.get("/api/v1/dashboard")
def dashboard(
    start: date,
    end: date,
    customer_id: str = "",
    tenant_id: str = "",
    subscription_id: str = "",
    granularity: str = "day",
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    valid_range(start, end)
    if granularity not in ["day", "month"]:
        fail("INVALID_GRANULARITY", "时间粒度不正确")
    payload = operations.dashboard_cached(
        db,
        dict(
            start=str(start),
            end=str(end),
            customer_id=customer_id,
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            granularity=granularity,
        ),
        lambda: operations.dashboard_view(
            db,
            str(start),
            str(end),
            customer_id,
            tenant_id,
            subscription_id,
            granularity,
        ),
    )

    return JSONResponse(payload)


@app.get("/api/v1/reports/export")
def export(
    start: date,
    end: date,
    customer_id: str = "",
    tenant_id: str = "",
    subscription_id: str = "",
    db: Session = Depends(db_session),
    user: User = Depends(current_user),
):
    valid_range(start, end)
    rows = calculate(db, str(start), str(end), customer_id, tenant_id, subscription_id)[
        "report"
    ]
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula injection, preserving numeric negatives.
            writer.writerow(
                {
                    k: "'" + v
                    if isinstance(v, str)
                    and v.startswith(("=", "+", "@", "\t", "\r", "-"))
                    and k in ("customer", "service")
                    else v
                    for k, v in row.items()
                }
            )
    audit(db, user, "EXPORT_REPORT", f"{start}/{end}")
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="acr-report.csv"'},
    )


@app.get("/api/v1/audit")
def audits(db: Session = Depends(db_session), user: User = Depends(admin)):
    return [
        scalar(a)
        for a in db.scalars(select(Audit).order_by(Audit.created.desc()).limit(100))
    ]


from backend import operations

operations.install(app)
from backend import selective_purge
from backend import resource_costs

resource_costs.install(app)
from backend import subscription_tiers

subscription_tiers.install(app)

operations.replay_tombstones()

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
