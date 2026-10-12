from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app import models  # noqa: F401  (registers tables on Base)
from app.api import router
from app.config import Settings
from app.database import Base, make_session_factory


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    engine, session_factory = make_session_factory(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(router)
    return app


app = create_app()
