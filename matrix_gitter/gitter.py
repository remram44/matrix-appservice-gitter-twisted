import hashlib
import hmac
from twisted import logger
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
import urllib

from matrix_gitter.gitter_oauth import setup_gitter_oauth
from matrix_gitter.utils import read_json_response, JsonProducer


log = logger.Logger()

agent = Agent(reactor)


class GitterAPI(object):
    """Gitter interface.

    This communicates with Gitter using their API, authenticating via OAuth2 as
    specific users.
    """
    def __init__(self, bridge, port, url, oauth_key, oauth_secret):
        self.bridge = bridge

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.url = url

        setup_gitter_oauth(self, port)

    def secret_hmac(self, msg):
        return hmac.new(self.bridge.secret_key, msg, hashlib.sha1).hexdigest()

    def gitter_request(self, method, uri, content, *args, **kwargs):
        if 'access_token' in kwargs:
            access_token = kwargs.pop('access_token')
        else:
            access_token = kwargs.pop('user').gitter_access_token
        if args:
            uri = uri % tuple(urllib.quote(a) for a in args)
        if isinstance(uri, unicode):
            uri = uri.encode('ascii')
        return agent.request(
            method,
            'https://api.gitter.im/%s' % uri,
            Headers({'accept': ['application/json'],
                     'authorization': ['Bearer %s' % access_token]}),
            JsonProducer(content) if content is not None else None)

    def set_access_token(self, matrix_user, access_token):
        log.info("Getting GitHub username for Matrix user {matrix}",
                 matrix=matrix_user)
        d = self.gitter_request('GET', 'v1/user', None,
                                access_token=access_token)
        d.addCallback(read_json_response)
        d.addCallback(self._set_user_access_token, matrix_user, access_token)

    def _set_user_access_token(self, (request, content),
                               matrix_user, access_token):
        github_user = content[0]['username']
        log.info("Storing Gitter access token for user {matrix}/{github}",
                 matrix=matrix_user, github=github_user)
        self.bridge.set_gitter_info(matrix_user, github_user, access_token)

    def auth_link(self, matrix_user):
        state = '%s|%s' % (matrix_user, self.secret_hmac(matrix_user))
        return '%sauth_gitter/%s' % (self.url, urllib.quote(state))
