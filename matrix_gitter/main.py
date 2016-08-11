import sys
from twisted.internet import reactor
from twisted import logger


def main():
    logger.globalLogPublisher.addObserver(
        logger.FileLogObserver(sys.stderr, logger.formatEventAsClassicLogText))

    from matrix_gitter.matrix import MatrixAPI

    matrix = MatrixAPI(8445, 'http://10.4.0.1:8440/',
                       'yOBsbMzpRXQOD+7KF9yTGzlJbgxK2z+Nmq0E082C',
                       'n/xRrUOvzFWskYrSyeKVUlsvQ5I2/CuRMB8XtMll')

    reactor.run()
