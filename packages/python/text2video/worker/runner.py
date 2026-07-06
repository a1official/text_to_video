from __future__ import annotations

import time

from text2video.aws.queue import DynamoJobQueue
from text2video.config import Settings
from text2video.worker.adapters import build_adapter_registry


class WorkerRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue = DynamoJobQueue(settings)
        self.adapters = build_adapter_registry()

    def run_forever(self) -> None:
        while True:
            processed = self.run_once()
            if not processed:
                time.sleep(self.settings.worker_poll_interval_sec)

    def run_once(self) -> bool:
        candidates = self.queue.list_pending(worker_type=self.settings.worker_type)
        for job in candidates:
            if not self._job_is_ready(job):
                continue
            job_id = job["job_id"]
            if self.queue.try_acquire(job_id, self.settings.worker_id):
                self._handle_job(job)
                return True
        return False

    def _handle_job(self, job: dict) -> None:
        job_id = job["job_id"]
        try:
            adapter = self.adapters.get(job["job_type"])
            if not adapter:
                result = {
                    "message": f"Stub worker completed {job['job_type']}",
                    "worker_type": self.settings.worker_type,
                }
                self.queue.mark_complete(job_id, self.settings.worker_id, result)
                return

            result = adapter.execute(job)
            completion_result = {
                "output_type": result.output_type,
                "s3_key": result.s3_key,
                "backend": result.backend,
                "duration_sec": result.duration_sec,
                "fps": result.fps,
                "resolution": result.resolution,
                "seed": result.seed,
                "manifest_ref": result.manifest_ref,
                "notes": result.notes,
            }
            self.queue.mark_complete(job_id, self.settings.worker_id, completion_result)
        except Exception as exc:  # pragma: no cover - scaffold safety
            self.queue.mark_failed(job_id, self.settings.worker_id, str(exc))

    def _job_is_ready(self, job: dict) -> bool:
        dependency_ids = [
            str(job.get("payload", {}).get(field) or "").strip()
            for field in ("depends_on_job_id", "preview_job_id", "base_job_id")
        ]
        dependency_ids = [dependency_id for dependency_id in dependency_ids if dependency_id]
        if not dependency_ids:
            return True

        for dependency_id in dependency_ids:
            dependency = self.queue.get_job(dependency_id)
            if not dependency or dependency.get("status") != "completed":
                return False
        return True
