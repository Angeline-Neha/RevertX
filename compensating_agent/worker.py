"""
compensating_agent/worker.py
Consumes compensation_requests from RabbitMQ and runs the LangGraph compensation.
"""
import asyncio
import json
import logging
import aio_pika

from compensating_agent.graph import run_compensation
from state_log.redis_client import publish_event
import db.client as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def process_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process():
        body = message.body.decode()
        data = json.loads(body)
        wid = data["workflow_id"]
        failing_step = data["failing_step"]
        
        logger.info(f"Received compensation request for workflow: {wid}")
        publish_event(wid, "compensation_started", {
            "workflow_id": wid, "trigger": "mandate_limit_exceeded",
        })
        try:
            result = await run_compensation(wid, failing_step)
            publish_event(wid, "compensation_complete", {
                "workflow_id": wid,
                "has_udir": result.get("udir_payload") is not None,
                "has_liability_report": result.get("liability_report") is not None,
            })
            logger.info(f"Compensation complete for workflow: {wid}")
        except Exception as exc:
            publish_event(wid, "compensation_error", {"error": str(exc)})
            logger.error(f"Compensation error for {wid}: {exc}")

async def main() -> None:
    await db.run_migrations()
    await db.init_pool()
    
    connection = await aio_pika.connect_robust("amqp://guest:guest@localhost:5673/")
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue("compensation_requests", auto_delete=False)
        
        logger.info("Worker started. Listening for compensation_requests...")
        await queue.consume(process_message)
        
        try:
            await asyncio.Future()
        finally:
            await db.close_pool()

if __name__ == "__main__":
    asyncio.run(main())
