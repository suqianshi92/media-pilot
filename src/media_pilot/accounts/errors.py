class AlreadyInitializedError(ValueError):
    pass


class InvalidUsernameError(ValueError):
    pass


class ProtectedAdminError(ValueError):
    pass


class UserDeletionForbiddenError(ValueError):
    pass
