import getpass
import re
import sys

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models import PlatformAdmin, User


USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
MIN_PASSWORD_LENGTH = 12


def create_admin() -> None:
    username = input("Username (3-32 characters): ").strip().lower()

    if not USERNAME_PATTERN.fullmatch(username):
        print("Invalid username.")
        return

    display_name = input("Display name: ").strip()
    if not display_name or len(display_name) > 100:
        print("Display name must contain 1-100 characters.")
        return

    password = getpass.getpass("Password (at least 12 characters): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print("Password must contain at least 12 characters.")
        return

    confirm_password = getpass.getpass("Confirm password: ")
    if password != confirm_password:
        print("Passwords do not match.")
        return

    db = SessionLocal()
    try:
        existing_user = db.scalar(
            select(User).where(User.username == username)
        )
        if existing_user is not None:
            print("Username already exists. No changes were made.")
            return

        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            status="active",
        )
        db.add(user)
        db.flush()

        admin = PlatformAdmin(
            user_id=user.id,
            is_active=True,
        )
        db.add(admin)
        db.commit()

        print(f"Platform administrator created successfully: {username}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    try:
        create_admin()
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        sys.exit(1)
    except Exception as exc:
        print(f"Failed to create administrator: {exc}")
        sys.exit(1)
