from typing import Any, Callable
import grpc
import traceback
from grpc_interceptor import ServerInterceptor
from grpc_interceptor.exceptions import GrpcException

class exception_metadata_interceptor(ServerInterceptor):

    def intercept(
        self,
        method: Callable,
        request_or_iterator: Any,
        context: grpc.ServicerContext,
        method_name: str,
    ) -> Any:

        try:
            return method(request_or_iterator, context)
        except GrpcException as rpc_error:  # Catch gRPC-specific exceptions
            print(f"🔴 GrpcException in {method_name}: {rpc_error.details()}")
            print(f"   Status Code: {rpc_error.code()}")
            raise

        except Exception as e:
            print(f"🔴 Exception in {method_name}: {type(e).__name__}: {str(e)}")
            print(f"   Traceback:")
            traceback.print_exc()

            metadata = [
             ('method', method_name),
             ('application', 'output-creator')
            ]

            context.set_trailing_metadata(metadata)
            msg = getattr(e, "message", None) or str(e) or context.details() or "no message"
            raise GrpcException(grpc.StatusCode.INTERNAL, "Error in output-creator: " +msg)



