import os
import sqlite3
from twisted import logger

from matrix_gitter.gitter import GitterAPI
from matrix_gitter.matrix import MatrixAPI


log = logger.Logger()


class User(object):
    def __init__(self, matrix_username, matrix_private_room,
                 github_username, gitter_access_token):
        self.matrix_username = matrix_username
        self.matrix_private_room = matrix_private_room
        self.github_username = github_username
        self.gitter_access_token = gitter_access_token

    @staticmethod
    def from_row(row):
        return User(row['matrix_username'], row['matrix_private_room'],
                    row['github_username'], row['gitter_access_token'])


class Bridge(object):
    """Main application object.

    This is a bridge between Matrix and Gitter. It uses the Matrix application
    service on one side to communicate to a homeserver that will act as bridge
    for the users, and the Gitter API on the other side.

    We also use a database to keep information about users and rooms.
    """
    def __init__(self, config):
        create_db = not os.path.exists('database.sqlite3')
        self.db = sqlite3.connect('database.sqlite3')
        self.db.row_factory = sqlite3.Row

        if create_db:
            self.db.execute(
                '''
                CREATE TABLE users(
                    matrix_username TEXT NOT NULL PRIMARY KEY,
                    matrix_private_room TEXT NULL,
                    github_username TEXT NULL,
                    gitter_access_token TEXT NULL);
                ''')
            self.db.execute(
                '''
                CREATE TABLE virtual_users(
                    matrix_username TEXT NOT NULL);
                ''')
            self.db.execute(
                '''
                CREATE TABLE rooms(
                    user INTEGER NOT NULL,
                    matrix_room TEXT NULL,
                    gitter_room TEXT NOT NULL);
                ''')

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

    def get_user(self, matrix_user=None, github_user=None):
        if matrix_user is not None and github_user is None:
            cur = self.db.execute(
                '''
                SELECT * FROM users
                WHERE matrix_username=?;
                ''',
                (matrix_user,))
        elif github_user is not None and matrix_user is None:
            cur = self.db.execute(
                '''
                SELECT * FROM users
                WHERE github_username=?;
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

    def set_gitter_access_token(self, github_user, access_token):
        self.db.execute(
            '''
            UPDATE users SET gitter_access_token=?
            WHERE github_username=?;
            ''',
            (github_user, access_token))
        self.matrix.access_token_set(self.get_user(github_user=github_user))

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
            WHERE matrix_username=?;
            ''',
            (matrix_user,))
        try:
            prev_room = next(cur)[0]
        except StopIteration:
            prev_room = None
        self.db.execute(
            '''
            UPDATE users SET matrix_private_room=?
            WHERE matrix_username=?;
            ''',
            (room, matrix_user))
        return prev_room

    def forget_private_matrix_room(self, room):
        self.db.execute(
            '''
            UPDATE users SET matrix_private_room=NULL
            WHERE matrix_private_room=?;
            ''',
            (room,))

    def matrix_room_exists(self, room):
        cur = self.db.execute(
            '''
            SELECT user FROM rooms
            WHERE matrix_room = ?;
            ''',
            (room,))
        try:
            next(cur)
        except StopIteration:
            return False
        else:
            return True

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
