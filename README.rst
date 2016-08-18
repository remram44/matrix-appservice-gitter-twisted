..  image:: https://remram44.github.io/matrix-appservice-gitter-twisted/img/matrix-badge.svg
    :target: https://vector.im/beta/#/room/#gitter-twisted:matrix.org

Matrix-Gitter bridge using Twisted
==================================

This is a Python 2 application using `Twisted <https://twistedmatrix.com>`__ that bridges the `Matrix <https://matrix.org/>`__ chat network with the `Gitter <https://gitter.im/>`__ system.

This is supposed to be deployed as a Matrix application service alongside a homeserver. It allows users to log in to their personal Gitter accounts and chat in Gitter rooms via their Matrix client.

Contrary to other bridges, this doesn't link a public Matrix room with a Gitter one. You won't be able to join a Gitter room without a Gitter account. On the other hand, Gitter users won't see the difference between a Matrix user and a normal Gitter user, since they will appear to be chatting natively.

User experience
---------------

Interaction happens through a bot. Just start chatting with ``@gitter:gitter.remram.fr``, and it will give you a link to log in to your Gitter account. Then the bot will invite you to the Gitter rooms you are already in. Those are private rooms, that do NOT have an alias like ``#gitterHQ/gitter``.

The user can join or leave Gitter rooms by sending commands to the bot.

Current status
--------------

This works but is still in development. While you are welcome to use it, expect bugs and do not rely on it in a production environment. Feedback is appreciated.

Deployment guide
----------------

- Install and setup a homeserver
- Sign up for a Gitter application on https://developer.gitter.im/apps; your URL should be your server hostname (``gitter_login_url`` in settings) with path ``/callback``, example ``https://gitter.remram.fr/callback``.
- Write a registration file for this application service, based on this:

  .. code-block:: yaml

    id: gitter                      # An identifier, unique within your appservices
    hs_token: "changeme42changeme"  # Token you will set in the settings as matrix_homeserver_token
    as_token: "changeme42changeme"  # Token you will set in the settings as matrix_appservice_token
    namespaces:
      users:
        - exclusive: true
          regex: '@gitter.*'        # You can't change this currently
      aliases: []
      rooms: []
    url: 'https://127.0.0.1:8445'   # URL of your appservice; probably local. Port should match matrix_appservice_port
    sender_localpart: gitter        # Has to fall within user namespace regex, and match matrix_botname in settings

- Create ``settings.py`` and edit the configuration

  .. code-block:: python

    unique_secret_key = 'change this before running'    # A unique secret string for HMAC

    matrix_appservice_port = 8445                       # Should match url in registration
    matrix_homeserver_url = 'https://127.0.0.1:8448/'   # URL of your homeserver, usually local
    matrix_homeserver_domain = 'gitter.remram.fr'       # The domain your homeserver uses
    matrix_botname = '@gitter:gitter.remram.fr'         # Should be sender_localpart in registration + domain
    matrix_appservice_token = 'changeme42changeme'      # as_token from registration
    matrix_homeserver_token = 'changeme42changeme'      # hs_token from registration

    gitter_login_port = 80                              # Port the OAuth webapp is listening on. Should match
                                                        # gitter_login_url, unless you have a reverse proxy in the middle
    gitter_login_url = 'http://gitter.remram.fr/'       # URL sent to users to register. When registering your
                                                        # Gitter app, use this + /callback as redirect URL
    gitter_oauth_key = 'get this from Gitter'           # Key for your registered Gitter app
    gitter_oauth_secret = 'get this from Gitter'        # Secret for your registered Gitter app

- Run this software; it will create a file ``database.sqlite`` in the current folder, so make sure it has permission to do that.

Internals
---------

The database is created on the first runs. It contains the following tables:

- users: contains informations about a user on either service. It might be a Matrix user that did not authenticate with Gitter yet, or an active bridge user. The table contains usernames, Gitter OAuth tokens, and the ID of the private chat room of the bot with the Matrix user.

- rooms: contains information about bridged rooms. Linked to a Matrix user. Maps a Matrix room ID with a Gitter room name and ID.

The bot responds to invite requests. When it joins, if more than one persom is in the chat, it will print a message and leave (and remember not to accept invites for that room in the future). Else, it will set this room as the private chat with that user in the database, leaving the previous one if it was set, and display instructions (with link to auth page).

The auth page is an HTML page allowing a user to auth her Gitter account using OAuth2.

Recognized bot commands:

- ``list``: displays the list of Gitter room the user is in, with an asterix for rooms he has joined on Matrix.
- ``gjoin <gitter-room>``: join a new room on Gitter
- ``gpart <gitter-room>``: leave a room on Gitter. Kick you out of the Matrix room if you were on it
- ``invite <gitter-room>``: if you are not on a Matrix room for that Gitter room, create one, populate it with virtual users and invite you to it
- ``logout``: throw away your Gitter credentials. Kick you out of all rooms you are in
