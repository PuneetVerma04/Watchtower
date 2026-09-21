import asyncio
import os

from fastapi import FastAPI

LATENCY_MS = float(os.getenv("LATENCY_MS", 100))

app = FastAPI()

@app.get("/")
async def root():
    await asyncio.sleep(LATENCY_MS / 1000)  # Simulate latency
    return {"message": "Watchtower Inventory Service"}


@app.get("/healthz")
async def health():
    return {"status": "Service is healthy"}


@app.get("/check/{item}")
async def get_inventory(item: str):
    await asyncio.sleep(LATENCY_MS / 1000)  # Simulate latency
    # Simulate inventory data
    inventory_data = {
        "item": item,
        "quantity": 100,  # Example quantity
        "status": "available",
    }
    return inventory_data
