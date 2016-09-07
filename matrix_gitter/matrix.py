from datetime import datetime
import json
from twisted.internet import defer
from twisted.internet import reactor
from twisted.python.failure import Failure
from twisted.web.client import Agent
from twisted.web.http_headers import Headers
from twisted import logger
from twisted.web.resource import Resource, NoResource
from twisted.web.server import NOT_DONE_YET, Site
import urllib

from matrix_gitter.utils import assert_http_200, Errback, JsonProducer, \
    read_json_response


log = logger.Logger()

agent = Agent(reactor)


HELP_MESSAGE = (
    "This service is entirely controlled through messages sent in private to "
    "this bot. The commands I recognize are:\n"
    " - `list`: displays the list of Gitter room you are in, that you can "
    "join in Matrix via the `invite` command. An asterix indicates a room you "
    "are already in through Matrix.\n"
    " - `gjoin <gitter-room>`: join a new room on Gitter (you can then use "
    "`invite` to talk in it from here).\n"
    " - `gpart <gitter-room>`: leave a room on Gitter. This will kick you out "
    "of the Matrix room if you were in it.\n"
    " - `invite <gitter-room>`: create a Matrix room bridged to that Gitter "
    "room and invite you to join it.\n"
    " - `logout`: throw away your Gitter credentials. Kick you out of all the "
    "rooms you are in.")


def txid():
    """Return a unique ID for transactions.
    """
    return datetime.utcnow().isoformat()


class BaseMatrixResource(Resource):
    """Base class for resources called by the homeserver; checks token.

    This hold the `api` attribute and checks the access token provided by the
    homeserver on each request.
    """
    def __init__(self, api):
        self.api = api
        Resource.__init__(self)

    def matrix_request(self, *args, **kwargs):
        return self.api.matrix_request(*args, **kwargs)

    def render(self, request):
        request.setHeader(b"content-type", b"application/json")
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
    """`/transactions/<txid>` endpoint, where the homeserver delivers events.

    This reacts to events from Matrix.
    """
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
                # FIXME: Remember rooms we've left from private_room_members
                log.info("Joining room {room}", room=room)
                d = self.matrix_request(
                    'POST',
                    '_matrix/client/r0/join/%s',
                    {},
                    room)
                d.addErrback(Errback(log, "Error joining room {room}",
                                     room=room))
            elif (event['type'] == 'm.room.member' and
                    event['content'].get('membership') == 'join'):
                # We or someone else joined a room
                if self.api.get_room(room) is None:
                    # We want to be in private chats with users, but either we
                    # or them may invite; this indicates that the second party
                    # has joined, or that we have joined an empty room.
                    # Request the list of members to find out
                    d = self.matrix_request(
                        'GET',
                        '_matrix/client/r0/rooms/%s/members',
                        None,
                        room,
                        limit='3')
                    d.addCallback(read_json_response)
                    d.addCallback(self.private_room_members, room)
                    d.addErrback(Errback(
                        log, "Error getting members of room {room}",
                        room=room))
                # We don't care about joins to linked rooms, they have to be
                # virtual users
            elif (event['type'] == 'm.room.member' and
                    event['content'].get('membership') != 'join'):
                # FIXME: can this be triggered by other people getting invited?
                # Someone left a room
                room_obj = self.api.get_room(room)

                # It's a linked room: stop forwarding
                if room_obj is not None:
                    log.info("User {user} left room {room}, destroying",
                             user=user, room=room)
                    room_obj.destroy()
                elif user != self.api.bot_fullname:
                    # It is a user's private room
                    user_obj = self.api.get_user(user)
                    if (user_obj is not None and
                            room == user_obj.matrix_private_room):
                        log.info("User {user} left his private room {room}, "
                                 "leaving",
                                 user=user, room=room)
                        self.api.forget_private_room(room)
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
                        d.addErrback(Errback(log, "Error leaving room {room}",
                                             room=room))
            elif (event['type'] == 'm.room.message' and
                    event['content'].get('msgtype') == 'm.text'):
                # Text message to a room
                if user != self.api.bot_fullname:
                    room_obj = self.api.get_room(room)
                    msg = event['content']['body']

                    # If it's a linked room: forward
                    if room_obj is not None:
                        if user == room_obj.user.matrix_username:
                            room_obj.to_gitter(msg)
                    # If it's a message on a private room, handle a command
                    else:
                        user_obj = self.api.get_user(user)
                        if (user_obj is not None and
                                room == user_obj.matrix_private_room):
                            if user_obj.gitter_access_token is not None:
                                self.command(user_obj, msg)
                            else:
                                self.api.private_message(
                                    user_obj,
                                    "You are not logged in.",
                                    False)

        return '{}'

    def command(self, user_obj, msg):
        """Handle a command receive from a user in private chat.
        """
        log.info("Got command from user {user}: {msg!r}",
                 user=user_obj.matrix_username, msg=msg)

        first_word, rest = (msg.split(None, 1) + [''])[:2]
        first_word = first_word.strip().lower()
        rest = rest.strip()

        if first_word == 'list':
            if not rest:
                d = self.api.get_gitter_user_rooms(user_obj)
                d.addCallback(self._send_room_list, user_obj)
                d.addErrback(Errback(
                    log, "Error getting list of rooms for user {user}",
                    user=user_obj.github_username))
                return
        elif first_word == 'gjoin':
            d = self.api.peek_gitter_room(user_obj, rest)
            d.addCallback(lambda room: self.api.join_gitter_room(user_obj,
                                                                 room['id']))
            d.addBoth(self._room_joined, user_obj, rest)
            return
        elif first_word == 'gpart':
            room_obj = self.api.get_gitter_room(user_obj.matrix_username, rest)
            if room_obj is not None:
                d = self.matrix_request(
                    'POST',
                    '_matrix/client/r0/rooms/%s/leave',
                    {},
                    room_obj.matrix_room)
                d.addCallback(lambda r: self.matrix_request(
                    'POST',
                    '_matrix/client/r0/rooms/%s/forget',
                    {},
                    room_obj.matrix_room))
                room_obj.destroy()
            d = self.api.leave_gitter_room(user_obj, rest)
            d.addBoth(self._room_left, user_obj, rest)
            return
        elif first_word == 'invite':
            room_obj = self.api.get_gitter_room(user_obj.matrix_username, rest)
            # Room already exist: invite anyway and display a message
            if room_obj is not None:
                d = self.api.matrix_request(
                    'POST',
                    '_matrix/client/r0/rooms/%s/invite',
                    {'user_id': user_obj.matrix_username},
                    room_obj.matrix_room)
                d.addErrback(Errback(
                    log, "Error inviting {user} to bridged room {matrix}",
                    user=user_obj.matrix_username,
                    matrix=room_obj.matrix_room))
                self.api.private_message(
                    user_obj,
                    "You are already on room {gitter}: {matrix}".format(
                        gitter=room_obj.gitter_room_name,
                        matrix=room_obj.matrix_room),
                    False)
            else:
                # Check if the room is available
                # FIXME: We want to know if the user is on it
                d = self.api.peek_gitter_room(user_obj, rest)
                d.addBoth(self._new_room, user_obj, rest)
            return
        elif first_word == 'logout':
            for room_obj in self.api.get_all_rooms(user_obj.matrix_username):
                room_obj.destroy()
            self.api.logout(user_obj.matrix_username)
            self.api.private_message(user_obj, "You have been logged out.",
                                     False)
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/rooms/%s/leave',
                {},
                user_obj.matrix_private_room)
            d.addCallback(lambda r: self.matrix_request(
                              'POST',
                              '_matrix/client/r0/rooms/%s/forget',
                              {},
                user_obj.matrix_private_room))
            self.api.forget_private_room(user_obj.matrix_private_room)
            return

        self.api.private_message(user_obj, "Invalid command!", False)

    def _send_room_list(self, rooms, user_obj):
        log.info("Got room list for user {user} ({nb} rooms)",
                 user=user_obj.matrix_username, nb=len(rooms))
        msg = ["Rooms you are currently in on Gitter (* indicates you are in "
               "that room from Matrix as well):"]
        for gitter_id, gitter_name, matrix_name in sorted(rooms,
                                                          key=lambda r: r[1]):
            msg.append(" - %s%s" % (gitter_name,
                                    " *" if matrix_name is not None else ""))
        self.api.private_message(user_obj, "\n".join(msg), False)

    def _room_joined(self, result, user_obj, room):
        if isinstance(result, Failure):
            log.failure("Failed to join room {room}", result, room=room)
            msg = "Couldn't join room {room}"
        else:
            msg = "Successfully joined room {room}"
        self.api.private_message(user_obj, msg.format(room=room), False)

    def _room_left(self, result, user_obj, room):
        if isinstance(result, Failure):
            log.failure("Failed to leave room {room}", result, room=room)
            msg = "Couldn't leave room {room}"
        else:
            msg = "Successfully left room {room}"
        self.api.private_message(user_obj, msg.format(room=room), False)

    def _new_room(self, result, user_obj, gitter_room):
        if isinstance(result, Failure):
            log.failure("Couldn't get info for room {room}", result,
                        room=gitter_room)
            self.api.private_message(
                user_obj,
                "Can't access room {room}".format(room=gitter_room),
                False)
            return

        d = self.matrix_request(
            'POST',
            '_matrix/client/r0/createRoom',
            {'preset': 'private_chat',
             'name': "%s (Gitter)" % gitter_room})
        # FIXME: don't allow the user to invite others to that room
        d.addCallback(read_json_response)
        d.addCallback(self._bridge_rooms, user_obj, result)
        d.addErrback(Errback(log, "Couldn't create a room"))

    def _bridge_rooms(self, (response, content), user_obj, gitter_room_obj):
        matrix_room = content['room_id']

        self.api.bridge_rooms(user_obj, matrix_room, gitter_room_obj)

        d = self.api.matrix_request(
            'POST',
            '_matrix/client/r0/rooms/%s/invite',
            {'user_id': user_obj.matrix_username},
            matrix_room)
        # FIXME: Should we only start forwarding when the user joins?

    def private_room_members(self, (response, content), room):
        """Get list of members on what should be a private room.

        If there is one member, wait for someone to join.
        If there are two members, this is now the private room for that user.
        If there are more members, leave it.
        """
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
            d.addErrback(Errback(log, "Error leaving room {room}", room=room))
            self.api.forget_private_room(room)
        else:
            # Find the member that's not us
            user = [m for m in members if m != self.api.bot_fullname]
            if len(user) == 1:
                user_obj = self.api.get_user(user[0])

                # Register this room as the private chat with that user
                self.api.register_private_room(user_obj.matrix_username, room)
                user_obj.matrix_private_room = room

                # Say hi
                msg = ("Hi {user}! I am the interface to this Matrix-Gitter "
                       "bridge.").format(
                    user=user_obj.matrix_username.split(':', 1)[0])
                if user_obj.github_username is not None:
                    msg += "\nYou are currently logged in as {gh}.\n".format(
                        gh=user_obj.github_username)
                    msg += HELP_MESSAGE
                else:
                    msg += ("\nYou will need to log in to your Gitter account "
                            "or sign up for one before I can do anything for "
                            "you.\n"
                            "You can do this now using this link: "
                            "{link}").format(
                        link=self.api.gitter_auth_link(
                            user_obj.matrix_username))
                self.api.private_message(user_obj, msg, False)


class Users(BaseMatrixResource):
    """Endpoint that creates users the homeserver asks about.
    """
    # FIXME: useless since we create all the needed virtual users?
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
        if not user_localpart.startswith('gitter'):
            request.setResponseCode(404)
            return '{"errcode": "twisted.no_such_user"}'
        d = self.matrix_request(
            'POST',
            '_matrix/client/r0/register',
            {'type': 'm.login.application_service',
             'username': user_localpart})
        d.addErrback(Errback(log, "Error creating user {user}", user=user))
        d.addBoth(lambda res: self._end(request))
        return NOT_DONE_YET


class MatrixAPI(object):
    """Matrix interface.

    This communicates with a Matrix homeserver as an application service.
    """
    def __init__(self, bridge, port, homeserver_url, homeserver_domain,
                 botname, token_as, token_hs, debug=False):
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

        # Create virtual user for bot
        if not self.bridge.virtualuser_exists('gitter'):
            log.info("Creating user gitter")
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/register',
                {'type': 'm.login.application_service',
                 'username': 'gitter'})
            self.bridge.add_virtualuser('gitter')
            d.addErrback(Errback(log, "Error creating user 'gitter' for the "
                                      "bridge; usage over federated rooms "
                                      "might not work correctly"))

        root = Resource()
        root.putChild('transactions', Transaction(self))
        root.putChild('users', Users(self))
        site = Site(root)
        site.displayTracebacks = debug
        site.logRequest = True
        reactor.listenTCP(port, site)

    def matrix_request(self, method, uri, content, *args, **kwargs):
        """Matrix client->homeserver API request.
        """
        if args:
            uri = uri % tuple(urllib.quote(a) for a in args)
        if isinstance(uri, unicode):
            uri = uri.encode('ascii')
        getargs = {'access_token': self.token_as}
        getargs.update(kwargs)
        uri = '%s%s?%s' % (
            self.homeserver_url,
            uri,
            urllib.urlencode(getargs))
        log.info("matrix_request {method} {uri} {content!r}",
                 method=method, uri=uri, content=content)
        d = agent.request(
            method,
            uri,
            Headers({'content-type': ['application/json'],
                     'accept': ['application/json']}),
            JsonProducer(content) if content is not None else None)
        if kwargs.pop('assert200', True):
            d.addCallback(assert_http_200)
        return d

    def gitter_info_set(self, user_obj):
        """Called from the Bridge when we get a user's Gitter info.

        This happens when a user authenticates through the OAuth webapp.
        """
        # If we have a private chat with the user, tell him he logged in,
        # else start new private chat
        self.private_message(user_obj,
                             "You are now logged in as {gh}.\n{help}".format(
                                 gh=user_obj.github_username,
                                 help=HELP_MESSAGE),
                             True)

    def forward_message(self, room, username, msg):
        """Called from the Bridge to send a forwarded message to a room.

        Creates the user, invites him on the room, then speak the message.
        """
        user = '@gitter_%s:%s' % (username, self.homeserver_domain)

        if not self.bridge.virtualuser_exists('gitter_%s' % username):
            log.info("Creating user {user}", user=username)
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/register',
                {'type': 'm.login.application_service',
                 'username': 'gitter_%s' % username},
                assert200=False)
            d.addCallback(self._set_user_name, user, username)
            d.addCallback(lambda r: self.bridge.add_virtualuser(
                'gitter_%s' % username))
        else:
            d = defer.succeed(None)
        if not self.bridge.is_virtualuser_on_room('gitter_%s' % username,
                                                  room):
            d.addCallback(self._invite_user, room, user)
            d.addCallback(self._join_user, room, user)
            d.addCallback(lambda r: self.bridge.add_virtualuser_on_room(
                'gitter_%s' % username, room))
        d.addCallback(self._post_message, room, user, msg)
        d.addErrback(Errback(log,
                             "Error posting message to Matrix room {room}",
                             room=room))

    def _set_user_name(self, response, user, username):
        return self.matrix_request(
            'PUT',
            '_matrix/client/r0/profile/%s/displayname',
            {'displayname': "%s (Gitter)" % username},
            user,
            assert200=False,
            user_id=user)

    def _invite_user(self, response, room, user):
        return self.matrix_request(
            'POST',
            '_matrix/client/r0/rooms/%s/invite',
            {'user_id': user},
            room,
            assert200=False)

    def _join_user(self, response, room, user):
        return self.matrix_request(
            'POST',
            '_matrix/client/r0/rooms/%s/join',
            {},
            room,
            user_id=user)

    def _post_message(self, response, room, user, msg):
        return self.matrix_request(
            'PUT',
            '_matrix/client/r0/rooms/%s/send/m.room.message/%s',
            {'msgtype': 'm.text',
             'body': msg},
            room,
            txid(),
            user_id=user)

    def private_message(self, user_obj, msg, invite):
        """Send a message to a user on the appropriate private room.

        If we have no private room with the requested user, `invite` indicates
        whether to create a private room and invite him.
        """
        if user_obj.matrix_private_room is not None:
            self.matrix_request(
                'PUT',
                '_matrix/client/r0/rooms/%s/send/m.room.message/%s',
                {'msgtype': 'm.text',
                 'body': msg},
                user_obj.matrix_private_room,
                txid())
        elif invite:
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/createRoom',
                {'invite': [user_obj.matrix_username],
                 'preset': 'private_chat'})
            d.addCallback(read_json_response)
            d.addCallback(self._private_chat_created, user_obj.matrix_username)
            d.addErrback(Errback(
                log, "Error creating private room for user {user}",
                user=user_obj.matrix_username))

    def _private_chat_created(self, (request, content), user):
        room = content['room_id']
        log.info("Created private chat with user {user}: {room}",
                 user=user, room=room)
        self.register_private_room(user, room)

    def register_private_room(self, user, room):
        """Set the private room with a user, getting rid of the previous one.
        """
        log.info("Storing new private room for user {user}: {room}",
                 user=user, room=room)
        previous_room = self.bridge.set_user_private_matrix_room(user, room)

        # If there was already a private room, leave it
        if previous_room is not None and previous_room != room:
            log.info("Leaving previous private room {room}",
                     room=previous_room)
            d = self.matrix_request(
                'POST',
                '_matrix/client/r0/rooms/%s/leave',
                {},
                previous_room)
            d.addCallback(lambda r: self.matrix_request(
                'POST',
                '_matrix/client/r0/rooms/%s/forget',
                {},
                previous_room))
            d.addErrback(Errback(log, "Error leaving room {room}",
                                 room=previous_room))
            return True
        else:
            return False

    def forget_private_room(self, room):
        """Forget a Matrix room that was someone's private room.
        """
        self.bridge.forget_private_matrix_room(room)

    def get_room(self, room=None):
        """Find a linked room from its Matrix ID.
        """
        return self.bridge.get_room(matrix_room=room)

    def get_gitter_room(self, matrix_username, gitter_room):
        """Find a linked room from the user and Gitter name.
        """
        return self.bridge.get_room(matrix_username=matrix_username,
                                    gitter_room_name=gitter_room)

    def get_all_rooms(self, user):
        """Get the list of all linked rooms for a given Matrix user.
        """
        return self.bridge.get_all_rooms(user)

    def logout(self, user):
        """Removes a user's Gitter info from the database.

        This assumes all his linked rooms are already gone.
        """
        self.bridge.logout(user)

    def get_user(self, user):
        """Find a user in the database from its Matrix username.
        """
        user_obj = self.bridge.get_user(matrix_user=user)
        if user_obj is None:
            return self.bridge.create_user(user)
        return user_obj

    def get_gitter_user_rooms(self, user_obj):
        """List the Gitter rooms a user is in.

        The user is in these on Gitter and not necessarily through Matrix.
        """
        return self.bridge.get_gitter_user_rooms(user_obj)

    def peek_gitter_room(self, user_obj, gitter_room_name):
        """Get info on a Gitter room without joining it.
        """
        # FIXME: This should indicate if the user is on it
        return self.bridge.peek_gitter_room(user_obj, gitter_room_name)

    def join_gitter_room(self, user_obj, gitter_room_id):
        """Join a Gitter room.

        This happens on Gitter only and does not mean the room becomes linked.
        """
        return self.bridge.join_gitter_room(user_obj, gitter_room_id)

    def leave_gitter_room(self, user_obj, gitter_room_name):
        """Leave a Gitter room.

        This assumes the room is not longer linked for the user.
        """
        return self.bridge.leave_gitter_room(user_obj, gitter_room_name)

    def bridge_rooms(self, user_obj, matrix_room, gitter_room_obj):
        """Setup a linked room and start forwarding.
        """
        self.bridge.bridge_rooms(user_obj, matrix_room, gitter_room_obj)

    def gitter_auth_link(self, user):
        """Get the link a user should visit to authenticate.
        """
        return self.bridge.gitter_auth_link(user)
