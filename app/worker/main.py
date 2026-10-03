import asyncio
import json
import os

import httpx
import redis.asyncio

ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders:8000")

# Deliberately unbounded, never cleared -- fragility is intentional
LEAK_ENABLED = os.getenv("MEMORY_LEAK_ENABLED", "false").lower() == "true"
_leaked_memory: list[bytearray] = []

# Simulated work per job; 0 drains instantly, raise it so the queue can back up
PROCESS_MS = float(os.getenv("PROCESS_MS", "0"))


async def get_redis_client():
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", 6379))
    return redis.asyncio.Redis(host=redis_host, port=redis_port, decode_responses=True)


async def get_job_from_queue(redis_client, queue_name):
    return await redis_client.blpop(queue_name)


async def process_job(payload):
    print(f"Processing order_id={payload['order_id']} quantity={payload['quantity']}")

    await asyncio.sleep(PROCESS_MS / 1000)

    if LEAK_ENABLED:
        _leaked_memory.append(bytearray(1_000_000))


async def update_job_status(client: httpx.AsyncClient, order_id: int, status: str) -> None:
    response = await client.patch(
        f"{ORDERS_URL}/orders/{order_id}/status",
        json={"status": status},
    )
    response.raise_for_status()


async def main():
    redis_client = await get_redis_client()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                job = await get_job_from_queue(redis_client, "orders_queue")
                payload = json.loads(job[1])
                order_id = payload["order_id"]

                await process_job(payload)
                await update_job_status(client, order_id, "fulfilled")
            except Exception as exc:
                print(f"Failed to process job: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
