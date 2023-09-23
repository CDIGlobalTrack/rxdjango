from .exceptions import UnknownProperty


def is_related_property(model, property_name):
    key = _make_key(model, property_name)
    return key in RelatedProperty.accessors


def get_accessor(model, property_name):
    return RelatedProperty.get_accessor(model, property_name)


class RelatedProperty:
    accessors = {}
    unknown_properties = set()

    def __init__(self, accessor=None):
        self.accessor = accessor

    def __call__(self, fget):
        self.fget = fget
        key = (fget.__module__, fget.__qualname__)
        RelatedProperty.accessors[key] = self.accessor
        return property(self._get_wrapper())

    def _get_wrapper(self):
        def wrapper(instance):
            return self.fget(instance)
        return wrapper

    @classmethod
    def get_accessor(cls, model, property_name):
        key = _make_key(model, property_name)
        try:
            return cls.accessors[key]
        except KeyError:
            if key in cls.unknown_properties:
                name = '.'.join(key)
                module = cls.__module__
                raise UnknownProperty(
                    f'Unknown property {name}, use {module}.related_property '
                    'decorator instead to provide an acessor'
                )

    @classmethod
    def register_unknown_property(cls, model, property_name):
        key = _make_key(model, property_name)
        cls.unknown_properties.add(key)
        return cls.accessors.get(key)

def _make_key(model, property_name):
    qualname = '.'.join([model.__name__, property_name])
    return (model.__module__, qualname)
