class UnknownProperty(Exception):
    pass


class AnchorDoesNotExist(Exception):
    pass


class UnauthorizedError(Exception):
    pass


class ForbiddenError(Exception):
    def __init__(self, msg):
        self.message = msg
        super().__init__(msg)


class RxDjangoBug(Exception):
    pass


class ActionNotAsync(Exception):
    pass


class InvalidInstanceError(Exception):
    pass
