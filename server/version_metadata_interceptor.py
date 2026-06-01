import grpc

class version_metadata_interceptor(grpc.ServerInterceptor):
    def __init__(self, version: str):
        self.version = version


    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)

        if handler is None:
            return None

        # Wrap unary-unary and unary-stream handlers
        if handler.unary_unary:
            return grpc.unary_unary_rpc_method_handler(
                self._wrap_unary_unary(handler.unary_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.unary_stream:
            return grpc.unary_stream_rpc_method_handler(
                self._wrap_unary_stream(handler.unary_stream),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        # Pass through other method types unchanged
        return handler

    def _wrap_unary_unary(self, original_handler):
        def wrapper(request, context):
            # Add version as trailing metadata
            context.set_trailing_metadata([
                ("protocol-version", self.version)
            ])
            return original_handler(request, context)
        return wrapper

    def _wrap_unary_stream(self, original_handler):
        def wrapper(request, context):
            context.set_trailing_metadata([
                ("protocol-version", self.version)
            ])
            return original_handler(request, context)
        return wrapper