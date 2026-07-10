from dataclasses import dataclass

from sqlalchemy import Select

from media_pilot.repository.models import DownloadTask, IngestTask


@dataclass(frozen=True)
class TaskAccessScope:
    user_id: str
    can_view_all_tasks: bool
    can_access_adult: bool


def restrict_ingest_tasks(statement: Select, scope: TaskAccessScope) -> Select:
    if not scope.can_view_all_tasks:
        statement = statement.where(IngestTask.owner_user_id == scope.user_id)
    if not scope.can_access_adult:
        statement = statement.where(IngestTask.is_adult.is_(False))
    return statement


def restrict_download_tasks(statement: Select, scope: TaskAccessScope) -> Select:
    if not scope.can_view_all_tasks:
        statement = statement.where(DownloadTask.owner_user_id == scope.user_id)
    if not scope.can_access_adult:
        statement = statement.where(DownloadTask.is_adult.is_(False))
    return statement
