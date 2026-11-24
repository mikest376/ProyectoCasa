"""Punto de entrada: python -m medallion_worker."""

import signal
import threading
from types import FrameType

from medallion_worker import json_logging
from medallion_worker.config import Settings
from medallion_worker.consumer import run
from medallion_worker.sinks import LogSink


def main() -> None:
    settings = Settings()
    json_logging.configure(settings.log_level)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(settings, LogSink(), stop)


if __name__ == "__main__":
    main()
