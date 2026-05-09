import threading
from datetime import timedelta

from prescribe.state import StateStore


class LockLostError(RuntimeError):
    pass


class LockHeartbeat:
    def __init__(
        self,
        state_store: StateStore,
        *,
        name: str,
        owner: str,
        token: str | None,
        ttl: timedelta,
        interval: timedelta,
    ) -> None:
        self.state_store = state_store
        self.name = name
        self.owner = owner
        self.token = token
        self.ttl = ttl
        self.interval = interval
        self.lost = False
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LockHeartbeat":
        if self.token is None or self.ttl.total_seconds() <= 0 or self.interval.total_seconds() <= 0:
            return self
        self._thread = threading.Thread(target=self._run, name="prescribe-lock-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval.total_seconds() * 2))

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval.total_seconds()):
                refreshed = self.state_store.heartbeat_lock(
                    self.name,
                    owner=self.owner,
                    token=self.token or "",
                    ttl=self.ttl,
                )
                if refreshed is None:
                    self.lost = True
                    return
        except BaseException as exc:
            self.lost = True
            self.error = exc

    def require_current(self) -> None:
        if self.error is not None:
            raise LockLostError(f"global lock heartbeat failed: {self.error}") from self.error
        if self.lost:
            raise LockLostError("global lock was lost")
        if self.token is None:
            return
        if not self.state_store.lock_is_current(self.name, owner=self.owner, token=self.token):
            self.lost = True
            raise LockLostError("global lock was lost")
