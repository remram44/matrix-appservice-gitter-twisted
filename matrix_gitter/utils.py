import json
from StringIO import StringIO
from twisted.web.iweb import IBodyProducer
from twisted.internet import defer
from twisted.internet.protocol import connectionDone, Protocol
import urllib
import urlparse
from zope.interface import implements


class StringProducer(object):
    implements(IBodyProducer)

    def __init__(self, body):
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        consumer.write(self.body)
        return defer.succeed(None)

    def pauseProducing(self):
        pass

    def stopProducing(self):
        pass


class JsonProducer(StringProducer):
    def __init__(self, body):
        StringProducer.__init__(self, json.dumps(body))


class FormProducer(StringProducer):
    def __init__(self, body):
        StringProducer.__init__(self, urllib.urlencode(body))


class StringReceiver(Protocol):
    def __init__(self, finished, max_size=1024 * 10):
        self.finished = finished
        self.remaining = max_size
        self.content = StringIO()

    def dataReceived(self, bytes):
        if self.remaining:
            if len(bytes) > self.remaining:
                self.finished.errback(RuntimeError("response is too big"))
                return
            self.content.write(bytes)
            self.remaining -= len(bytes)

    def connectionLost(self, reason=connectionDone):
        self.finished.callback(self.content.getvalue())


def read_json_response(response):
    d = defer.Deferred()
    response.deliverBody(StringReceiver(d))
    d.addCallback(lambda s: (response, json.loads(s)))
    return d


def read_form_response(response):
    d = defer.Deferred()
    response.deliverBody(StringReceiver(d))
    d.addCallback(lambda s: (response, urlparse.parse_qs(s)))
    return d
