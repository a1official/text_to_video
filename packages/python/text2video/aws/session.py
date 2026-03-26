import boto3

from text2video.config import Settings


def build_boto3_session(settings: Settings) -> boto3.Session:
    kwargs = {"region_name": settings.aws_default_region}

    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            kwargs["aws_session_token"] = settings.aws_session_token

    return boto3.Session(**kwargs)
