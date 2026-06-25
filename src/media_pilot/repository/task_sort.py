"""IngestTask 列表排序 helper.

排序真理源集中在这里, 不得在 SQL / 其它 repository / API 层出现第二份
status → priority 映射.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, case

from media_pilot.repository.models import IngestTask

# 状态 → priority 映射. 数值越小越靠前.
# 1: 需要用户处理
# 2: 进行中
# 3: 失败
# 4: 终态
# 5: 其它 (兜底)
TASK_STATUS_PRIORITY: dict[str, int] = {
    "waiting_user": 1,
    "agent_running": 2,
    "processing": 2,
    "queued": 2,
    "downloading": 2,
    "awaiting_sync": 2,
    "waiting_stable": 2,
    "agent_failed": 3,
    "failed": 3,
    "sync_failed": 3,
    "library_import_complete": 4,
    "completed": 4,
}

_FALLBACK_PRIORITY = 5


def status_priority_expr() -> ColumnElement[int]:
    """把 IngestTask.status 映射成 priority 整数的 SQL 表达式.

    同一 IngestTask.status 列在 SQL ORDER BY 中只出现一次, 不得在
    repository / API 层重复出现 status 字符串映射.
    """

    whens = {
        status: priority
        for status, priority in TASK_STATUS_PRIORITY.items()
    }
    return case(whens, value=IngestTask.status, else_=_FALLBACK_PRIORITY)


def build_task_list_order_by() -> list[ColumnElement]:
    """构造 IngestTaskRepository.list 的 SQL 排序表达式.

    排序: status priority 升序 → updated_at 倒序 → created_at 倒序 → id 升序 (兜底).
    必须在分页前由 SQL 完成, 不得依赖 Python 后处理.
    """

    return [
        status_priority_expr().asc(),
        IngestTask.updated_at.desc(),
        IngestTask.created_at.desc(),
        IngestTask.id.asc(),
    ]
