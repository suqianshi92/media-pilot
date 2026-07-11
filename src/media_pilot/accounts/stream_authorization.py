import queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator

_END = object()


def stream_with_periodic_authorization[T](
    source: Iterable[T],
    *,
    authorize: Callable[[], bool],
    authorization_error: T,
    interval_seconds: float = 30.0,
) -> Iterator[T]:
    """转发阻塞式流，并在流无事件时仍按间隔复核权限。"""
    events: queue.Queue[object] = queue.Queue(maxsize=1)

    def produce() -> None:
        try:
            for item in source:
                events.put(item)
        except BaseException as exc:
            events.put(exc)
        finally:
            events.put(_END)

    threading.Thread(target=produce, daemon=True).start()
    next_check = time.monotonic() + interval_seconds

    while True:
        timeout = max(0.0, next_check - time.monotonic())
        try:
            item = events.get(timeout=timeout)
        except queue.Empty:
            if not authorize():
                yield authorization_error
                return
            next_check = time.monotonic() + interval_seconds
            continue

        if item is _END:
            return
        if isinstance(item, BaseException):
            raise item
        if time.monotonic() >= next_check:
            if not authorize():
                yield authorization_error
                return
            next_check = time.monotonic() + interval_seconds
        yield item  # type: ignore[misc]
