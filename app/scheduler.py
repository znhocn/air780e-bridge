"""Scheduled SMS tasks.

A background thread periodically scans enabled tasks; a task is "due" when:
- it has never run, or
- the last run finished >= interval_days ago, or
- the last run failed and at least 1 day has passed (retry).

Execution inserts an outbound message, queues it on the worker, and marks the
task ``running``; the worker's on_send_result callback settles it to
success/failed by matching last_msg_id.
"""

import datetime
import logging
import threading
import time

log = logging.getLogger("air780e.scheduler")


class SchedulerService(threading.Thread):
    def __init__(self, db, worker, scan_interval: float = 30.0):
        super().__init__(daemon=True, name="sms-scheduler")
        self.db = db
        self.worker = worker
        self.scan_interval = scan_interval
        self._stop_event = threading.Event()

    # ---------- lifecycle ----------

    def stop(self):
        self._stop_event.set()

    def run(self):
        # Tasks stuck in "running" from a previous incarnation: let them be due again.
        self.db.execute("UPDATE scheduled_tasks SET last_status='never' WHERE last_status='running'")
        while not self._stop_event.is_set():
            try:
                self._scan()
            except Exception:
                log.exception("Scheduler scan failed")
            self._stop_event.wait(self.scan_interval)

    # ---------- scheduling ----------

    def _scan(self):
        for task in self.db.rows(
            "SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY id"
        ):
            if self._due(task):
                self.execute(task)

    def _due(self, task: dict) -> bool:
        if not task.get("last_run_at"):
            return True
        try:
            last = datetime.datetime.strptime(task["last_run_at"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return True
        days = (datetime.datetime.now() - last).total_seconds() / 86400
        if task.get("last_status") == "failed":
            return days >= 1.0
        return days >= float(task.get("interval_days") or 0)

    def execute(self, task: dict):
        mid = self.db.execute(
            "INSERT INTO messages (direction, receiver, content, status, raw) "
            "VALUES ('out', ?, ?, 'queued', '')",
            (task["phone"], task["content"]),
        )
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            "UPDATE scheduled_tasks SET last_run_at=?, last_status='running', last_msg_id=? WHERE id=?",
            (now, mid, task["id"]),
        )
        log.info("Executing scheduled task #%s (%s) -> %s (msg #%s)", task["id"], task.get("name"), task["phone"], mid)
        self.worker.send(mid, task["phone"], task["content"])

    def trigger(self, task_id: int) -> bool:
        """Run a task immediately (on manual trigger)."""
        task = self.db.row("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,))
        if not task:
            return False
        self.execute(task)
        return True

    def on_send_result(self, message_id: int, ok: bool):
        status = "success" if ok else "failed"
        self.db.execute(
            "UPDATE scheduled_tasks SET last_status=? WHERE last_msg_id=?",
            (status, message_id),
        )
        log.info("Scheduled task resolved (msg #%s): %s", message_id, status)