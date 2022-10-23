class ModelNotRegistered(Exception):
    def __init__(self, model):
        self.model = model
        super().__init__(f'{model} is not registered')

class ProgrammingError(Exception):
    pass
