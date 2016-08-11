import json
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted import logger
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET, Site
from urllib import quote

from matrix_gitter.utils import StringProducer


log = logger.Logger()

agent = Agent(reactor)


class BaseMatrixResource(Resource):
    def __init__(self, api):
        self._api = api
        Resource.__init__(self)

    def render(self, request):
        request.responseHeaders.addRawHeader(b"content-type",
                                             b"application/json")
        token = request.args.get('access_token')
        if token:
            token = token[0]
        if not token:
            log.info("No access token")
            request.setResponseCode(401)
            return '{"errcode": "twisted.unauthorized"}'
        elif token != self._api.token_hs:
            log.info("Wrong token: {got!r} != {expected!r}",
                     got=token, expected=self._api.token_hs)
            request.setResponseCode(403)
            return '{"errcode": "M_FORBIDDEN"}'
        else:
            return Resource.render(self, request)


class Transaction(BaseMatrixResource):
    isLeaf = True

    def render_PUT(self, request):
        if len(request.postpath) == 1:
            transaction, = request.postpath
        else:
            raise NoResource

        events = json.load(request.content)['events']
        log.info("Got {nb} events", nb=len(events))
        for event in events:
            log.info("  {user} on {room}",
                     user=event['user_id'], room=event['room_id'])
            log.info("    {type}", type=event['type'])
            log.info("    {content}", content=event['content'])
        return '{}'


class Rooms(BaseMatrixResource):
    isLeaf = True

    def _err(self, err):
        log.error("Error creating room: {err}", err=str(err))
        return None

    def _end(self, request):
        log.info("callback done")
        request.write('{}')
        request.finish()

    def render_GET(self, request):
        if len(request.postpath) == 1:
            alias, = request.postpath
        else:
            raise NoResource

        log.info("Requested room {room}", room=alias)
        alias_localpart = alias.split(':', 1)[0][1:]
        if not alias_localpart.startswith('twisted_yes_'):
            request.setResponseCode(404)
            return '{"errcode": "twisted.no_such_room"}'
        d = agent.request(
            'POST',
            '%s_matrix/client/r0/createRoom?access_token=%s' % (
                self._api.homeserver_url,
                quote(self._api.token_as)),
            Headers({'content-type': ['application/json']}),
            StringProducer(json.dumps({'room_alias_name': alias_localpart})))
        d.addErrback(self._err)
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


class Users(BaseMatrixResource):
    isLeaf = True

    def _err(self, err):
        log.error("Error creating user: {err}", err=str(err))
        return None

    def _end(self, request):
        log.info("callback done")
        request.write('{}')
        request.finish()

    def render_GET(self, request):
        if len(request.postpath) == 1:
            user, = request.postpath
        else:
            raise NoResource

        log.info("Requested user {user}", user=user)
        user_localpart = user.split(':', 1)[0][1:]
        if not user_localpart.startswith('twisted_yes_'):
            request.setResponseCode(404)
            return '{"errcode": "twisted.no_such_user"}'
        d = agent.request(
            'POST',
            '%s_matrix/client/r0/register?access_token=%s' % (
                self._api.homeserver_url,
                quote(self._api.token_as)),
            Headers({'content-type': ['application/json']}),
            StringProducer(json.dumps({'type': 'm.login.application_service',
                                       'username': user_localpart})))
        d.addErrback(self._err)
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


class MatrixAPI(object):
    """Matrix interface.

    This communicates with a Matrix homeserver as an application service.
    """
    def __init__(self, port, homeserver_url, token_as, token_hs):
        self.homeserver_url = homeserver_url
        self.token_as = token_as
        self.token_hs = token_hs

        root = Resource()
        root.putChild('transactions', Transaction(self))
        root.putChild('rooms', Rooms(self))
        root.putChild('users', Users(self))
        site = Site(root)
        site.logRequest = True
        reactor.listenTCP(port, site)
