import os
import sqlite3

from matrix_gitter.gitter import GitterAPI
from matrix_gitter.matrix import MatrixAPI


class Bridge(object):
    """Main application object.

    This is a bridge between Matrix and Gitter. It uses the Matrix application
    service on one side to communicate to a homeserver that will act as bridge
    for the users, and the Gitter API on the other side.

    We also use a database to keep information about users and rooms.
    """
    def __init__(self, config):
        create_db = not os.path.exists('database.sqlite3')
        conn = sqlite3.connect('database.sqlite3')
        conn.row_factory = sqlite3.Row

        if create_db:
            conn.execute(
                '''
                CREATE TABLE users(
                    id INTEGER NOT NULL PRIMARY KEY,
                    matrix_username TEXT NULL,
                    matrix_displayname TEXT NULL,
                    github_username TEXT NULL,
                    github_displayname TEXT NULL);
                ''')

        self.matrix = MatrixAPI(
            self,
            config['matrix_appservice_port'],
            config['matrix_homeserver_url'],
            config['matrix_homeserver_domain'],
            config['matrix_botname'],
            config['matrix_appservice_token'],
            config['matrix_homeserver_token'])

        self.gitter = GitterAPI(self)
