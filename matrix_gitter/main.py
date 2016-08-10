if __name__ == '__main__':
    try:
        from matrix_gitter.main import main
    except ImportError:
        import os
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from matrix_gitter.main import main
    main()


import json
import sys
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted import logger
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET, Site
from urllib import quote

from matrix_gitter.utils import StringProducer


logger.globalLogPublisher.addObserver(
    logger.FileLogObserver(sys.stderr, logger.formatEventAsClassicLogText))


HOMESERVER_URL = 'http://10.4.0.1:8440/'
HOMESERVER_TOKEN = 'yOBsbMzpRXQOD+7KF9yTGzlJbgxK2z+Nmq0E082C'


log = logger.Logger()

agent = Agent(reactor)


class Transaction(Resource):
    isLeaf = True

    def render_PUT(self, request):
        if len(request.postpath) == 1:
            transaction, = request.postpath
        else:
            raise NoResource

        events = json.load(request.content)['events']
        log.info("Got {nb} events", nb=len(events))
        for event in events:
            log.info(user=event['user_id'], room=event['room_id'])
            log.info(event_type=event['type'])
            log.info(content=event['content'])
        request.responseHeaders.addRawHeader(b"content-type",
                                             b"application/json")
        return '{}'


class Alias(Resource):
    isLeaf = True

    def _err(self, err):
        log.error("Error creating room: {err}", err=str(err))
        return None

    def _end(self, request):
        log.info("callback done")
        request.responseHeaders.addRawHeader(b"content-type",
                                             b"application/json")
        request.write('{}')
        request.finish()

    def render_GET(self, request):
        if len(request.postpath) == 1:
            alias, = request.postpath
        else:
            raise NoResource

        log.info("Requested room {room}", room=alias)
        alias_localpart = alias.split(':', 1)[0][1:]
        d = agent.request(
            'POST',
            '%s_matrix/client/r0/createRoom?access_token=%s' % (
                HOMESERVER_URL,
                quote(HOMESERVER_TOKEN)),
            Headers({'content-type': ['application/json']}),
            StringProducer(json.dumps({'room_alias_name': alias_localpart})))
        d.addErrback(self._err)
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


def main():
    root = Resource()
    root.putChild("transaction", Transaction())
    root.putChild("rooms", Alias())
    factory = Site(root)
    factory.logRequest = True
    reactor.listenTCP(8445, factory)
    reactor.run()
