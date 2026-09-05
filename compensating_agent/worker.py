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
from contextlib import AsyncExitStack
import aio_pika
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from compensating_agent.graph import build_graph, run_compensation
from state_log.redis_client import publish_event
import db.client as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def process_message(
    message: aio_pika.abc.AbstractIncomingMessage, get_graph
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
        # get_graph() returns the current (checkpointer, graph) pair. On a
        # dead-connection error (stale idle Postgres socket — see
        # CHECKPOINTER_DSN's comment in db/client.py) we ask for a FRESH
        # pair and retry exactly once, instead of letting one bad socket
        # kill an otherwise-healthy demo run. A second failure is a real
        # problem (Postgres actually down), not a transient idle-drop, so
        # it's reported normally rather than retried again.
        for attempt in (1, 2):
            _, graph = await get_graph(fresh=attempt == 2)
            try:
                result = await run_compensation(wid, failing_step, graph=graph)
                publish_event(wid, "compensation_complete", {
                    "workflow_id": wid,
                    "has_udir": result.get("udir_payload") is not None,
                    "has_liability_report": result.get("liability_report") is not None,
                })
                logger.info(f"Compensation complete for workflow: {wid}")
                return
            except Exception as exc:
                is_conn_error = "connection" in str(exc).lower() or "consuming input" in str(exc).lower()
                if attempt == 1 and is_conn_error:
                    logger.warning(f"Checkpointer connection looks dead ({exc}) — rebuilding and retrying once for {wid}")
                    continue
                publish_event(wid, "compensation_error", {"error": str(exc)})
                logger.error(f"Compensation error for {wid}: {exc}")
                return
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

    # Manages the (checkpointer, graph) pair ourselves via AsyncExitStack
    # instead of a single `async with ... as checkpointer:` wrapping the
    # whole process lifetime — that pattern is exactly what let one stale
    # idle connection take down every compensation run for the rest of the
    # process. get_graph(fresh=True) closes the old connection and opens a
    # brand new one; process_message() above calls it after a
    # connection-abort error so a dead socket self-heals on the next
    # message instead of requiring a manual worker restart.
    stack = AsyncExitStack()
    state = {"checkpointer": None, "graph": None}

    async def get_graph(fresh: bool = False):
        if fresh and state["checkpointer"] is not None:
            await stack.aclose()
            state["checkpointer"] = None
        if state["checkpointer"] is None:
            checkpointer = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(db.CHECKPOINTER_DSN)
            )
            await checkpointer.setup()
            state["checkpointer"] = checkpointer
            state["graph"] = build_graph(checkpointer=checkpointer)
        return state["checkpointer"], state["graph"]

    await get_graph()

    try:
        connection = await aio_pika.connect_robust("amqp://guest:guest@localhost:5673/")
        async with connection:
            channel = await connection.channel()
            # Dead-letter queue — receives messages that exceeded the max-delivery
            # count, so a stale/broken workflow_id can't loop forever in the main
            # queue (as happened with 780f6f74 in the previous session).
            dlq = await channel.declare_queue(
                "compensation_requests_dlq",
                auto_delete=False,
                durable=True,
            )
            queue = await channel.declare_queue(
                "compensation_requests",
                auto_delete=False,
                durable=True,
                arguments={
                    "x-queue-type": "quorum",        # x-delivery-limit only exists on quorum queues
                    "x-delivery-limit": 5,          # aio-pika / RabbitMQ Streams DLQ cap
                    "x-dead-letter-exchange": "",    # default exchange
                    "x-dead-letter-routing-key": "compensation_requests_dlq",
                },
            )

            logger.info("Worker started. Listening for compensation_requests...")
            await queue.consume(lambda message: process_message(message, get_graph))

            try:
                await asyncio.Future()
            finally:
                await db.close_pool()
    finally:
        await stack.aclose()

if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        # psycopg's async connection cannot run under Windows' default
        # ProactorEventLoop (used by asyncio.run() since Python 3.8+ on
        # Windows). Force the Selector-based loop instead.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
