from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from db import cache, close_pool, init_schema, open_pool, pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    await init_schema()
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


class OrderRequest(BaseModel):
    item: str
    quantity: int


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item: str
    quantity: int


@app.get("/")
async def root():
    return {"message": "Watchtower Orders Service"}


@app.get("/healthz")
async def health():
    return {"status": "Service is healthy"}


@app.get("/orders/{id}", response_model=OrderResponse)
async def get_orders(id: int):
    cached = cache.get(id)
    if cached is not None:
        return cached

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, item, quantity FROM orders WHERE id = %s", (id,)
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")

    order = OrderResponse(id=row[0], item=row[1], quantity=row[2])
    cache[id] = order
    return order


@app.post("/orders", response_model=OrderResponse)
async def create_order(order: OrderRequest):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO orders (item, quantity) VALUES (%s, %s) RETURNING id",
                (order.item, order.quantity),
            )
            row = await cur.fetchone()

    new_order = OrderResponse(id=row[0], item=order.item, quantity=order.quantity)
    cache[new_order.id] = new_order
    return new_order
