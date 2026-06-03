

def log_and_abort(context, status_code, message):
    """
    Logs an error message and aborts the gRPC call with the given status code.
    """
    print(f"[Error] {message}")  # Log the error message
    context.abort(status_code, message)  # Abort the gRPC call with the specified status code and message


def require_not_empty(context, value: str, field_name: str):
    """
    Aborts the gRPC call with INVALID_ARGUMENT if value is None or empty.
    """
    if not value:
        log_and_abort(context, __import__('grpc').StatusCode.INVALID_ARGUMENT, f"{field_name} must not be null or empty.")