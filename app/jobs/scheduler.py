# app/jobs/scheduler.py
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.followup_service import executar_jobs_pendentes

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            executar_jobs_pendentes,
            trigger="interval",
            seconds=60,
            id="followup_runner",
            replace_existing=True,
        )
    return _scheduler
