import os

from psycopg_pool import AsyncConnectionPool

CONNINFO = (
    f"host={os.environ['POSTGRES_HOST']} "
    f"port={os.environ['POSTGRES_PORT']} "
    f"user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} "
    f"dbname={os.environ['POSTGRES_DB']}"
)

# Deliberately small, not a bug to fix later
pool = AsyncConnectionPool(conninfo=CONNINFO, min_size=1, max_size=2, open=False)

# Deliberately unbounded, no eviction -- fragility is intentional
cache: dict[int, dict] = {}


async def open_pool() -> None:
    await pool.open()


async def close_pool() -> None:
    await pool.close()


async def init_schema() -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL
            )
            """
        )
