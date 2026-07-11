"""交互式重置唯一初始管理员密码。"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from media_pilot.accounts.passwords import hash_password, validate_password
from media_pilot.config import AppConfig
from media_pilot.repository.account_repositories import UserRepository
from media_pilot.repository.database import create_session_factory
from media_pilot.repository.models import User


def reset_initial_admin_password(
    session_factory: sessionmaker[Session],
    password: str,
) -> str:
    password_hash = hash_password(password)
    with session_factory() as session:
        admins = list(session.scalars(select(User).where(User.role == "admin")))
        if len(admins) != 1:
            raise RuntimeError(f"expected exactly one initial admin, found {len(admins)}")
        admin = admins[0]
        UserRepository(session).set_password(admin, password_hash)
        session.commit()
        return admin.username


def _session_factory_from_environment() -> sessionmaker[Session]:
    config = AppConfig(
        database_dir=Path(os.getenv("MEDIA_PILOT_DATABASE_DIR", "/data/db")),
        database_url=os.getenv("MEDIA_PILOT_DATABASE_URL") or None,
    )
    return create_session_factory(config)


def main() -> int:
    password = getpass.getpass("新管理员密码: ")
    confirmation = getpass.getpass("再次输入新密码: ")
    if password != confirmation:
        print("两次输入的密码不一致。")
        return 1
    try:
        validate_password(password)
        username = reset_initial_admin_password(
            _session_factory_from_environment(),
            password,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"密码重置失败: {exc}")
        return 1
    print(f"管理员 {username} 的密码已重置，现有会话已全部撤销。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
