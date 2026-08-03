from __future__ import annotations

from app.domains.chapter_review import _prompt_legacy as _implementation


for _value in vars(_implementation).values():
    _module_name = getattr(_value, "__module__", None)
    if _module_name == _implementation.__name__ or (
        isinstance(_module_name, str)
        and _module_name.startswith("app.domains.chapter_review.")
    ):
        try:
            _value.__module__ = __name__
        except (AttributeError, TypeError):
            pass

globals().update(
    {
        _name: _value
        for _name, _value in vars(_implementation).items()
        if not _name.startswith("__")
    }
)

__all__ = [name for name in globals() if not name.startswith("_")]
