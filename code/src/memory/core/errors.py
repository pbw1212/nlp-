class MemoryDomainError(RuntimeError):
    pass


class MemoryNotFoundError(MemoryDomainError):
    pass


class InvalidLifecycleTransitionError(MemoryDomainError):
    pass


class DuplicateEventConflictError(MemoryDomainError):
    pass


class LegacySchemaDetectedError(MemoryDomainError):
    pass


class MigrationError(MemoryDomainError):
    pass
