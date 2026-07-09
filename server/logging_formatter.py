import logging


class SurimiFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        time_str = self.formatTime(record, '%H:%M:%S')
        if record.levelno == logging.INFO:
            return f"[{time_str}] {record.getMessage()}"
        return f"[{time_str}] [{record.levelname}] {record.getMessage()}"


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SurimiFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(handler)
