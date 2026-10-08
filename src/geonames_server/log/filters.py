import logging


class OnlyErrorsFilter(logging.Filter):
    def filter(self, record):

        return record.levelno >= logging.ERROR
