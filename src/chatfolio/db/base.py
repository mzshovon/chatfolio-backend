from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for all ORM models. Import model modules in alembic/env.py for autogenerate."""
