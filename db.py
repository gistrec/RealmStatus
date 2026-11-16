import os

from typing import Optional

import certifi
from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


MYSQL_USER     = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_HOST     = os.getenv("MYSQL_HOST")
MYSQL_PORT     = os.getenv("MYSQL_PORT")
MYSQL_DB       = os.getenv("MYSQL_DB")

# TLS включён по умолчанию; MYSQL_CHECK_SSL=0 отключает его (локальная база без TLS).
MYSQL_CHECK_SSL = os.getenv("MYSQL_CHECK_SSL", "1") != "0"

# Секунды. Без таймаута зависшее соединение съедает весь лимит выполнения функции.
MYSQL_CONNECT_TIMEOUT = 5


connect_args: dict[str, object] = {"connect_timeout": MYSQL_CONNECT_TIMEOUT}

if MYSQL_CHECK_SSL:
    # Сервер доступен через публичный адрес, поэтому TLS обязателен, а сертификат
    # и имя хоста проверяются по публичным CA (у хоста сертификат Let's Encrypt).
    connect_args["ssl_mode"] = "VERIFY_IDENTITY"
    connect_args["ssl"] = {"ca": certifi.where()}

engine = create_engine(
    f"mysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}",
    pool_pre_ping=True,
    connect_args=connect_args,
)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = 'settings'

    key: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)


def set_setting(key: str, value: str) -> None:
    with SessionLocal() as session:
        setting = session.get(Setting, key)
        if setting:
            setting.value = value
        else:
            session.add(Setting(key=key, value=value))

        session.commit()


def get_setting(key: str) -> Optional[str]:
    with SessionLocal() as session:
        setting = session.get(Setting, key)
        return setting.value if setting else None


def remove_setting(key: str) -> None:
    with SessionLocal() as session:
        setting = session.get(Setting, key)
        if setting:
            session.delete(setting)
            session.commit()
