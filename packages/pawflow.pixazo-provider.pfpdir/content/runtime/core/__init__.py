class ServiceError(RuntimeError):
    pass


class _ServiceFactory:
    @staticmethod
    def register(_service_class):
        return None


ServiceFactory = _ServiceFactory()
