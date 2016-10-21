import json
from StringIO import StringIO
import time
from twisted.web.iweb import IBodyProducer
from twisted.internet import defer, reactor
from twisted.internet.protocol import connectionDone, Protocol
from twisted import logger
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
import urllib
from zope.interface import implements


agent = Agent(reactor)


def request(method, uri, headers, bodyProducer=None, timeout=20):
    d = agent.request(method, uri,
                      Headers(dict((k, [v]) for k, v in headers.iteritems())),
                      bodyProducer)

    if timeout is not None:
        # http://stackoverflow.com/a/15142570/711380
        timeoutCall = reactor.callLater(timeout, d.cancel)

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


class RateLimiter(object):
    """Limits the rate at which an operation happens.

    Also provides exponential backoff when operation fails.
    """
    def __init__(self, operation_name,
                 min=10, max=30 * 60,
                 failed_mult=1.8, success_mult=0.8):
        """New rate limiter for a specific operation.

        Instead of running the operation directly, call this object's
        `schedule()` method that will enqueue it and call it when possible.

        If that succeeds, call `success()` to reduce the backoff. Else call
        `fail()` to increase backoff.

        :param str operation_name: A name for that operation, used in log
            messages (for example, "api_request").
        :param float min: Minimum delay between operations.
        :param float max: Maximum delay between operations; we will not
            increase the delay past that value when backing off.
        :param float failed_mult: How much to increase the delay when an
            operation fails (2 would wait twice as long each time).
        :param float success_mult: How much to change the delay when an
            operation succeeds (0.5 would wait half as long, 0 would return to
            the minimum delay immediately).
        """
        self.logger = logger.Logger('%s.RateLimiter.%s' % (__name__,
                                                           operation_name))
        self.min = min
        self.max = max
        self.failed_mult = failed_mult
        self.success_mult = success_mult

        self.delay = self.min
        self.last_scheduled = 0
        self.queue = []

    def fail(self):
        old_delay = self.delay
        self.delay = min(self.delay * self.failed_mult, self.max)
        self.logger.info("Failed, backing off (delay: {old:.1f} -> {new:.1f})",
                         old=old_delay, new=self.delay)

    def success(self):
        self.delay = max(self.delay * self.success_mult, self.min)
        self.logger.debug("Success, delay: {delay:.1f}", delay=self.delay)

    def schedule(self, function, *args, **kwargs):
        if not self.queue:
            now = time.time()
            next_schedule = self.last_scheduled + self.delay
            wait = max(0, next_schedule - now)

            reactor.callLater(wait, self._do_schedule)
        self.queue.append((function, args, kwargs))

    def _do_schedule(self):
        if not self.queue:
            return

        now = time.time()

        function, args, kwargs = self.queue.pop(0)
        reactor.callLater(0, function, *args, **kwargs)
        self.last_scheduled = now

        if self.queue:
            reactor.callLater(self.delay, self._do_schedule)
