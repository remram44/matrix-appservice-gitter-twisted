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
