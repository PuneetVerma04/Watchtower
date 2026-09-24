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
                order_id SERIAL PRIMARY KEY,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )


async def update_order_status(order_id: int, status: str) -> tuple | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE orders SET status = %s WHERE order_id = %s "
                "RETURNING order_id, item, quantity, status, created_at",
                (status, order_id),
            )
            return await cur.fetchone()
