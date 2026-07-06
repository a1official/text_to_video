from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from text2video.aws.session import build_boto3_session
from text2video.config import Settings


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DynamoProjectStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resource = build_boto3_session(settings).resource("dynamodb")
        self.table = self.resource.Table(settings.dynamodb_projects_table)
        self.outputs_table = self.resource.Table(settings.dynamodb_outputs_table)

    def create_project(self, title: str, created_by: str, style_profile: str | None) -> dict[str, str]:
        project_id = str(uuid4())
        now = utc_now_iso()
        item = {
            "pk": f"PROJECT#{project_id}",
            "sk": "META",
            "project_id": project_id,
            "title": title,
            "created_by": created_by,
            "style_profile": style_profile or "",
            "status": "created",
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return {"project_id": project_id}

    def get_project(self, project_id: str) -> dict | None:
        response = self.table.get_item(Key={"pk": f"PROJECT#{project_id}", "sk": "META"})
        return response.get("Item")

    def list_project_items(self, project_id: str) -> list[dict]:
        response = self.table.query(KeyConditionExpression=Key("pk").eq(f"PROJECT#{project_id}"))
        return response.get("Items", [])

    def save_plan(self, project_id: str, summary: str, continuity: list[str], shots: list[dict]) -> dict:
        project = self.get_project(project_id)
        if not project:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": f"Project {project_id} was not found",
                    }
                },
                "GetItem",
            )

        now = utc_now_iso()
        self.table.update_item(
            Key={"pk": f"PROJECT#{project_id}", "sk": "META"},
            UpdateExpression=(
                "SET #status = :status, plan_summary = :summary, continuity = :continuity, "
                "shot_count = :shot_count, updated_at = :updated_at"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "planned",
                ":summary": summary,
                ":continuity": continuity,
                ":shot_count": len(shots),
                ":updated_at": now,
            },
        )

        existing_items = self.list_project_items(project_id)
        existing_shot_keys = [
            {"pk": item["pk"], "sk": item["sk"]}
            for item in existing_items
            if item.get("sk", "").startswith("SHOT#")
        ]
        if existing_shot_keys:
            with self.table.batch_writer() as batch:
                for key in existing_shot_keys:
                    batch.delete_item(Key=key)

        with self.table.batch_writer() as batch:
            for index, shot in enumerate(shots, start=1):
                batch.put_item(
                    Item={
                        "pk": f"PROJECT#{project_id}",
                        "sk": f"SHOT#{shot['shot_id']}",
                        "project_id": project_id,
                        "sequence_index": index,
                        "review_status": shot.get("review_status", "pending_review"),
                        "approved_for_render": bool(shot.get("approved_for_render", False)),
                        "edit_prompt": str(shot.get("edit_prompt") or ""),
                        **shot,
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        return {
            "project_id": project_id,
            "summary": summary,
            "continuity": continuity,
            "shots": shots,
        }

    def update_project_metadata(self, project_id: str, fields: dict[str, Any]) -> dict:
        project = self.get_project(project_id)
        if not project:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": f"Project {project_id} was not found",
                    }
                },
                "GetItem",
            )

        if not fields:
            return project

        now = utc_now_iso()
        expression_attribute_names: dict[str, str] = {}
        expression_attribute_values: dict[str, Any] = {":updated_at": now}
        assignments = ["updated_at = :updated_at"]

        for index, (key, value) in enumerate(fields.items()):
            name_key = f"#field_{index}"
            value_key = f":value_{index}"
            expression_attribute_names[name_key] = key
            expression_attribute_values[value_key] = value
            assignments.append(f"{name_key} = {value_key}")

        response = self.table.update_item(
            Key={"pk": f"PROJECT#{project_id}", "sk": "META"},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})

    def set_project_status(self, project_id: str, status: str, **fields: Any) -> dict:
        return self.update_project_metadata(project_id, {"status": status, **fields})

    def list_shots(self, project_id: str) -> list[dict]:
        items = self.list_project_items(project_id)
        shots = [item for item in items if item.get("sk", "").startswith("SHOT#")]
        return sorted(shots, key=lambda item: item.get("sequence_index", 0))

    def get_shot(self, project_id: str, shot_id: str) -> dict | None:
        response = self.table.get_item(Key={"pk": f"PROJECT#{project_id}", "sk": f"SHOT#{shot_id}"})
        return response.get("Item")

    def update_shot_metadata(self, project_id: str, shot_id: str, fields: dict[str, Any]) -> dict:
        shot = self.get_shot(project_id, shot_id)
        if not shot:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": f"Shot {shot_id} was not found for project {project_id}",
                    }
                },
                "GetItem",
            )

        if not fields:
            return shot

        now = utc_now_iso()
        expression_attribute_names: dict[str, str] = {}
        expression_attribute_values: dict[str, Any] = {":updated_at": now}
        assignments = ["updated_at = :updated_at"]

        for index, (key, value) in enumerate(fields.items()):
            name_key = f"#field_{index}"
            value_key = f":value_{index}"
            expression_attribute_names[name_key] = key
            expression_attribute_values[value_key] = value
            assignments.append(f"{name_key} = {value_key}")

        response = self.table.update_item(
            Key={"pk": f"PROJECT#{project_id}", "sk": f"SHOT#{shot_id}"},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})

    def get_plan_context(self, project_id: str) -> dict:
        project = self.get_project(project_id)
        if not project:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": f"Project {project_id} was not found",
                    }
                },
                "GetItem",
            )
        return {
            "project": project,
            "summary": project.get("plan_summary", ""),
            "continuity": project.get("continuity", []),
            "shots": self.list_shots(project_id),
        }

    def save_stitch_manifest(self, project_id: str, manifest: dict) -> dict:
        project = self.get_project(project_id)
        if not project:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": f"Project {project_id} was not found",
                    }
                },
                "GetItem",
            )

        now = utc_now_iso()
        item = {
            "pk": f"PROJECT#{project_id}",
            "sk": f"MANIFEST#{manifest['scene_id']}",
            "project_id": project_id,
            **manifest,
            "created_at": now,
            "updated_at": now,
        }
        self.table.put_item(Item=item)
        return item

    def list_manifests(self, project_id: str) -> list[dict]:
        items = self.list_project_items(project_id)
        manifests = [item for item in items if item.get("sk", "").startswith("MANIFEST#")]
        return sorted(manifests, key=lambda item: item.get("created_at", ""))

    def save_output(self, project_id: str, shot_id: str, job_id: str, output: dict) -> dict:
        now = utc_now_iso()
        output_id = str(uuid4())
        item = {
            "pk": f"PROJECT#{project_id}",
            "sk": f"OUTPUT#{output_id}",
            "output_id": output_id,
            "project_id": project_id,
            "shot_id": shot_id,
            "job_id": job_id,
            **output,
            "created_at": now,
            "updated_at": now,
        }
        self.outputs_table.put_item(Item=item)
        return item

    def list_outputs(self, project_id: str) -> list[dict]:
        response = self.outputs_table.query(KeyConditionExpression=Key("pk").eq(f"PROJECT#{project_id}"))
        items = response.get("Items", [])
        return sorted(items, key=lambda item: item.get("created_at", ""))
