from text2video.config import get_settings
from text2video.worker.runner import WorkerRunner


def main() -> None:
    settings = get_settings()
    runner = WorkerRunner(settings)
    runner.run_forever()


if __name__ == "__main__":
    main()
