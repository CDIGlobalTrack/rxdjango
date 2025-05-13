# There is a C version of this library, at delta_utils.py,
# with the exact same interface and functionality, it exports
# module as delta_utils_c

def generate_delta(original, instance):
    # Removes from instance all keys that are the same as original.
    # Returns a list containing instance with removed fields, or an
    # empty list if there are no different fields.

    deltas = []
    empty = True
    for key, old_value in original.items():
        if key == 'id' or key.startswith('_'):
            continue
        try:
            new_value = instance[key]
        except KeyError:
            # An exception in a property may have generated
            # an incomplete serialized object.
            # TODO emit a warning
            continue
        if new_value == old_value:
            del instance[key]
        else:
            empty = False

    if not empty:
        deltas.append(instance)

    return deltas
