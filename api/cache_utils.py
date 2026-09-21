"""Shared caching helpers for the read-heavy API endpoints.

Design notes
------------
* **Fail open.** Every helper swallows cache backend errors and behaves like a
  permanent cache miss. If Redis disappears the API keeps serving from the
  database instead of returning 500s.
* **Token based invalidation.** Cached payloads are keyed by a random token
  stored under a namespace key. Writing new content replaces the token, which
  orphans every key built from the old one instead of having to enumerate and
  delete them. Orphans die off with their own TTL.
* **Response payloads only.** We cache already-serialized payloads exactly as
  the uncached code path produced them, so the wire format never changes and
  older app builds keep working.
"""

import hashlib
import logging
from uuid import uuid4

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Bump this when the *shape* of a cached payload changes so that entries
# written by an older deploy are ignored rather than served to clients.
CACHE_SCHEMA = 'v1'

# --- Namespaces -----------------------------------------------------------
# Group related entries so a single write can invalidate all of them.
NS_CATEGORY = 'category'
NS_QUIZ_CATEGORY = 'quizcategory'
NS_QUESTION = 'question'
NS_QUIZ = 'quiz'
NS_MODEL_TEST = 'modeltest'
NS_APP_UPDATE = 'appupdate'
NS_COURSE = 'course'
NS_COURSE_HOME = 'coursehome'

# --- TTLs (seconds) -------------------------------------------------------
TTL_LONG = 60 * 60 * 12   # near-static reference data (category trees)
TTL_MEDIUM = 60 * 30      # content that only staff can change
TTL_SHORT = 60            # per-user dashboards

# Namespace tokens must outlive the payloads they guard.
_TOKEN_TTL = None  # never expire

_MISS = object()


def _token_key(namespace, scope=None):
    if scope is None:
        return f'{CACHE_SCHEMA}:ns:{namespace}'
    return f'{CACHE_SCHEMA}:ns:{namespace}:{scope}'


def _new_token():
    return uuid4().hex[:12]


def get_token(namespace, scope=None):
    """Return the current token for a namespace, creating one if needed.

    ``scope`` narrows invalidation to a single owner (usually a user id) so one
    user's activity does not flush every other user's cached dashboard.
    """
    key = _token_key(namespace, scope)
    try:
        token = cache.get(key)
        if token is None:
            token = _new_token()
            # ``add`` so concurrent workers agree on the first token written.
            if not cache.add(key, token, _TOKEN_TTL):
                token = cache.get(key) or token
        return token
    except Exception:
        logger.warning('Cache unavailable reading token for %s', key, exc_info=True)
        # A stable fallback keeps key building working; reads/writes below will
        # simply keep missing while the backend is down.
        return 'nocache'


def invalidate(namespace, scope=None):
    """Orphan every cached payload in a namespace by rotating its token."""
    key = _token_key(namespace, scope)
    try:
        cache.set(key, _new_token(), _TOKEN_TTL)
    except Exception:
        logger.warning('Cache unavailable invalidating %s', key, exc_info=True)


def invalidate_many(namespaces):
    for namespace in namespaces:
        invalidate(namespace)


def build_key(namespace, parts, scope=None):
    """Build a cache key from ``parts``, hashing it when it grows too long.

    Scoped keys embed both the namespace-wide token and the scope's own token,
    so ``invalidate(ns)`` flushes every scope while ``invalidate(ns, scope)``
    only flushes that one owner.
    """
    raw = '|'.join(str(part) for part in parts)
    if len(raw) > 120:
        raw = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    token = get_token(namespace)
    if scope is None:
        return f'{CACHE_SCHEMA}:{namespace}:{token}:{raw}'
    return f'{CACHE_SCHEMA}:{namespace}:{token}:{scope}:{get_token(namespace, scope)}:{raw}'


def cache_get(key):
    try:
        value = cache.get(key, _MISS)
    except Exception:
        logger.warning('Cache read failed for %s', key, exc_info=True)
        return _MISS
    return value


def cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:
        logger.warning('Cache write failed for %s', key, exc_info=True)


def cached_payload(namespace, parts, timeout, builder, scope=None):
    """Return a cached payload, building and storing it on a miss.

    The object handed back is never shared with the cache: on a hit the backend
    unpickles a fresh copy, and on a miss it is the object ``builder`` just
    produced. Callers may therefore mutate it safely.
    """
    key = build_key(namespace, parts, scope=scope)
    value = cache_get(key)
    if value is not _MISS:
        return value

    value = builder()
    cache_set(key, value, timeout)
    return value


def request_signature(request):
    """A stable, collision-resistant signature of the request's read inputs.

    Includes the host because DRF builds absolute ``next``/``previous``
    pagination URLs from it.
    """
    query = sorted(
        (key, ','.join(sorted(request.query_params.getlist(key))))
        for key in request.query_params
    )
    query_part = '&'.join(f'{key}={value}' for key, value in query)
    return f'{request.get_host()}|{request.path}|{query_part}'
