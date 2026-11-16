import logging
from logging.handlers import RotatingFileHandler


def configure_logging(directory, callback=None):
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("discourse_epub_archive")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(directory / "app.log", maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    if callback:
        class CallbackHandler(logging.Handler):
            def emit(self, record):
                callback(self.format(record))
        handler = CallbackHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
    return logger
