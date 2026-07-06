import shutil
from pathlib import Path
from text2video.config import Settings


class LocalAssetStore:
    def __init__(self, settings: Settings, base_url: str = "http://localhost:8000/assets"):
        self.settings = settings
        self.base_dir = Path(settings.runtime_root)
        self.base_url = base_url.rstrip("/")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def upload_file(self, source_path: str, key: str) -> str:
        target_path = self.base_dir / key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

        return f"{self.base_url}/{key}"

    def create_presigned_download(self, key: str, expires_in: int = 3600) -> dict[str, str]:
        # Local storage doesn't really "presign" in this simple mock, just returns the URL
        return {"url": f"{self.base_url}/{key}"}

    def create_presigned_upload(self, key: str, expires_in: int = 3600) -> dict[str, str]:
        # For simplicity, we just point to the URL where a PUT might be handled or managed
        return {"url": f"{self.base_url}/{key}"}
