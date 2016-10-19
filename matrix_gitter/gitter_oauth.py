from twisted.internet import reactor
from twisted import logger
from twisted.web.resource import NoResource, Resource
from twisted.web.server import NOT_DONE_YET, Site
import urllib

from matrix_gitter.utils import FormProducer, read_json_response, request


log = logger.Logger()


HTML_TEMPLATE = '''\
<html>
  <head><title>{title}</title></head>
  <body>
{content}
  </body>
</html>
'''


class Index(Resource):
    """Index page, showing some info about this system.
    """
    def __init__(self, botname):
        self.botname = botname
        Resource.__init__(self)

    def render_GET(self, request):
        request.setHeader('content-type', 'text/html')
        return HTML_TEMPLATE.format(
            title="Gitter-Matrix bridge",
            content="<h1>Gitter-Matrix bridge</h1>\n"
                    "<p>This server provides a bridge for users of the <a "
                    "href=\"https://matrix.org/\">Matrix chat network</a>, to "
                    "the <a href=\"https://gitter.im/\">Gitter system</a>, "
                    "allowing them to join Gitter rooms without using their "
                    "client.</p>\n"
                    "<h2>How to use this</h2>\n"
                    "<p>Start a private chat with <a href=\""
                    "https://matrix.to/#/%s\"><tt>%s</tt></a>.</p>\n" %
                    (urllib.quote(self.botname), self.botname))


class Redirect(Resource):
    """Starting point for OAuth, redirects to Gitter.
    """
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
            request.setResponseCode(404)
            request.setHeader('content-type', 'text/html')
            return HTML_TEMPLATE.format(
                title="Invalid link",
                content="<p>The link you followed is invalid.</p>\n")
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
    """Page that Gitter redirects users to once they approve the app.
    """
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
        d = request(
            'POST',
            'https://gitter.im/login/oauth/token',
            {'content-type': 'application/x-www-form-urlencoded',
             'accept': 'application/json'},
            FormProducer(postargs))
        d.addCallback(read_json_response)
        d.addCallback(self._authorized, user, request)
        d.addErrback(self.error, request, user)
        return NOT_DONE_YET

    def _authorized(self, (response, content), user, request):
        log.info("Got response from Gitter: {code} {content!r}",
                 code=response.code, content=content)
        access_token = content['access_token']
        token_type = content['token_type']
        if token_type.lower() != 'bearer':
            raise ValueError("Got invalid token type %r" % token_type)
        self.api.set_access_token(user, access_token)
        request.setHeader('content-type', 'text/html')
        request.write(HTML_TEMPLATE.format(
            title="Authentication successful",
            content="<p>You have successfully authenticated. You can go back "
                    "to your Matrix client and start chatting!</p>\n"))
        request.finish()

    def error(self, err, request, user):
        log.failure("Error getting access_token for user {user}", err, user)
        request.setResponseCode(403)
        request.setHeader('content-type', 'text/plain')
        request.write("Error getting access token :(\n")
        request.finish()


def setup_gitter_oauth(api, port, debug=False):
    """Register the OAuth website with Twisted.
    """
    root = Resource()
    root.putChild('', Index(api.bot_fullname))
    root.putChild('auth_gitter', Redirect(api))
    root.putChild('callback', Callback(api))
    site = Site(root)
    site.displayTracebacks = debug
    site.logRequest = True
    reactor.listenTCP(port, site)
