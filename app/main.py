import asyncio
from contextlib import asynccontextmanager
from typing import Annotated
from warnings import warn

from fastapi import Depends, FastAPI
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine

import models  # noqa
from config import Settings
from deps import get_handler
from handler import MessageHandler
from whatsapp import WhatsAppClient
from whatsapp.init_groups import gather_groups

settings = Settings()  # pyright: ignore [reportCallIssue]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings
    app.state.settings = settings
    app.state.whatsapp = WhatsAppClient(
        settings.whatsapp_host,
        settings.whatsapp_basic_auth_user,
        settings.whatsapp_basic_auth_password,
    )

    if settings.db_uri.startswith("postgresql://"):
        warn("use 'postgresql+asyncpg://' instead of 'postgresql://' in db_uri")
    engine = create_async_engine(
        settings.db_uri,
        pool_size=20,
        max_overflow=40,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=600,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    asyncio.create_task(gather_groups(engine, app.state.whatsapp))

    app.state.db_engine = engine

    try:
        yield
    finally:
        await engine.dispose()


# Initialize FastAPI app
app = FastAPI(title="Webhook API", lifespan=lifespan)


@app.post("/webhook")
async def webhook(
    payload: models.WhatsAppWebhookPayload,
    handler: Annotated[MessageHandler, Depends(get_handler)],
) -> str:
    if payload.from_:
        await handler(payload)

    return "ok"


if __name__ == "__main__":
    import uvicorn

    print(f"Running on {settings.host}:{settings.port}")

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
