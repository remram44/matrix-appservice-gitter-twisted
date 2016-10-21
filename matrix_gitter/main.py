import os
import platform
import sys
from twisted.internet import reactor
from twisted import logger


def main():
    # Log to stderr
    observer = logger.FileLogObserver(sys.stderr,
                                      logger.formatEventAsClassicLogText)
    # Filter events by level
    predicate = logger.LogLevelFilterPredicate(logger.LogLevel.warn)
    predicate.setLogLevelForNamespace('matrix_gitter', logger.LogLevel.info)
    observer = logger.FilteringLogObserver(observer, [predicate])
    # Register as global observer
    logger.globalLogPublisher.addObserver(observer)

    if (platform.system().lower() == 'darwin' and
            not os.environ.get('SSL_CERT_FILE')):
        sys.stderr.write(
            "==========\n"
            "On Mac OS, you might run into OpenSSL bugs.\n"
            "Running `export SSL_CERT_FILE=$(python -m certifi)` helps.\n"
            "==========\n\n")

    from matrix_gitter.bridge import Bridge

    config = {}
    execfile('settings.py', config, config)
    Bridge(config)

    reactor.run()
