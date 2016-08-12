Matrix-Gitter bridge using Twisted
==================================

This is a Python 2 application using `Twisted <https://twistedmatrix.com>`__ that bridges the `Matrix <https://matrix.org/>`__ chat network with the `Gitter <https://gitter.im/>`__ system.

This is supposed to be deployed as a Matrix application service alongside a homeserver. It allows users to log in to their personal Gitter accounts and chat in Gitter rooms via their Matrix client.

Contrary to other bridges, this doesn't link a public Matrix room with a Gitter one. You won't be able to join a Gitter room without a Gitter account. On the other hand, Gitter users won't see the difference between a Matrix user and a normal Gitter user, since they will appear to be chatting natively.

User experience
---------------

Interaction happens through a bot. Just start chatting with ``@gitter:gitter.remram.fr``, and it will give you a link to log in to your Gitter account. Then the bot will invite you to the Gitter rooms you are already in. Those are private rooms, that do NOT have an alias like ``#gitterHQ/gitter``.

The user can join or leave Gitter rooms by sending commands to the bot::

    join gitterHQ/gitter
    leave gitterHQ/sandbox

Current status
--------------

This is still work-in-progress. The Gitter side is not implemented yet, so this doesn't do anything useful out of the box.

Deployment guide
----------------

- Install and setup a homeserver
- Write a registration file for this application service
- Copy ``settings.py.example`` to ``settings.py`` and edit the configuration
- Run this software

Internals
---------

The database is created on the first runs. It contains the following tables:

- users: contains informations about a user on either service. It might be a Matrix user that did not authenticate with Gitter yet, a Gitter user that doesn't use Matrix, or an active bridge user. The table contains usernames, Gitter OAuth tokens, a flag indicating if the Matrix user is real, and the ID of the private chat room of the bot with the Matrix user.

- rooms: contains information about bridged rooms. Linked to a Matrix user. Maps a Matrix room ID with a Gitter room ID. The Matrix room may be NULL if the user is only in the room om Gitter.

The bot responds to invite requests. When it joins, if more than one persom is in the chat, it will print a message and leave (and remember not to accept invites for that room in the future). Else, it will set this room as the private chat with that user in the database, leaving the previous one if it was set, and display instructions (with link to auth page).

The auth page is an HTML page allowing a user to auth her Gitter account using OAuth2.

Recognized bot commands:

- ``list``: displays the list of Gitter room the user is in, with an asterix for rooms he has joined on Matrix.
- ``gjoin <gitter-room>``: join a new room on Gitter
- ``gpart <gitter-room>``: leave a room on Gitter. Kick you out of the Matrix room if you were on it
- ``invite <gitter-room>``: if you are not on a Matrix room for that Gitter room, create one, populate it with virtual users and invite you to it
- ``logout``: throw away your Gitter credentials. Kick you out of all rooms you are in
