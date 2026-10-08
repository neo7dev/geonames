from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from geonames_server.database.session import asyncSessionLocal


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with asyncSessionLocal() as session:
        yield session
