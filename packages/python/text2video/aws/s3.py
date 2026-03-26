from urllib.parse import urlparse

from botocore.config import Config

from text2video.aws.session import build_boto3_session
from text2video.config import Settings


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = build_boto3_session(settings)
        self.client = self.session.client(
            "s3",
            config=Config(signature_version="s3v4"),
        )

    def make_key(self, project_id: str, prefix: str, filename: str) -> str:
        return f"{prefix}/{project_id}/{filename}"

    def create_presigned_upload(self, key: str, expires_in: int = 3600) -> dict[str, str]:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return {"bucket": self.settings.s3_bucket, "key": key, "url": url}

    def create_presigned_download(self, key: str, expires_in: int = 3600) -> dict[str, str]:
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return {"bucket": self.settings.s3_bucket, "key": key, "url": url}

    def upload_file(self, source_path: str, key: str) -> str:
        self.client.upload_file(source_path, self.settings.s3_bucket, key)
        return f"s3://{self.settings.s3_bucket}/{key}"

    def download_file(self, key: str, target_path: str) -> str:
        self.client.download_file(self.settings.s3_bucket, key, target_path)
        return target_path

    @staticmethod
    def parse_s3_uri(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError("Expected an s3:// URI")
        return parsed.netloc, parsed.path.lstrip("/")
