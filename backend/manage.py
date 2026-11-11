"""Explicit local administration. No public registration or automatic demo account."""

import argparse
import getpass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from sqlalchemy import select
from backend.app import (
    SessionLocal,
    User,
    Customer,
    Discount,
    Tenant,
    Subscription,
    Batch,
    Fact,
    Coverage,
    Fx,
    hash_password,
    now,
)

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["create-user", "demo"])
parser.add_argument("--email")
parser.add_argument("--name", default="管理员")
parser.add_argument("--role", default="admin", choices=["admin", "member"])
args = parser.parse_args()
with SessionLocal.begin() as db:
    if args.command == "create-user":
        email = (args.email or input("Email: ")).strip().lower()
        if "@" not in email:
            raise SystemExit("Invalid email")
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit("User already exists")
        password = getpass.getpass("Password (minimum 12 characters): ")
        if len(password) < 12 or password != getpass.getpass("Confirm password: "):
            raise SystemExit("Password invalid/mismatch")
        db.add(
            User(
                email=email,
                name=args.name,
                role=args.role,
                password=hash_password(password),
            )
        )
        print("User created.")
    else:
        if db.scalar(select(User)) or db.scalar(select(Customer)):
            raise SystemExit(
                "Demo seed requires an empty database; existing data was preserved."
            )
        db.add(
            User(
                email="demo@azure-lens.local",
                name="演示管理员",
                role="admin",
                password=hash_password("AzureLens-Demo-2026!"),
            )
        )
        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        start = end - timedelta(days=89)
        db.add(Fx(currency="CNY", day="2020-01-01", rate="0.14"))
        names = [
            "远山科技 · DEMO",
            "星河数字 · DEMO",
            "云启制造 · DEMO",
            "北辰零售 · DEMO",
            "澄明医疗 · DEMO",
            "森屿设计 · DEMO",
        ]
        for i, name in enumerate(names):
            c = Customer(name=name, notes="合成演示数据，与真实 Azure 账单无关。")
            db.add(c)
            db.flush()
            db.add(Discount(customer_id=c.id, day="2020-01-01", factor="0.85"))
            t = Tenant(
                id=f"10000000-0000-4000-8000-{i + 1:012d}",
                customer_id=c.id,
                name=name.split(" · ")[0] + "租户",
            )
            db.add(t)
            db.flush()
            sub = Subscription(
                id=f"20000000-0000-4000-8000-{i + 1:012d}",
                tenant_id=t.id,
                name=name.split(" · ")[0] + " · Production",
            )
            db.add(sub)
            db.flush()
            b = Batch(
                digest=hashlib.sha256(name.encode()).hexdigest(),
                filename=f"demo-cost-{i + 1}.csv",
                created=now(),
                start=str(start),
                end=str(end),
                subscription_id=sub.id,
                verified=True,
                complete=True,
                evidence="仅用于演示的合成折扣前成本，不是 Azure 真实验证结论",
                reason="",
                rows="[]",
                row_count=180,
            )
            db.add(b)
            db.flush()
            for j in range(90):
                day = str(start + timedelta(days=j))
                db.add(Coverage(subscription_id=sub.id, day=day, batch_id=b.id))
                for k, service in enumerate(["Virtual Machines", "Azure SQL Database"]):
                    amount = (
                        Decimal(1100 - i * 145)
                        * (Decimal("1") + Decimal(j % 7) / 20 + Decimal(j) / 250)
                        * (Decimal("0.68") if k == 0 else Decimal("0.32"))
                    )
                    db.add(
                        Fact(
                            subscription_id=sub.id,
                            day=day,
                            service=service,
                            currency="USD",
                            amount=str(amount.quantize(Decimal("0.0001"))),
                            batch_id=b.id,
                        )
                    )
        print(
            "Synthetic demo initialized. Login details are in README.md. Do not deploy this demo database to production."
        )
