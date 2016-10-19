import json
from StringIO import StringIO
from twisted.web.iweb import IBodyProducer
from twisted.internet import defer, reactor
from twisted.internet.protocol import connectionDone, Protocol
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
import urllib
from zope.interface import implements


agent = Agent(reactor)


def request(method, uri, headers, bodyProducer=None):
    d = agent.request(method, uri,
                      Headers(dict((k, [v]) for k, v in headers.iteritems())),
                      bodyProducer)

    # http://stackoverflow.com/a/15142570/711380
    timeoutCall = reactor.callLater(20, d.cancel)

    def completed(passthrough):
        if timeoutCall.active():
            timeoutCall.cancel()
        return passthrough
    d.addBoth(completed)

    return d


def Errback(log, fmt, **kwargs):
    """Convenience class for logging an error on a Deferred.
    """
    def errback_func(err):
        log.failure(fmt, err, **kwargs)
    return errback_func


class StringProducer(object):
    """A producer that simply sends a given string.
    """
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
    """A producer that sends the JSON representation of a given value.
    """
    def __init__(self, body):
        StringProducer.__init__(self, json.dumps(body))


class FormProducer(StringProducer):
    """A producer that sends a dictionary in form-encoded representation.
    """
    def __init__(self, body):
        StringProducer.__init__(self, urllib.urlencode(body))


class StringReceiver(Protocol):
    """A receiver that buffers up data and emits it on a Deferred when done.
    """
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
    """Convenience function to read a JSON response.
    """
    d = defer.Deferred()
    response.deliverBody(StringReceiver(response, d))
    d.addCallback(lambda s: (response, json.loads(s)))
    return d


def _assert_fail(content, response):
    raise IOError("HTTP %d: %s" % (response.code, content))


def assert_http_200(response):
    """Convenience function that errors out if the HTTP status is not 200.

    This is useful because an HTTP request is considered successful if a
    response is received, even if that response represents an error.
    """
    if response.code != 200:
        d = defer.Deferred()
        response.deliverBody(StringReceiver(response, d))
        d.addCallback(_assert_fail, response)
        return d
    else:
        return response
