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
            if job["job_type"] != "stitch_segments":
                if self.settings.runpod_inference_base_url:
                    self.queue.mark_complete(
                        job_id,
                        self.settings.worker_id,
                        {
                            "output_type": result.output_type,
                            "s3_key": result.s3_key,
                            "backend": result.backend,
                            "notes": result.notes,
                        },
                    )
                    return
                raise RuntimeError(
                    f"{adapter.name} adapter validated the payload, but local model execution is disabled until a real GPU worker is attached."
                )

            self.queue.mark_complete(
                job_id,
                self.settings.worker_id,
                {
                    "output_type": result.output_type,
                    "s3_key": result.s3_key,
                    "backend": result.backend,
                    "manifest_ref": result.manifest_ref,
                    "notes": result.notes,
                },
            )
        except Exception as exc:  # pragma: no cover - scaffold safety
            self.queue.mark_failed(job_id, self.settings.worker_id, str(exc))
