"""Gives an account credits (or takes them back, with a negative number), with a note shown in its history.
Without a number, shows the account's balance.

    .venv/bin/python -m web.grant_credits someone@company.com 30 "October"
    docker compose exec app python -m web.grant_credits someone@company.com 30 "October"   (on the server)
"""
import sys
from dotenv import load_dotenv
from sqlmodel import select

from web.auth import normalize_email
from web.config import Settings
from web.credits import balance, grant
from web.db import User, make_engine, session


def main() -> None:
    load_dotenv()
    if len(sys.argv) not in (2, 3, 4):
        sys.exit(__doc__)
    try:
        email = normalize_email(sys.argv[1])
        credits = int(sys.argv[2]) if len(sys.argv) > 2 else None
    except ValueError as exc:
        sys.exit(str(exc) if "email" in str(exc) else "The number of credits must be a whole number.")
    engine = make_engine(Settings().database_url)
    with session(engine) as db:
        user = db.exec(select(User).where(User.email == email)).first()
        if user is None:
            sys.exit(f"{email} has no account (add one with python -m web.create_user).")
        if credits is None:
            print(f"{email} has {balance(db, user.id)} credits.")
            return
    note = sys.argv[3].strip()[:120] if len(sys.argv) > 3 else "Given by the administrator"
    print(f"{email} now has {grant(engine, user.id, credits, note)} credits.")


if __name__ == "__main__":
    main()
