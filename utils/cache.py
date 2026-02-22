"""
Cache semplice in memoria.
"""

_cache: dict = {}


def cache_get(key: str):
    return _cache.get(key)


def cache_set(key: str, value):
    _cache[key] = value


def cache_delete(key: str):
    _cache.pop(key, None)


def cache_clear():
    _cache.clear()
