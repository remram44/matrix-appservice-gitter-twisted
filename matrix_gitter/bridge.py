from itertools import chain
import json
import os
import sqlite3
import sys
from twisted import logger
from twisted.internet import reactor
from twisted.internet.protocol import Protocol, connectionDone
from twisted.python.failure import Failure
from twisted.web.client import Agent

from matrix_gitter.gitter import GitterAPI
from matrix_gitter.matrix import MatrixAPI
from matrix_gitter.utils import Errback


log = logger.Logger()

agent = Agent(reactor)


class User(object):
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


class Room(Protocol):
    def __init__(self, bridge, user, matrix_room,
                 gitter_room_name, gitter_room_id):
        Protocol.__init__(self)
        self.bridge = bridge
        self.user = user
        self.matrix_room = matrix_room
        self.gitter_room_name = gitter_room_name
        self.gitter_room_id = gitter_room_id

        self.start_stream()

    def start_stream(self):
        self.content = []
        # Start stream
        d = self.bridge.gitter.gitter_request(
            'GET',
            'v1/rooms/%s/chatMessages',
            None,
            self.gitter_room_id,
            user=self.user)
        d.addCallback(self._receive_stream)
        d.addErrback(Errback(
            log,
            "Error starting Gitter stream for user {user} room {room}",
            user=self.user.github_username, room=self.gitter_room_name))

    def _receive_stream(self, response):
        log.info("Stream started for user {user} room {room}",
                 user=self.user.github_username, room=self.gitter_room_name)
        response.deliverBody(self)

    def dataReceived(self, data):
        log.info("Data received on stream for user {user} room {room} "
                 "({bytes} bytes)",
                 user=self.user.github_username, room=self.gitter_room_name,
                 bytes=len(data))
        if '\r' in data:
            data = data.split('\r', 1)
            content, self.content = self.content + [data[0]], [data[1]]
            document = ''.join(chain(content, data))
            try:
                json.loads(document)
            except Exception:
                log.failure("Error decoding JSON on stream for user {user} "
                            "room {room}",
                            Failure(*sys.exc_info()),
                            user=self.user.github_username,
                            room=self.gitter_room_name)
            log.info("Got message for user {user} room {room}: {msg!r}",
                     user=self.user.github_username,
                     room=self.gitter_room_name,
                     msg=document)
            # TODO: forward to Matrix
        else:
            self.content.append(data)

    def connectionLost(self, reason=connectionDone):
        log.info("Lost stream for user {user} room {room}",
                 user=self.user.github_username, room=self.gitter_room_name)
        self.start_stream()

    def to_gitter(self, msg):
        pass  # TODO: forward to Gitter

    def destroy(self):
        pass  # TODO: stop connection, update dicts, update database


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
            config['matrix_homeserver_token'])

        gitter_login_url = config['gitter_login_url']
        if gitter_login_url[-1] != '/':
            gitter_login_url += '/'

        self.gitter = GitterAPI(
            self,
            config['gitter_login_port'],
            gitter_login_url,
            config['gitter_oauth_key'],
            config['gitter_oauth_secret'])

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
            user = User.from_row(row)
            matrix_room = row['matrix_room']
            gitter_room_name = row['gitter_room_name']
            gitter_room_id = row['gitter_room_id']
            room = Room(self, user, matrix_room,
                        gitter_room_name, gitter_room_id)
            self.rooms_matrix[matrix_room] = room
            self.rooms_gitter_name[gitter_room_name] = room
            log.info("{matrix} {gitter} {user_m} {user_g}",
                     matrix=matrix_room, gitter=gitter_room_name,
                     user_m=user.matrix_username, user_g=user.github_username)

    @property
    def bot_fullname(self):
        return self.matrix.bot_fullname

    def get_user(self, matrix_user=None, github_user=None):
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

    def get_room(self, matrix_room=None, gitter_room_name=None):
        if matrix_room is not None and gitter_room_name is None:
            return self.rooms_matrix.get(matrix_room)
        elif gitter_room_name is not None and matrix_room is None:
            return self.rooms_gitter_name.get(gitter_room_name)
        else:
            raise TypeError

    def set_gitter_info(self, matrix_user, github_user, gitter_id,
                        access_token):
        self.db.execute(
            '''
            UPDATE users SET github_username = ?, gitter_id = ?,
                gitter_access_token = ?
            WHERE matrix_username = ?;
            ''',
            (github_user, gitter_id, access_token, matrix_user))
        self.matrix.gitter_info_set(self.get_user(github_user=github_user))

    def set_user_private_matrix_room(self, matrix_user, room):
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
        self.db.execute(
            '''
            UPDATE users SET matrix_private_room = NULL
            WHERE matrix_private_room = ?;
            ''',
            (room,))

    def gitter_auth_link(self, matrix_user):
        return self.gitter.auth_link(matrix_user)

    def create_user(self, matrix_user):
        self.db.execute(
            '''
            INSERT OR IGNORE INTO users(matrix_username)
            VALUES(?);
            ''',
            (matrix_user,))
        return self.get_user(matrix_user=matrix_user)

    def get_gitter_user_rooms(self, user_obj):
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

    def join_gitter_room(self, user_obj, gitter_room):
        return self.gitter.join_room(user_obj, gitter_room)

    def leave_gitter_room(self, user_obj, gitter_room):
        return self.gitter.leave_room(user_obj, gitter_room)
