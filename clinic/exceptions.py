from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    """
    Wraps DRF's default handler to return a consistent error shape:

        {"error": {"detail": ..., "fields": {...}}}

    `fields` carries per-field validation errors when present (e.g. from
    serializer.errors), so clients can show a message next to the right
    input instead of parsing prose.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    fields = detail if isinstance(detail, dict) else None
    message = "Validation failed." if fields else _flatten(detail)

    response.data = {"error": {"detail": message, "fields": fields}}
    return response


def _flatten(detail):
    if isinstance(detail, list):
        return " ".join(str(d) for d in detail)
    return str(detail)
