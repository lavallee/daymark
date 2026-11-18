"""Profile reads across a service boundary. Service results never choose client extensions."""

from ..models import DomainError
from . import Advice, has_adjustments, has_external_setting, selected_advice
from .profiles import PROFILES


def advice(tasks, name, *, settings, drops, scope, context, brief):
    has_adjustments(settings, drops)
    if name is not None and not isinstance(name, str):
        raise DomainError("invalid", "A profile name must be text")
    options = {"settings": settings, "drops": drops, "scope": scope, "context": context}
    if has_external_setting(settings):
        # Caller-selected code may run on this client. Never turn a service's
        # selection of an active or saved recipe into an instruction to load it.
        if name is None and tasks.engine.profile_read("active") is None:
            execution = "legacy-client"
        elif name in PROFILES:
            execution = "client-preview"
        else:
            raise DomainError(
                "invalid", "Choose a built-in base profile for a client extension preview",
                ["For example, use --profile plain with your explicit --set."],
            )
    else:
        result = tasks.engine.advice(profile=name, now=tasks._now, brief=brief, **options)
        if result is not None:
            return Advice.from_result(result)
        execution = "legacy-client"

    result = selected_advice(tasks, name, **options)
    result.profile_source["execution"] = execution
    return result
