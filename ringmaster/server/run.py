"""Entry point for the Ringmaster server process.

This module wires together all application components — FastAPI app, Ollama
client, sleep inhibitor, background worker — and runs them concurrently under
a single asyncio event loop.

Why a run.py rather than a console_scripts entry point in setup.cfg?
A standalone module is simpler to invoke directly (``python -m
ringmaster.server.run``), easier to find, and does not require the package
to be installed in editable mode during development.

Startup sequence
----------------
1. Parse CLI arguments (config file path).
2. Set up logging so early errors are visible.
3. Call ``create_app()`` to build the FastAPI app and wire all singletons.
4. Start the background worker loop as an asyncio Task.
5. Start uvicorn to serve the HTTP API.
6. On shutdown: cancel the worker task, release the inhibitor, close the
   Ollama HTTP client.

Shutdown is triggered by uvicorn handling SIGINT/SIGTERM.  The
``server.serve()`` coroutine returns when uvicorn has finished draining
connections; at that point the cleanup code in the ``finally`` block runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn

from ringmaster.ollama import OllamaClient
from ringmaster.power.inhibitor import SleepInhibitor
from ringmaster.server.app import create_app
from ringmaster.server import deps
from ringmaster.webhooks import deliver_webhook
from ringmaster.worker import Worker

logger = logging.getLogger(__name__)


async def worker_loop(worker: Worker, interval: float = 2.0) -> None:
    """Continuously poll the task queue and execute tasks one at a time.

    Runs forever until cancelled.  When the queue is empty, sleeps for
    *interval* seconds before checking again — this avoids a busy-wait that
    would spin the CPU at 100% with no work to do.

    We sleep only when no task was processed so that a burst of queued tasks
    is drained as quickly as possible without any artificial delay between them.

    Args:
        worker: The Worker instance used to execute tasks.
        interval: Seconds to sleep between polls when the queue is empty.
            Default 2 s balances responsiveness against CPU idle overhead.

    Raises:
        asyncio.CancelledError: Propagated cleanly on task cancellation so the
            caller's ``finally`` block runs without extra noise in the logs.
    """
    logger.info("Worker loop started (poll interval: %.1fs when idle).", interval)
    try:
        while True:
            try:
                ran = await worker.run_one()
            except asyncio.CancelledError:
                # Cancelled inside run_one — propagate immediately.
                raise
            except Exception:
                # Log and continue: a single task failure must not crash the
                # loop.  run_one() already handles OllamaError internally, so
                # exceptions here represent unexpected programming errors.
                logger.exception("Unexpected error in worker.run_one(); continuing.")
                ran = False

            if not ran:
                # Queue was empty — wait before polling again.
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Worker loop cancelled; shutting down.")
        raise


def main() -> None:
    """Parse arguments, configure logging, and run the Ringmaster server.

    This is the top-level entry point.  Everything runs inside a single
    ``asyncio.run()`` call so that the worker loop and uvicorn share the
    same event loop and can use asyncio primitives (queues, events, etc.)
    without cross-loop coordination.
    """
    parser = argparse.ArgumentParser(
        description="Ringmaster AI task orchestrator — HTTP API server."
    )
    parser.add_argument(
        "-c",
        "--config",
        default="ringmaster.yaml",
        metavar="PATH",
        help="Path to the ringmaster.yaml config file (default: ringmaster.yaml).",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()

    # Configure logging before anything else so that errors in create_app()
    # are captured.  The format includes a timestamp so log files are useful
    # without external timestamps (e.g. journald already adds its own).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-40s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    async def run() -> None:
        """Build all components and run the server until shutdown."""
        # create_app() is async because it may perform async I/O in the future
        # (e.g. async DB migrations).  It also wires singletons into deps so
        # that get_scheduler() and get_db_conn() are ready before we call them.
        app, _auth_manager = await create_app(config_path)

        # Read config from deps after create_app() has wired everything.
        config = deps.get_config()

        ollama = OllamaClient(base_url=config.ollama.host)
        inhibitor = SleepInhibitor()
        scheduler = deps.get_scheduler()

        # Worker gets its own dedicated DB connection — not shared with
        # HTTP handlers.  This avoids thread-safety issues since the worker
        # runs as an async task while handlers run in uvicorn's threadpool.
        from ringmaster import db as db_ops
        db_path = config_path.parent / "ringmaster.db"
        worker_conn = db_ops.get_db(str(db_path))

        worker = Worker(
            conn=worker_conn,
            scheduler=scheduler,
            ollama=ollama,
            inhibitor=inhibitor,
            deliver_webhook=deliver_webhook,
        )

        # Start the worker loop as a background task so it runs concurrently
        # with the uvicorn HTTP server on the same event loop.
        worker_task = asyncio.create_task(worker_loop(worker), name="worker-loop")

        uv_config = uvicorn.Config(
            app=app,
            host=config.server.host,
            port=config.server.port,
            log_level="info",
            # loop="none" tells uvicorn not to install its own event loop
            # policy — we already have one from asyncio.run().
            loop="none",
        )
        server = uvicorn.Server(uv_config)

        logger.info(
            "Starting Ringmaster on %s:%d (config: %s)",
            config.server.host,
            config.server.port,
            config_path,
        )

        try:
            await server.serve()
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

            inhibitor.release()
            await ollama.close()
            worker_conn.close()

            logger.info("Ringmaster shut down cleanly.")

    asyncio.run(run())


if __name__ == "__main__":
    main()
