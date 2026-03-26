from __future__ import annotations

import sys

from botocore.exceptions import ClientError

ROOT_NOTE = "Bootstrapping S3 bucket and DynamoDB tables"

sys.path.insert(0, "packages/python")

from text2video.aws.session import build_boto3_session
from text2video.config import get_settings


def ensure_bucket(settings) -> None:
    if not settings.s3_bucket:
        print("Skipping S3 bucket creation because S3_BUCKET is empty.")
        return

    session = build_boto3_session(settings)
    s3 = session.client("s3")
    try:
        if settings.aws_default_region == "us-east-1":
            s3.create_bucket(Bucket=settings.s3_bucket)
        else:
            s3.create_bucket(
                Bucket=settings.s3_bucket,
                CreateBucketConfiguration={"LocationConstraint": settings.aws_default_region},
            )
        print(f"Created bucket {settings.s3_bucket}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            print(f"Bucket {settings.s3_bucket} already exists")
            return
        raise


def ensure_table(dynamodb, table_name: str, attribute_definitions: list, key_schema: list) -> None:
    existing = dynamodb.list_tables()["TableNames"]
    if table_name in existing:
        print(f"Table {table_name} already exists")
        return

    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=attribute_definitions,
        KeySchema=key_schema,
        BillingMode="PAY_PER_REQUEST",
    )
    print(f"Creating table {table_name}")
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print(f"Table {table_name} is active")


def main() -> None:
    settings = get_settings()
    print(ROOT_NOTE)
    session = build_boto3_session(settings)
    dynamodb = session.client("dynamodb")

    ensure_bucket(settings)

    base_attributes = [
        {"AttributeName": "pk", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"},
    ]
    base_schema = [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]

    ensure_table(dynamodb, settings.dynamodb_projects_table, base_attributes, base_schema)
    ensure_table(dynamodb, settings.dynamodb_jobs_table, base_attributes, base_schema)
    ensure_table(dynamodb, settings.dynamodb_outputs_table, base_attributes, base_schema)
    ensure_table(dynamodb, settings.dynamodb_continuity_table, base_attributes, base_schema)


if __name__ == "__main__":
    main()
