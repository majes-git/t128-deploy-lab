"""Basic logging."""
import logging
import sys

FORMAT = '%(levelname)s: %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def set_debug():
    """If 'debug: True' is configured raise loglevel to DEBUG."""
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        format=FORMAT,
        level=logging.DEBUG,
        datefmt=DATE_FORMAT)

def format_msg(*msg):
    """Concatenate all log messages."""
    return ' '.join([str(s) for s in [*msg]])

def debug(*msg):
    logging.debug(format_msg(*msg))

def error(*msg):
    logging.error(format_msg(*msg))
    sys.exit(1)

def exception(msg):
    logging.exception(msg)

def info(*msg):
    logging.info(format_msg(*msg))

def warning(*msg):
    logging.warning(format_msg(*msg))


logging.basicConfig(
    format=FORMAT,
    level=logging.INFO,
    datefmt=DATE_FORMAT)
