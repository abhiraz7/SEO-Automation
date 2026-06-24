from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Primary application database (SQLite for local dev)
# To migrate to PostgreSQL: replace this URL with a postgresql:// connection string
# and remove connect_args — all models use standard SQLAlchemy types with no SQLite-isms
DATABASE_URL = "sqlite:///seo_automation.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
