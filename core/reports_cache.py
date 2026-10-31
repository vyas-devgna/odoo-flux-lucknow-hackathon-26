"""Tiny versioned cache for expensive aggregates. Writers call invalidate(); readers use cached()."""
from django.core.cache import cache

_KEY = "reports:version"


def version():
    v = cache.get(_KEY)
    if v is None:
        v = 1
        cache.set(_KEY, v, None)
    return v


def invalidate():
    try:
        cache.incr(_KEY)
    except ValueError:
        cache.set(_KEY, 2, None)


def cached(name, fn, ttl=60, extra=""):
    key = f"reports:{version()}:{name}:{extra}"
    hit = cache.get(key)
    if hit is None:
        hit = fn()
        cache.set(key, hit, ttl)
    return hit
