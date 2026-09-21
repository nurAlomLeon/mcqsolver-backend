"""Firebase Admin SDK initialization and thin messaging helpers.

The Admin SDK is initialized lazily from a service-account JSON file whose
path is provided via the ``FIREBASE_CREDENTIALS_PATH`` environment variable.
The credentials file itself is never read into Django settings or logged;
only the resolved path is used to build the credential object.
"""
import logging
import os

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)

_app = None


class FirebaseNotConfigured(RuntimeError):
    """Raised when FIREBASE_CREDENTIALS_PATH is missing or invalid."""


def get_firebase_app():
    """Return a lazily-initialized, memoized Firebase Admin app instance."""
    global _app
    if _app is not None:
        return _app

    credentials_path = os.environ.get('FIREBASE_CREDENTIALS_PATH')
    if not credentials_path:
        raise FirebaseNotConfigured(
            'FIREBASE_CREDENTIALS_PATH environment variable is not set.'
        )
    if not os.path.isfile(credentials_path):
        raise FirebaseNotConfigured(
            f'FIREBASE_CREDENTIALS_PATH does not point to a file: {credentials_path}'
        )

    try:
        _app = firebase_admin.get_app()
    except ValueError:
        cred = credentials.Certificate(credentials_path)
        _app = firebase_admin.initialize_app(cred)

    return _app


def _build_data(title, body, extra=None):
    """FCM data payloads must be flat string->string maps."""
    data = {'title': title, 'body': body}
    for key, value in (extra or {}).items():
        if value is None:
            continue
        data[str(key)] = str(value)
    return data


def send_topic_notification(topic, title, body, data=None):
    """Send a single notification message to an FCM topic.

    Returns the Firebase message id on success. Raises whatever exception
    the Admin SDK raises on failure so the caller can record it against the
    campaign without swallowing details.
    """
    app = get_firebase_app()
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        topic=topic,
        data=_build_data(title, body, data),
    )
    return messaging.send(message, app=app)


# FCM caps multicast sends at 500 tokens per request.
MULTICAST_BATCH_SIZE = 500


def send_token_notifications(tokens, title, body, data=None):
    """Fan a notification out to explicit device tokens, in batches of 500.

    Returns ``(success_count, failure_count, invalid_tokens)`` where
    ``invalid_tokens`` are tokens Firebase reported as unregistered or
    malformed, so the caller can deactivate them. Only runs from cron/CLI,
    never inside a web request, since large audiences take time.
    """
    app = get_firebase_app()
    token_list = [token for token in tokens if token]
    success_count = 0
    failure_count = 0
    invalid_tokens = []

    for start in range(0, len(token_list), MULTICAST_BATCH_SIZE):
        batch = token_list[start:start + MULTICAST_BATCH_SIZE]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=_build_data(title, body, data),
            tokens=batch,
        )
        response = messaging.send_each_for_multicast(message, app=app)
        success_count += response.success_count
        failure_count += response.failure_count

        for token, result in zip(batch, response.responses):
            if result.success:
                continue
            exception = result.exception
            if isinstance(
                exception,
                (messaging.UnregisteredError, messaging.SenderIdMismatchError),
            ):
                invalid_tokens.append(token)
            elif isinstance(exception, ValueError):
                invalid_tokens.append(token)

    return success_count, failure_count, invalid_tokens
