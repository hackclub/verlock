"""Credential-free timings for the launch critical path."""
from contextlib import contextmanager
import logging
from time import monotonic

logger = logging.getLogger(__name__)


@contextmanager
def launch_stage(stage, launch_id):
    started = monotonic()
    outcome = "failed"
    try:
        yield
        outcome = "ok"
    finally:
        logger.info("launch_timing id=%s stage=%s seconds=%.3f outcome=%s",
                    launch_id, stage, monotonic() - started, outcome)
