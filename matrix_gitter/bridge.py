import json
import os
import sqlite3
from twisted import logger
from twisted.internet.protocol import Protocol, connectionDone

from matrix_gitter.gitter import GitterAPI
from matrix_gitter.markup import matrix_to_gitter
from matrix_gitter.matrix import MatrixAPI
from matrix_gitter.utils import Errback, RateLimiter


log = logger.Logger()


class User(object):
    """A bridge user as it appears in the database.

    This user has a Matrix identity but might not be logged into Gitter yet.
    """
    def __init__(self, matrix_username, matrix_private_room,
                 github_username, gitter_id, gitter_access_token):
        self.matrix_username = matrix_username
        self.matrix_private_room = matrix_private_room
        self.github_username = github_username
        self.gitter_id = gitter_id
        self.gitter_access_token = gitter_access_token

    @staticmethod
    def from_row(row):
        return User(row['matrix_username'], row['matrix_private_room'],
                    row['github_username'], row['gitter_id'],
                    row['gitter_access_token'])


gitter_stream_limit = RateLimiter("gitter_stream")


class Room(Protocol):
    """A room linked between Gitter and Matrix.

    This stream events from the Gitter API, and gets fed messages from the
    Matrix API.
    """
    # FIXME: This class has too much Gitter logic that should be in gitter.py
    def __init__(self, bridge, user, matrix_room,
                 gitter_room_name, gitter_room_id):
        self.bridge = bridge
        self.user = user
        self.matrix_room = matrix_room
        self.gitter_room_name = gitter_room_name
        self.gitter_room_id = gitter_room_id

        self.stream_response = None
        self.destroyed = False

        gitter_stream_limit.schedule(self.start_stream)

    def start_stream(self):
        if self.destroyed:
            return

        self.content = []
        # Start stream
        d = self.bridge.gitter.gitter_stream(
            'GET',
            'v1/rooms/%s/chatMessages',
            self.gitter_room_id,
            user=self.user)
        d.addCallbacks(self._receive_stream, self.start_failed)

    def start_failed(self, err):
        log.failure("Error starting Gitter stream for user {user} room {room}",
                    user=self.user.github_username, room=self.gitter_room_name)
        gitter_stream_limit.fail()
        gitter_stream_limit.schedule(self.start_stream)

    def _receive_stream(self, response):
        log.info("Stream started for user {user} room {room}",
                 user=self.user.github_username, room=self.gitter_room_name)
        gitter_stream_limit.success()
        response.deliverBody(self)
        self.stream_response = response

    def dataReceived(self, data):
        if self.destroyed:
            return
        if '\n' in data:
            data = data.split('\n', 1)
            content, self.content = self.content + [data[0]], [data[1]]
            document = ''.join(content).strip()
            if not document:
                return
            log.debug("Data received on stream for user {user} room {room}:\n"
                      "{data!r}",
                      user=self.user.github_username,
                      room=self.gitter_room_name,
                      data=document)
            try:
                message = json.loads(document)
            except Exception:
                log.failure("Error decoding JSON on stream for user {user} "
                            "room {room}",
                            user=self.user.github_username,
                            room=self.gitter_room_name)
            else:
                log.info("Got message for user {user} room {room}: {msg!r}",
                         user=self.user.github_username,
                         room=self.gitter_room_name,
                         msg=message)
                try:
                    username = message['fromUser']['username']
                    if username != self.user.github_username:
                        self.to_matrix(username, message['text'])
                except Exception:
                    log.failure("Exception handling Gitter message")
        else:
            self.content.append(data)

    def connectionLost(self, reason=connectionDone):
        log.info("Lost stream for user {user} room {room}",
                 user=self.user.github_username, room=self.gitter_room_name)
        self.stream_response = None
        if not self.destroyed:
            gitter_stream_limit.schedule(self.start_stream)

    def to_gitter(self, msg):
        """Forward a message to Gitter.
        """
        d = self.bridge.gitter.gitter_request(
            'POST',
            'v1/rooms/%s/chatMessages',
            {'text': matrix_to_gitter(msg)},
            self.gitter_room_id,
            user=self.user)
        d.addErrback(Errback(log,
                             "Error posting message to Gitter room {room}",
                             room=self.gitter_room_name))

    def to_matrix(self, username, msg):
        """Forward a message to Matrix.
        """
        self.bridge.matrix.forward_message(self.matrix_room, username, msg)

    def destroy(self):
        """Stop forwarding and remove the room from the Bridge.
        """
        # FIXME: Handle getting kicked from the Gitter room
        if self.destroyed:
            return
        self.destroyed = True
        if self.stream_response is not None:
            pass  # FIXME: how to close the connection?
        self.bridge.destroy_room(self)


class Bridge(object):
    """Main application object.

    This is a bridge between Matrix and Gitter. It uses the Matrix application
    service on one side to communicate to a homeserver that will act as bridge
    for the users, and the Gitter API on the other side.

    We also use a database to keep information about users and rooms.
    """
    def __init__(self, config):
        self.rooms_matrix = {}
        self.rooms_gitter_name = {}

        create_db = not os.path.exists('database.sqlite3')
        self.db = sqlite3.connect('database.sqlite3')
        self.db.isolation_level = None
        self.db.row_factory = sqlite3.Row

        if create_db:
            self.db.execute(
                '''
                CREATE TABLE users(
                    matrix_username TEXT NOT NULL PRIMARY KEY,
                    matrix_private_room TEXT NULL,
                    github_username TEXT NULL,
                    gitter_id TEXT NULL,
                    gitter_access_token TEXT NULL);
                ''')
            self.db.execute(
                '''
                CREATE INDEX users_githubuser_idx ON users(
                    github_username);
                ''')
            self.db.execute(
                '''
                CREATE UNIQUE INDEX users_privateroom_idx ON users(
                    matrix_private_room);
                ''')

            self.db.execute(
                '''
                CREATE TABLE virtual_users(
                    matrix_username TEXT NOT NULL PRIMARY KEY);
                ''')

            self.db.execute(
                '''
                CREATE TABLE rooms(
                    user TEXT NOT NULL,
                    matrix_room TEXT NOT NULL,
                    gitter_room_name TEXT NOT NULL,
                    gitter_room_id TEXT NOT NULL);
                ''')
            self.db.execute(
                '''
                CREATE UNIQUE INDEX rooms_user_matrixroom_idx ON rooms(
                    user, matrix_room);
                ''')

            self.db.execute(
                '''
                CREATE TABLE virtual_user_rooms(
                    matrix_username TEXT NOT NULL,
                    matrix_room TEXT NOT NULL);
                ''')
            self.db.execute(
                '''
                CREATE UNIQUE INDEX virtualusersrooms_user_room_idx ON
                        virtual_user_rooms(
                    matrix_username, matrix_room);
                ''')

        self.debug = config.get('DEBUG', False)

        self.secret_key = config['unique_secret_key']
        if self.secret_key == 'change this before running':
            raise RuntimeError("Please go over the configuration and set "
                               "unique_secret_key to a unique secret string")

        homeserver_url = config['matrix_homeserver_url']
        if homeserver_url[-1] != '/':
            homeserver_url += '/'

        self.matrix = MatrixAPI(
            self,
            config['matrix_appservice_port'],
            config['matrix_homeserver_url'],
            config['matrix_homeserver_domain'],
            config['matrix_botname'],
            config['matrix_appservice_token'],
            config['matrix_homeserver_token'],
            debug=self.debug)

        gitter_login_url = config['gitter_login_url']
        if gitter_login_url[-1] != '/':
            gitter_login_url += '/'

        self.gitter = GitterAPI(
            self,
            config['gitter_login_port'],
            gitter_login_url,
            config['gitter_oauth_key'],
            config['gitter_oauth_secret'],
            debug=self.debug)

        # Initialize rooms
        cur = self.db.execute(
            '''
            SELECT u.matrix_username, u.matrix_private_room,
                u.github_username, u.gitter_id, u.gitter_access_token,
                r.matrix_room, r.gitter_room_name, r.gitter_room_id
            FROM rooms r
            INNER JOIN users u ON r.user = u.matrix_username;
            ''')
        log.info("Initializing rooms...")
        for row in cur:
            user_obj = User.from_row(row)
            matrix_room = row['matrix_room']
            gitter_room_name = row['gitter_room_name']
            gitter_room_id = row['gitter_room_id']
            room = Room(self, user_obj, matrix_room,
                        gitter_room_name, gitter_room_id)
            self.rooms_matrix[matrix_room] = room
            self.rooms_gitter_name.setdefault(
                user_obj.matrix_username, {})[
                gitter_room_name] = room
            log.info("{matrix} {gitter} {user_m} {user_g}",
                     matrix=matrix_room, gitter=gitter_room_name,
                     user_m=user_obj.matrix_username,
                     user_g=user_obj.github_username)

    @property
    def bot_fullname(self):
        return self.matrix.bot_fullname

    def destroy_room(self, room):
        self.db.execute(
            '''
            DELETE FROM rooms
            WHERE user = ? AND matrix_room = ?;
            ''',
            (room.user.matrix_username, room.matrix_room))
        self.rooms_matrix.pop(room.matrix_room, None)
        self.rooms_gitter_name.get(
            room.user.matrix_username, {}).pop(
            room.gitter_room_name, None)

    def bridge_rooms(self, user_obj, matrix_room, gitter_room_obj):
        """Create the Room and database entry, and start forwarding.
        """
        gitter_room_name = gitter_room_obj['url'][1:]
        gitter_room_id = gitter_room_obj['id']
        self.db.execute(
            '''
            INSERT INTO rooms(user, matrix_room,
                gitter_room_name, gitter_room_id)
            VALUES(?, ?, ?, ?);
            ''',
            (user_obj.matrix_username, matrix_room,
             gitter_room_name, gitter_room_id))
        room = Room(self, user_obj, matrix_room,
                    gitter_room_name, gitter_room_id)
        self.rooms_matrix[matrix_room] = room
        self.rooms_gitter_name.setdefault(
            user_obj.matrix_username, {})[
            gitter_room_name] = room
        log.info("Create room:")
        log.info("{matrix} {gitter} {user_m} {user_g}",
                 matrix=matrix_room, gitter=gitter_room_name,
                 user_m=user_obj.matrix_username,
                 user_g=user_obj.github_username)

    def get_room(self, matrix_room=None, gitter_room_name=None,
                 matrix_username=None):
        """Find a linked room.
        """
        if matrix_room is not None and gitter_room_name is None:
            return self.rooms_matrix.get(matrix_room)
        elif (gitter_room_name is not None and matrix_username is not None and
                matrix_room is None):
            return self.rooms_gitter_name.get(
                matrix_username, {}).get(
                gitter_room_name)
        else:
            raise TypeError

    def get_all_rooms(self, matrix_user):
        """Get all the linked rooms this user is in.
        """
        return self.rooms_gitter_name.get(matrix_user, {}).values()

    def create_user(self, matrix_user):
        """Create a new user in the database.
        """
        if matrix_user == self.bot_fullname:
            try:
                raise RuntimeError("CREATING USER FOR BOT")
            except RuntimeError:
                log.failure("CREATING USER FOR BOT")
        self.db.execute(
            '''
            INSERT OR IGNORE INTO users(matrix_username)
            VALUES(?);
            ''',
            (matrix_user,))
        return self.get_user(matrix_user=matrix_user)

    def get_user(self, matrix_user=None, github_user=None):
        """Find a user in the database.
        """
        if matrix_user is not None and github_user is None:
            cur = self.db.execute(
                '''
                SELECT * FROM users
                WHERE matrix_username = ?;
                ''',
                (matrix_user,))
        elif github_user is not None and matrix_user is None:
            cur = self.db.execute(
                '''
                SELECT * FROM users
                WHERE github_username = ?;
                ''',
                (github_user,))
        else:
            raise TypeError
        try:
            row = next(cur)
        except StopIteration:
            return None
        else:
            return User.from_row(row)

    def logout(self, matrix_user):
        """Removes a user's Gitter info from the database.

        This assumes all his linked rooms are already gone.
        """
        self.db.execute(
            '''
            UPDATE users SET github_username = NULL, gitter_id = NULL,
                gitter_access_token = NULL
            WHERE matrix_username = ?;
            ''',
            (matrix_user,))
        # TODO: assert no rooms left

    def set_gitter_info(self, matrix_user, github_user, gitter_id,
                        access_token):
        """Receive the Gitter info for an user that completed OAuth.

        Notify the user through Matrix and update the database.
        """
        self.db.execute(
            '''
            UPDATE users SET github_username = ?, gitter_id = ?,
                gitter_access_token = ?
            WHERE matrix_username = ?;
            ''',
            (github_user, gitter_id, access_token, matrix_user))
        self.matrix.gitter_info_set(self.get_user(github_user=github_user))

    def set_user_private_matrix_room(self, matrix_user, room):
        """Set a user's private Matrix room in the database.
        """
        self.db.execute(
            '''
            INSERT OR IGNORE INTO users(matrix_username)
            VALUES(?);
            ''',
            (matrix_user,))
        cur = self.db.execute(
            '''
            SELECT matrix_private_room FROM users
            WHERE matrix_username = ?;
            ''',
            (matrix_user,))
        try:
            prev_room = next(cur)[0]
        except StopIteration:
            prev_room = None
        self.db.execute(
            '''
            UPDATE users SET matrix_private_room = ?
            WHERE matrix_username = ?;
            ''',
            (room, matrix_user))
        return prev_room

    def forget_private_matrix_room(self, room):
        """Forget a Matrix room that was someone's private room.
        """
        self.db.execute(
            '''
            UPDATE users SET matrix_private_room = NULL
            WHERE matrix_private_room = ?;
            ''',
            (room,))

    def get_gitter_user_rooms(self, user_obj):
        """List the Gitter rooms a user is in.

        The user is in these on Gitter and not necessarily through Matrix.
        """
        d = self.gitter.get_gitter_user_rooms(user_obj)
        d.addCallback(self._join_user_rooms, user_obj)
        return d

    def _join_user_rooms(self, rooms, user_obj):
        # Get the rooms the user is in
        user_rooms = dict(
            (row['gitter_room_id'], row['matrix_room'])
            for row in iter(self.db.execute(
                '''
                SELECT matrix_room, gitter_room_id FROM rooms
                WHERE user = ?;
                ''',
                (user_obj.matrix_username,))))

        return [(gitter_id, gitter_name, user_rooms.get(gitter_id))
                for gitter_id, gitter_name in rooms]

    def peek_gitter_room(self, user_obj, gitter_room_name):
        """Get info on a Gitter room without joining it.
        """
        return self.gitter.get_room(gitter_room_name, user=user_obj)

    def join_gitter_room(self, user_obj, gitter_room_id):
        """Join a Gitter room.

        This happens on Gitter only and does not mean the room becomes linked.
        """
        return self.gitter.join_room(user_obj, gitter_room_id)

    def leave_gitter_room(self, user_obj, gitter_room):
        """Leave a Gitter room.

        This assumes the room is not longer linked for the user.
        """
        return self.gitter.leave_room(user_obj, gitter_room)
        # TODO: assert no room

    def virtualuser_exists(self, matrix_user):
        """Indicate if a virtual Matrix user was already created.
        """
        cur = self.db.execute(
            '''
            SELECT matrix_username FROM virtual_users
            WHERE matrix_username = ?;
            ''',
            (matrix_user,))
        try:
            next(cur)
        except StopIteration:
            return False
        else:
            return True

    def add_virtualuser(self, matrix_user):
        """Add a virtual Matrix user to the database.
        """
        self.db.execute(
            '''
            INSERT OR IGNORE INTO virtual_users(matrix_username)
            VALUES(?);
            ''',
            (matrix_user,))

    def add_virtualuser_on_room(self, matrix_user, matrix_room):
        self.db.execute(
            '''
            INSERT OR IGNORE INTO virtual_user_rooms(
                matrix_username, matrix_room)
            VALUES(?, ?);
            ''',
            (matrix_user, matrix_room))

    def is_virtualuser_on_room(self, matrix_user, matrix_room):
        cur = self.db.execute(
            '''
            SELECT matrix_username FROM virtual_user_rooms
            WHERE matrix_username = ? AND matrix_room = ?;
            ''',
            (matrix_user, matrix_room))
        try:
            next(cur)
        except StopIteration:
            return False
        else:
            return True

    def gitter_auth_link(self, matrix_user):
        """Get the link a user should visit to authenticate.
        """
        return self.gitter.auth_link(matrix_user)
