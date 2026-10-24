from sqlalchemy import func, select

from ash_agent.api.dependencies import SessionDep
from ash_agent.persistence.models import RepairLesson, Run


async def read_metrics(session: SessionDep) -> dict:
    total = await session.scalar(select(func.count()).select_from(Run)) or 0
    rows = await session.execute(select(Run.status, func.count()).group_by(Run.status))
    by_status = {status: count for status, count in rows}
    published = (
        await session.scalar(select(func.count()).select_from(Run).where(Run.pr_url.is_not(None)))
        or 0
    )
    lessons = await session.scalar(select(func.count()).select_from(RepairLesson)) or 0
    average_attempts = await session.scalar(select(func.avg(Run.attempt)))

    return {
        "runs": {
            "total": total,
            "by_status": by_status,
            "average_attempts": round(float(average_attempts or 0), 2),
        },
        "pull_requests": {"published": published},
        "repair_lessons": lessons,
    }
