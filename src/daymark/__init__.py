"""Daymark: tasks, plans and time for work and life, independent of any application."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import Client as Client
    from .errors import ClientError as ClientError
    from .errors import DomainError as DomainError
    from .models import Entity as Entity
    from .models import Recurrence as Recurrence
    from .models import Temporal as Temporal
    from .store import Store as Store
    from .tasks import Tasks as Tasks

__version__ = "0.8.2"

# Metadata and CLI discovery can run without initializing the validation models.
_EXPORTS = {
    "Client": "client",
    "ClientError": "errors",
    "DomainError": "errors",
    "Entity": "models",
    "Recurrence": "models",
    "Store": "store",
    "Tasks": "tasks",
    "Temporal": "models",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_EXPORTS[name]}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()).union(__all__))
