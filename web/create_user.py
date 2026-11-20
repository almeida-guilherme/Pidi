"""Adds an account from the command line, for sites where sign-up is closed (PDD_ALLOW_SIGNUP=false):
the person can then log in with the Google account of that email.

    .venv/bin/python -m web.create_user someone@company.com
    docker compose exec app python -m web.create_user someone@company.com   (on the server)
"""
import sys
from dotenv import load_dotenv
from sqlmodel import select

from web.auth import normalize_email, welcome
from web.config import Settings
from web.db import User, make_engine, session


def main() -> None:
    load_dotenv()
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        email = normalize_email(sys.argv[1])
    except ValueError as exc:
        sys.exit(str(exc))
    settings = Settings()
    with session(make_engine(settings.database_url)) as db:
        if db.exec(select(User).where(User.email == email)).first():
            sys.exit(f"{email} already has an account.")
        # The privacy notice is accepted by the person on their first visit.
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
        welcome(db, settings, user)
    paid = (f"with {max(settings.signup_credits, 0)} credits" if settings.uses_credits
            else "(they connect their own OpenAI key on the Account page)")
    print(f"Account added for {email} {paid}; they can log in with Google.")


if __name__ == "__main__":
    main()
