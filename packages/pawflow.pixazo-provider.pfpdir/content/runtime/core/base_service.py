class BaseService:
    def __init__(self, config):
        self.config = dict(config or {})
        self._connection = None

    def connect(self):
        if self._connection is None:
            self._connection = self._create_connection()
        return self._connection

    def ensure_connected(self):
        return self.connect()

    def disconnect(self):
        if self._connection is not None:
            self._close_connection()
        self._connection = None
