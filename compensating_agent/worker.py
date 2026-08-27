"""
compensating_agent/worker.py
Consumes compensation_requests from RabbitMQ and runs the LangGraph compensation.

Crash recovery: this worker builds its LangGraph compensation graph with a
real, Postgres-backed checkpointer (AsyncPostgresSaver) at startup, using
workflow_id as the LangGraph thread_id. If this process is killed mid-saga
and a new worker process starts up, RabbitMQ redelivers the still-unacked
message (message.process() only acks on clean completion), and
run_compensation() sees an existing checkpoint for that thread_id and
resumes from the last completed node instead of restarting the whole undo
sequence — which would otherwise double-refund whatever had already
succeeded. See compensating_agent/graph.py::run_compensation() and
test_level2.py::test_checkpointer_resumes_after_interruption for the
behavioral proof.
"""
import asyncio
import json
import logging
import aio_pika
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from compensating_agent.graph import build_graph, run_compensation
from state_log.redis_client import publish_event
import db.client as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def process_message(
    message: aio_pika.abc.AbstractIncomingMessage, graph
) -> None:
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
            result = await run_compensation(wid, failing_step, graph=graph)
            publish_event(wid, "compensation_complete", {
                "workflow_id": wid,
                "has_udir": result.get("udir_payload") is not None,
                "has_liability_report": result.get("liability_report") is not None,
            })
            logger.info(f"Compensation complete for workflow: {wid}")
        except Exception as exc:
            publish_event(wid, "compensation_error", {"error": str(exc)})
            logger.error(f"Compensation error for {wid}: {exc}")
            # Deliberately not re-raising: message.process() will still ack
            # on normal exit from this block. A node-level failure inside
            # run_compensation already has its own handling (DLQ, fail-safe
            # defaults); an exception escaping this far means something
            # unexpected happened, and the checkpoint for whatever nodes DID
            # complete is already durably saved in Postgres regardless of
            # what we do here — a future run_compensation() call for the
            # same workflow_id will still resume from that point rather
            # than lose progress.

async def main() -> None:
    await db.run_migrations()
    await db.init_pool()

    async with AsyncPostgresSaver.from_conn_string(db.DSN) as checkpointer:
        await checkpointer.setup()
        graph = build_graph(checkpointer=checkpointer)

        connection = await aio_pika.connect_robust("amqp://guest:guest@localhost:5673/")
        async with connection:
            channel = await connection.channel()
            queue = await channel.declare_queue("compensation_requests", auto_delete=False)

            logger.info("Worker started. Listening for compensation_requests...")
            await queue.consume(lambda message: process_message(message, graph))

            try:
                await asyncio.Future()
            finally:
                await db.close_pool()

if __name__ == "__main__":
    asyncio.run(main())
