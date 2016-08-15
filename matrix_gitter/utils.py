import json
from StringIO import StringIO
from twisted.web.iweb import IBodyProducer
from twisted.internet import defer
from twisted.internet.protocol import connectionDone, Protocol
import urllib
import urlparse
from zope.interface import implements


def Errback(log, fmt, **kwargs):
    def errback_func(err):
        log.failure(fmt, err, **kwargs)
    return errback_func


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
    def __init__(self, response, finished, max_size=2 * 1024 * 1024):
        self.response = response
        self.finished = finished
        self.remaining = max_size
        self.content = StringIO()

    def dataReceived(self, bytes):
        if self.remaining:
            if len(bytes) > self.remaining:
                self.finished.errback(RuntimeError("response is too big"))
                self.response.close()
                return
            self.content.write(bytes)
            self.remaining -= len(bytes)

    def connectionLost(self, reason=connectionDone):
        if not self.finished.called:
            self.finished.callback(self.content.getvalue())


def read_json_response(response):
    d = defer.Deferred()
    response.deliverBody(StringReceiver(response, d))
    d.addCallback(lambda s: (response, json.loads(s)))
    return d


def read_form_response(response):
    d = defer.Deferred()
    response.deliverBody(StringReceiver(response, d))
    d.addCallback(lambda s: (response, urlparse.parse_qs(s)))
    return d


def _assert_fail(content, response):
    raise IOError("HTTP %d: %s" % (response.code, content))


def assert_http_200(response):
    if response.code != 200:
        d = defer.Deferred()
        response.deliverBody(StringReceiver(response, d))
        d.addCallback(_assert_fail, response)
        return d
    else:
        return response
