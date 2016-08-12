from matrix_gitter.gitter_oauth import setup_gitter_oauth
from twisted import logger
import urllib


log = logger.Logger()


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

    def store_access_token(self, user, access_token):
        log.info("Storing Gitter access token for user {user}",
                 user=user)
        self.bridge.store_gitter_access_token(user, access_token)

    def auth_link(self, matrix_user):
        return '%sauth_gitter/%s' % (self.url, urllib.quote(matrix_user))
