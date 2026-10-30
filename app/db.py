from sqlmodel import Session, SQLModel, create_engine

from . import config

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
