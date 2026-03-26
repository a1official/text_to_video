from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from botocore.exceptions import ClientError

from text2video.aws.session import build_boto3_session
from text2video.config import Settings


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class DynamoJobQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resource = build_boto3_session(settings).resource("dynamodb")
        self.table = self.resource.Table(settings.dynamodb_jobs_table)

    def enqueue(
        self,
        project_id: str,
        shot_id: str | None,
        job_type: str,
        worker_type: str,
        payload: dict,
        priority: int = 100,
    ) -> dict[str, str]:
        job_id = str(uuid4())
        now = utc_now()
        item = {
            "pk": f"JOB#{job_id}",
            "sk": "META",
            "job_id": job_id,
            "project_id": project_id,
            "shot_id": shot_id or "",
            "job_type": job_type,
            "worker_type": worker_type,
            "status": "pending",
            "priority": Decimal(priority),
            "payload": payload,
            "lease_owner": "",
            "lease_expires_at": "",
            "heartbeat_at": "",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "available_at": now.isoformat(),
        }
        self.table.put_item(Item=item)
        return {"job_id": job_id}

    def get_job(self, job_id: str) -> dict | None:
        response = self.table.get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})
        return response.get("Item")

    def list_jobs_for_project(self, project_id: str) -> list[dict]:
        response = self.table.scan(
            FilterExpression="project_id = :project_id",
            ExpressionAttributeValues={":project_id": project_id},
        )
        items = response.get("Items", [])
        return sorted(items, key=lambda item: item.get("created_at", ""))

    def list_pending(self, worker_type: str, limit: int = 20) -> list[dict]:
        response = self.table.scan(
            FilterExpression="#status = :pending AND (#worker = :worker OR #worker = :general)",
            ExpressionAttributeNames={"#status": "status", "#worker": "worker_type"},
            ExpressionAttributeValues={":pending": "pending", ":worker": worker_type, ":general": "general"},
            Limit=limit,
        )
        items = response.get("Items", [])
        return sorted(items, key=lambda item: (int(item.get("priority", 100)), item.get("created_at", "")))

    def try_acquire(self, job_id: str, worker_id: str) -> bool:
        now = utc_now()
        lease_expires_at = (now + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat()
        try:
            self.table.update_item(
                Key={"pk": f"JOB#{job_id}", "sk": "META"},
                UpdateExpression=(
                    "SET #status = :running, lease_owner = :owner, lease_expires_at = :lease, "
                    "heartbeat_at = :heartbeat, updated_at = :updated"
                ),
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": "pending",
                    ":running": "running",
                    ":owner": worker_id,
                    ":lease": lease_expires_at,
                    ":heartbeat": now.isoformat(),
                    ":updated": now.isoformat(),
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        now = utc_now()
        lease_expires_at = (now + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat()
        self.table.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": "META"},
            UpdateExpression="SET heartbeat_at = :heartbeat, lease_expires_at = :lease, updated_at = :updated",
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeValues={
                ":owner": worker_id,
                ":heartbeat": now.isoformat(),
                ":lease": lease_expires_at,
                ":updated": now.isoformat(),
            },
        )

    def mark_complete(self, job_id: str, worker_id: str, result: dict) -> None:
        self.table.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": "META"},
            UpdateExpression=(
                "SET #status = :done, #result = :result, completed_at = :completed_at, updated_at = :updated "
                "REMOVE lease_owner, lease_expires_at"
            ),
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeNames={"#status": "status", "#result": "result"},
            ExpressionAttributeValues={
                ":owner": worker_id,
                ":done": "completed",
                ":result": result,
                ":completed_at": utc_now_iso(),
                ":updated": utc_now_iso(),
            },
        )

    def mark_failed(self, job_id: str, worker_id: str, error_message: str) -> None:
        self.table.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": "META"},
            UpdateExpression=(
                "SET #status = :failed, error_message = :error, completed_at = :completed_at, updated_at = :updated "
                "REMOVE lease_owner, lease_expires_at"
            ),
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":owner": worker_id,
                ":failed": "failed",
                ":error": error_message,
                ":completed_at": utc_now_iso(),
                ":updated": utc_now_iso(),
            },
        )

    def force_start(self, job_id: str, worker_id: str) -> dict:
        now = utc_now()
        lease_expires_at = (now + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat()
        self.table.update_item(
            Key={"pk": f"JOB#{job_id}", "sk": "META"},
            UpdateExpression=(
                "SET #status = :running, lease_owner = :owner, lease_expires_at = :lease, "
                "heartbeat_at = :heartbeat, updated_at = :updated"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": "running",
                ":owner": worker_id,
                ":lease": lease_expires_at,
                ":heartbeat": now.isoformat(),
                ":updated": now.isoformat(),
            },
        )
        return self.get_job(job_id)
