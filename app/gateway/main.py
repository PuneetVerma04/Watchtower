import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders:8000")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately no timeout, not a bug to fix later
    app.state.client = httpx.AsyncClient(timeout=None)
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Watchtower Gateway"}


@app.get("/healthz")
async def health():
    return {"status": "Watchtower Gateway is healthy"}


@app.get("/orders/{id}")
async def get_order(id: int, request: Request):
    resp = await request.app.state.client.get(f"{ORDERS_URL}/orders/{id}")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


@app.post("/orders")
async def create_order(request: Request):
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{ORDERS_URL}/orders",
        content=body,
        headers={"content-type": "application/json"},
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )
