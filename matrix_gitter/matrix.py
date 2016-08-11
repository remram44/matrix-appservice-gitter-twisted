from datetime import datetime
import json
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted import logger
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET, Site
import urllib

from matrix_gitter.utils import JsonProducer, read_json_response

log = logger.Logger()

agent = Agent(reactor)


class BaseMatrixResource(Resource):
    def __init__(self, api):
        self.api = api
        Resource.__init__(self)

    def matrix_request(self, method, uri, content, *args, **kwargs):
        if args:
            uri = uri % tuple(urllib.quote(a) for a in args)
        if isinstance(uri, unicode):
            uri = uri.encode('ascii')
        getargs = {'access_token': self.api.token_as}
        getargs.update(kwargs)
        return agent.request(
            method,
            '%s%s?%s' % (
                self.api.homeserver_url,
                uri,
                urllib.urlencode(getargs)),
            Headers({'content-type': ['application/json']}),
            JsonProducer(content) if content is not None else None)

    @staticmethod
    def txid():
        return datetime.utcnow().isoformat()

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
        elif token != self.api.token_hs:
            log.info("Wrong token: {got!r} != {expected!r}",
                     got=token, expected=self.api.token_hs)
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
        for event in events:
            user = event['user_id']
            room = event['room_id']
            log.info("  {user} on {room}",
                     user=user, room=room)
            log.info("    {type}", type=event['type'])
            log.info("    {content}", content=event['content'])

            if (event['type'] == 'm.room.member' and
                    event['content'].get('membership') == 'invite'):
                # We've been invited to a room, join it
                log.info("Joining room {room}", room=room)
                d = self.matrix_request(
                    'POST',
                    '_matrix/client/r0/rooms/%s/join',
                    {},
                    room)
            elif (event['type'] == 'm.room.member' and
                    user == self.api.bot_fullname and
                    event['content'].get('membership') == 'join'):
                # We joined a room
                # TODO: if it's a linked room?
                # Request list of members
                d = self.matrix_request(
                    'GET',
                    '_matrix/client/r0/rooms/%s/members',
                    None,
                    room,
                    limit='3')
                d.addCallback(read_json_response)
                d.addCallback(self.room_members, room)

        return '{}'

    def room_members(self, (response, content), room):
        if response.code != 200:
            return
        members = [m['state_key']
                   for m in content['chunk']
                   if m['content']['membership'] == 'join']
        log.info("Room members for {room}: {members}",
                 room=room,
                 members=members)
        if len(members) > 2:
            log.info("Too many members in room {room}, leaving", room=room)
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/rooms/%s/leave',
                {},
                room)
            d.addCallback(lambda r: self.matrix_request(
                              'POST',
                              '_matrix/client/r0/rooms/%s/forget',
                              {},
                              room))
        else:
            # Find the member that's not us
            user, = [m for m in members if m != self.api.bot_fullname]

            # Register this room as the private chat with that user
            self.api.register_private_room(user, room)

            # Say hi
            self.matrix_request(
                'PUT',
                '_matrix/client/r0/rooms/%s/send/m.room.message/%s',
                {'msgtype': 'm.text',
                 'body': "Hello!"},
                room,
                self.txid())


class Rooms(BaseMatrixResource):
    isLeaf = True

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
        d = self.matrix_request(
            'POST',
            '_matrix/client/r0/createRoom',
            {'room_alias_name': alias_localpart})
        #d.addErrback()
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


class Users(BaseMatrixResource):
    isLeaf = True

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
        d = self.matrix_request(
            'POST',
            '_matrix/client/r0/register',
            {'type': 'm.login.application_service',
             'username': user_localpart})
        #d.addErrback()
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


class MatrixAPI(object):
    """Matrix interface.

    This communicates with a Matrix homeserver as an application service.
    """
    def __init__(self, bridge, port, homeserver_url, homeserver_domain,
                 botname,
                 token_as, token_hs):
        self.bridge = bridge
        self.homeserver_url = homeserver_url
        self.homeserver_domain = homeserver_domain
        self.token_as = token_as
        self.token_hs = token_hs

        if botname[0] == '@':
            botname = botname[1:]
        if ':' in botname:
            botname, domain = botname.split(':', 1)
            if domain != homeserver_domain:
                raise ValueError("Bot domain doesn't match homeserver")
        self.bot_username = botname
        self.bot_fullname = '@%s:%s' % (botname, homeserver_domain)

        root = Resource()
        root.putChild('transactions', Transaction(self))
        root.putChild('rooms', Rooms(self))
        root.putChild('users', Users(self))
        site = Site(root)
        site.logRequest = True
        reactor.listenTCP(port, site)

    def register_private_room(self, user, room):
        # TODO
        pass
