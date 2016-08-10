if __name__ == '__main__':
    try:
        from matrix_gitter.main import main
    except ImportError:
        import os
        import sys

        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from matrix_gitter.main import main
    main()


from twisted.internet import reactor
from twisted.web.server import Site
from twisted.web.resource import Resource


class TransactionRoot(Resource):
    def getChild(self, name, request):
        return Transaction(name)


class Transaction(Resource):
    isLeaf = True

    def __init__(self, transaction):
        Resource.__init__(self)
        self.transaction = transaction

    def render_PUT(self, request):
        request.responseHeaders.addRawHeader(b"content-type",
                                             b"application/json")
        return "transaction: %s\ncontent: %s\n" % (
            self.transaction,
            request.content.read())


def main():
    root = Resource()
    root.putChild("transaction", TransactionRoot())
    factory = Site(root)
    reactor.listenTCP(4805, factory)
    reactor.run()
