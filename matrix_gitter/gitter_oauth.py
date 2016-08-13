from twisted.internet import reactor
from twisted import logger
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted.web.resource import NoResource, Resource
from twisted.web.server import NOT_DONE_YET, Site
import urllib

from matrix_gitter.utils import FormProducer, read_json_response


log = logger.Logger()

agent = Agent(reactor)


class Redirect(Resource):
    isLeaf = True

    def __init__(self, api):
        self.api = api
        Resource.__init__(self)

    def render_GET(self, request):
        if len(request.postpath) == 1:
            state, = request.postpath
        else:
            raise NoResource

        user, secret = state.split('|')
        if self.api.secret_hmac(user) != secret:
            log.info("Secret mismatch: {got!r} != {expected!r}",
                     got=secret, expected=self.api.secret_hmac(user))
            raise ValueError("Invalid state")
        log.info("User {user} starting Gitter authorization", user=user)
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
        user, secret = request.args['state'][0].split('|')
        log.info("Gitter authorization callback for user {user}", user=user)
        if self.api.secret_hmac(user) != secret:
            raise ValueError("Invalid state")
        code = request.args.get('code')[0]
        log.info("Requesting access_token")
        postargs = {
            'client_id': self.api.oauth_key,
            'client_secret': self.api.oauth_secret,
            'code': code,
            'redirect_uri': '%scallback' % self.api.url,
            'grant_type': 'authorization_code'}
        d = agent.request(
            'POST',
            'https://gitter.im/login/oauth/token',
            Headers({'content-type': ['application/x-www-form-urlencoded'],
                     'accept': ['application/json']}),
            FormProducer(postargs))
        d.addCallback(read_json_response)
        d.addCallback(self.authorized, user, request)
        d.addErrback(self.error, request)
        return NOT_DONE_YET

    def authorized(self, (response, content), user, request):
        log.info("Got response from Gitter: {code} {content!r}",
                 code=response.code, content=content)
        access_token = content['access_token']
        token_type = content['token_type']
        if token_type.lower() != 'bearer':
            raise ValueError("Got invalid token type %r" % token_type)
        self.api.set_access_token(user, access_token)
        request.setHeader('content-type', 'text/plain')
        request.write("Success!\n")
        request.finish()

    def error(self, err, request):
        log.failure("Error getting access_token", err)
        request.setResponseCode(403)
        request.setHeader('content-type', 'text/plain')
        request.write("Error getting access token :(\n")
        request.finish()


def setup_gitter_oauth(api, port):
    root = Resource()
    root.putChild('auth_gitter', Redirect(api))
    root.putChild('callback', Callback(api))
    site = Site(root)
    site.logRequest = True
    reactor.listenTCP(port, site)
