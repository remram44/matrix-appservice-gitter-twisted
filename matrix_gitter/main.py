import sys
from twisted.internet import reactor
from twisted import logger


def main():
    logger.globalLogPublisher.addObserver(
        logger.FileLogObserver(sys.stderr, logger.formatEventAsClassicLogText))

    from matrix_gitter.bridge import Bridge

    config = {}
    execfile('settings.py', config, config)
    Bridge(config)

    reactor.run()
