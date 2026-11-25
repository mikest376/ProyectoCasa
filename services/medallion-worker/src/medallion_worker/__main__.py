"""Punto de entrada: python -m medallion_worker."""

import logging
import signal
import sys
import threading
from types import FrameType

from medallion_worker import json_logging
from medallion_worker.config import Settings
from medallion_worker.consumer import run
from medallion_worker.sinks import DeltaSink, storage_options

logger = logging.getLogger(__name__)


def main() -> int:
    settings = Settings()
    json_logging.configure(settings.log_level)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        run(settings, DeltaSink(settings.bronze_uri, storage_options(settings)), stop)
    except Exception:
        # Docker reinicia el contenedor y Kafka vuelve a entregar lo no confirmado.
        logger.exception("el worker se detiene por un error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
