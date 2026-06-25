from pathlib import Path

from sqlalchemy.orm import Session

from media_pilot.repository.models import AuditLog, OperationRecord

CHECKSUM_CHUNK_SIZE = 1024 * 1024


def record_file_operation(
    session: Session,
    *,
    task_id: str,
    operation_type: str,
    permission_level: str,
    source_path: Path,
    target_path: Path,
    status: str,
    actor: str,
    file_asset_id: str | None = None,
    error_message: str | None = None,
    extra_details: dict | None = None,
) -> OperationRecord:
    details = {
        "source_checksum": _checksum(source_path),
        "target_checksum": _checksum(target_path),
        "source_size_bytes": _size(source_path),
        "target_size_bytes": _size(target_path),
        "error_message": error_message,
    }
    if extra_details:
        details.update(extra_details)
    operation = OperationRecord(
        task_id=task_id,
        file_asset_id=file_asset_id,
        operation_type=operation_type,
        permission_level=permission_level,
        source_path=str(source_path),
        target_path=str(target_path),
        status=status,
        details=details,
    )
    session.add(operation)
    session.flush()

    session.add(
        AuditLog(
            task_id=task_id,
            actor=actor,
            object_type="operation_record",
            object_id=operation.id,
            action="file_operation_recorded",
            context={
                "operation_type": operation_type,
                "permission_level": permission_level,
                "source_path": str(source_path),
                "target_path": str(target_path),
                "status": status,
                "error_message": error_message,
            },
        )
    )
    session.flush()
    return operation


def record_generated_file_operation(
    session: Session,
    *,
    task_id: str,
    operation_type: str,
    permission_level: str,
    target_path: Path,
    status: str,
    actor: str,
    file_asset_id: str | None = None,
    error_message: str | None = None,
) -> OperationRecord:
    details = {
        "target_checksum": _checksum(target_path),
        "target_size_bytes": _size(target_path),
        "error_message": error_message,
    }
    operation = OperationRecord(
        task_id=task_id,
        file_asset_id=file_asset_id,
        operation_type=operation_type,
        permission_level=permission_level,
        source_path=None,
        target_path=str(target_path),
        status=status,
        details=details,
    )
    session.add(operation)
    session.flush()

    session.add(
        AuditLog(
            task_id=task_id,
            actor=actor,
            object_type="operation_record",
            object_id=operation.id,
            action="file_operation_recorded",
            context={
                "operation_type": operation_type,
                "permission_level": permission_level,
                "source_path": None,
                "target_path": str(target_path),
                "status": status,
                "error_message": error_message,
            },
        )
    )
    session.flush()
    return operation


def _checksum(path: Path) -> str | None:
    if not path.is_file():
        return None

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHECKSUM_CHUNK_SIZE), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _size(path: Path) -> int | None:
    if not path.is_file():
        return None
    return path.stat().st_size
