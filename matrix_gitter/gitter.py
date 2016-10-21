import hashlib
import hmac
from twisted import logger
import urllib

from matrix_gitter.gitter_oauth import setup_gitter_oauth
from matrix_gitter.utils import assert_http_200, Errback, JsonProducer, \
    read_json_response, request


log = logger.Logger()


class GitterAPI(object):
    """Gitter interface.

    This communicates with Gitter using their API, authenticating via OAuth2 as
    specific users.
    """
    def __init__(self, bridge, port, url, oauth_key, oauth_secret, debug=False):
        self.bridge = bridge

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.url = url

        setup_gitter_oauth(self, port, debug=debug)

    @property
    def bot_fullname(self):
        return self.bridge.bot_fullname

    def secret_hmac(self, msg):
        """HMAC a message with the secret in the config.
        """
        return hmac.new(self.bridge.secret_key, msg, hashlib.sha1).hexdigest()

    def gitter_request(self, method, uri, content, *args, **kwargs):
        """Gitter API request.
        """
        if 'access_token' in kwargs:
            access_token = kwargs.pop('access_token')
        else:
            access_token = kwargs.pop('user').gitter_access_token
        if args:
            uri = uri % tuple(urllib.quote(a) for a in args)
        if isinstance(uri, unicode):
            uri = uri.encode('ascii')
        headers = {'accept': 'application/json',
                   'authorization': 'Bearer %s' % access_token}
        if content is not None:
            headers['content-type'] = 'application/json'
        log.debug("gitter_request {method} {uri} {content!r}",
                  method=method, uri=uri, content=content)
        return request(
            method,
            'https://api.gitter.im/%s' % uri,
            headers,
            JsonProducer(content) if content is not None else None)

    def gitter_stream(self, method, uri, *args, **kwargs):
        """Request to Gitter's streaming API.
        """
        if 'access_token' in kwargs:
            access_token = kwargs.pop('access_token')
        else:
            access_token = kwargs.pop('user').gitter_access_token
        if args:
            uri = uri % tuple(urllib.quote(a) for a in args)
        if isinstance(uri, unicode):
            uri = uri.encode('ascii')
        headers = {'accept': 'application/json',
                   'authorization': 'Bearer %s' % access_token}
        log.debug("gitter_stream {method} {uri} {content!r}",
                  method=method, uri=uri)
        return request(
            method,
            'https://stream.gitter.im/%s' % uri,
            headers,
            timeout=None)

    def set_access_token(self, matrix_user, access_token):
        """Set the access token for a user who completed OAuth.
        """
        log.info("Getting GitHub username for Matrix user {matrix}",
                 matrix=matrix_user)
        d = self.gitter_request('GET', 'v1/user', None,
                                access_token=access_token)
        d.addCallback(assert_http_200)
        d.addCallback(read_json_response)
        d.addCallback(self._set_user_access_token, matrix_user, access_token)
        d.addErrback(Errback(log,
                             "Error getting username for Matrix user {matrix}",
                             matrix=matrix_user))

    def _set_user_access_token(self, (response, content),
                               matrix_user, access_token):
        github_user = content[0]['username']
        gitter_id = content[0]['id']
        log.info("Storing Gitter access token for user {matrix}/{github}",
                 matrix=matrix_user, github=github_user)
        self.bridge.set_gitter_info(matrix_user, github_user, gitter_id,
                                    access_token)

    def get_gitter_user_rooms(self, user_obj):
        """List the Gitter rooms a user is in.
        """
        d = self.gitter_request('GET', 'v1/rooms', None,
                                   user=user_obj)
        d.addCallback(assert_http_200)
        d.addCallback(read_json_response)
        d.addCallback(self._read_gitter_rooms)
        return d

    def _read_gitter_rooms(self, (response, content)):
        return [(room['id'], room['url'][1:])
                for room in content]

    def get_room(self, gitter_room, **kwargs):
        """Get a Gitter room without joining it.
        """
        d = self.gitter_request(
            'POST',
            'v1/rooms',
            {'uri': gitter_room},
            **kwargs)
        d.addCallback(assert_http_200)
        d.addCallback(read_json_response)
        d.addCallback(lambda (r, c): c)
        return d

    def join_room(self, user_obj, gitter_room_id):
        """Join a Gitter room.
        """
        d = self.gitter_request(
            'POST',
            'v1/user/%s/rooms',
            {'id': gitter_room_id},
            user_obj.gitter_id,
            user=user_obj)
        d.addCallback(assert_http_200)
        d.addCallback(read_json_response)
        d.addCallback(lambda (r, c): c)
        return d

    def leave_room(self, user_obj, gitter_room):
        """Leave a Gitter room.
        """
        d = self.get_room(gitter_room, user=user_obj)
        d.addCallback(self._leave_room, user_obj)
        return d

    def _leave_room(self, room, user_obj):
        log.info("Resolved {name} into {id}, leaving...",
                 name=room['url'][1:], id=room['id'])
        user_id = user_obj.gitter_id
        return self.gitter_request(
            'DELETE',
            'v1/rooms/%s/users/%s' % (room['id'], user_id),
            None,
            user=user_obj)

    def auth_link(self, matrix_user):
        """Get the link a user should visit to authenticate.
        """
        state = '%s|%s' % (matrix_user, self.secret_hmac(matrix_user))
        return '%sauth_gitter/%s' % (self.url, urllib.quote(state))
