import hashlib
import hmac
import random
import string
from twisted.internet import reactor
from twisted import logger
from twisted.web.client import Agent
from twisted.web.resource import NoResource, Resource
from twisted.web.server import NOT_DONE_YET, Site
import urllib

from matrix_gitter.utils import read_form_response


log = logger.Logger()

agent = Agent(reactor)


secret_key = ''.join(random.choice(string.ascii_uppercase +
                                   string.ascii_lowercase +
                                   string.digits)
                     for _ in range(32))


def secret_hmac(msg):
    return hmac.new(secret_key, msg, hashlib.sha1).hexdigest()


class Redirect(Resource):
    isLeaf = True

    def __init__(self, api):
        self.api = api
        Resource.__init__(self)

    def render_GET(self, request):
        if len(request.postpath) == 1:
            user, = request.postpath
        else:
            raise NoResource

        log.info("User {user} starting Gitter authorization", user=user)
        state = '%s|%s' % (user, secret_hmac(user))
        getargs = {
            'client_id': self.api.oauth_key,
            'response_type': 'code',
            'redirect_uri': '%scallback' % self.api.url,
            'state': state}
        request.redirect(
            b'https://gitter.im/login/oauth/authorize?%s' % (
                urllib.urlencode(getargs)))
        request.finish()
        return NOT_DONE_YET


class Callback(Resource):
    isLeaf = True

    def __init__(self, api):
        self.api = api
        Resource.__init__(self)

    def render_GET(self, request):
        user, secret = request.args['state'].split('|')
        log.info("Gitter authorization callback for user {user}", user=user)
        if secret_hmac(user) != secret:
            raise ValueError("Invalid state")
        code = request.args.get('code')
        log.info("Requesting access_token")
        getargs = {
            'client_id': self.api.client_id,
            'client_secret': self.api.oauth_secret,
            'code': code,
            'redirect_uri': '%scallback' % self.api.url,
            'grant_type': 'authorization_code'}
        d = agent.request(
            'POST',
            'https://gitter.im/login/oauth/token?%s' %
            urllib.urlencode(getargs))
        d.addCallback(read_form_response)
        d.addCallback(self.authorized, user)
        return ''

    def authorized(self, (response, content), user):
        log.info("Got response from Gitter")
        access_token, = content['access_token']
        token_type, = content['token_type']
        if token_type.lower() != 'bearer':
            raise ValueError("Got invalid token type %r" % token_type)
        self.api.set_access_token(user, access_token)


def setup_gitter_oauth(api, port):
    root = Resource()
    root.putChild('auth_gitter', Redirect(api))
    root.putChild('callback', Callback(api))
    site = Site(root)
    site.logRequest = True
    reactor.listenTCP(port, site)
