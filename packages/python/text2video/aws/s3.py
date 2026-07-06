import shutil
from pathlib import Path
from urllib.parse import quote, urlparse

from botocore.config import Config

from text2video.aws.session import build_boto3_session
from text2video.config import Settings, get_runtime_path


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
        try:
            url = self.client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.settings.s3_bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return {"bucket": self.settings.s3_bucket, "key": key, "url": url}
        except Exception:
            return {"bucket": self.settings.s3_bucket, "key": key, "url": self._local_asset_url(key)}

    def create_presigned_download(self, key: str, expires_in: int = 3600) -> dict[str, str]:
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.s3_bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return {"bucket": self.settings.s3_bucket, "key": key, "url": url}
        except Exception:
            return {"bucket": self.settings.s3_bucket, "key": key, "url": self._local_asset_url(key)}

    def upload_file(self, source_path: str, key: str) -> str:
        try:
            self.client.upload_file(source_path, self.settings.s3_bucket, key)
            return f"s3://{self.settings.s3_bucket}/{key}"
        except Exception:
            target_path = self._local_asset_path(key)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if Path(source_path).resolve() != target_path.resolve():
                shutil.copyfile(source_path, target_path)
            return self._local_asset_url(key)

    def download_file(self, key: str, target_path: str) -> str:
        try:
            self.client.download_file(self.settings.s3_bucket, key, target_path)
            return target_path
        except Exception:
            local_source = self._local_asset_path(key)
            if not local_source.exists():
                raise
            resolved_target = Path(target_path)
            resolved_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_source, resolved_target)
            return target_path

    def _local_asset_path(self, key: str) -> Path:
        clean_parts = [part for part in Path(key).parts if part not in {"", ".", ".."}]
        return get_runtime_path(self.settings, *clean_parts)

    def _local_asset_url(self, key: str) -> str:
        encoded_parts = [quote(part) for part in Path(key).parts if part not in {"", ".", ".."}]
        return f"{self.settings.base_public_url.rstrip('/')}/assets/{'/'.join(encoded_parts)}"

    @staticmethod
    def parse_s3_uri(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError("Expected an s3:// URI")
        return parsed.netloc, parsed.path.lstrip("/")
