import enum


class Operation(enum.Enum):
    SAVE = object()
    CREATE = object()
    DELETE = object()


SAVE = Operation.SAVE
CREATE = Operation.CREATE
DELETE = Operation.DELETE
