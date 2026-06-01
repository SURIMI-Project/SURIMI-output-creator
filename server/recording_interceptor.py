# import grpc
# from experiment_recorder_manager import experiment_recorder_manager
# from surimi.v1.output-creator_service_pb2 import RegisterExperimentRequest

# from message_registry import message_registry


# # WATCH OUT!!!!!! THIS INTERCEPTOR IS NOT USED ANYMORE. IT WAS REPLACED BY THE RECORDING FUNCTIONALITY IN THE SERVICE IMPLEMENTATIONS THEMSELVES. 
# # I WANT TO KEEP THIS BECAUSE MAYBE RECORDING WILL BE MOVED TO THE CONTROLLER

# class recording_interceptor(grpc.ServerInterceptor):
#     """
#     Records ONLY selected request messages while an experiment is active.
#     """

#     def __init__(self, manager: experiment_recorder_manager):
#         self._manager = manager

#     def intercept_service(self, continuation, handler_call_details):
#         handler = continuation(handler_call_details)
#         if handler is None:
#             return None

#         if handler.unary_unary:
#             return grpc.unary_unary_rpc_method_handler(
#                 self._wrap_unary_unary(handler.unary_unary),
#                 request_deserializer=handler.request_deserializer,
#                 response_serializer=handler.response_serializer,
#             )

#         if handler.stream_unary:
#             return grpc.stream_unary_rpc_method_handler(
#                 self._wrap_stream_unary(handler.stream_unary),
#                 request_deserializer=handler.request_deserializer,
#                 response_serializer=handler.response_serializer,
#             )

#         return handler

#     # ---------- wrappers ----------

#     def _wrap_unary_unary(self, handler):
#         def wrapped(request, context):
#             # lifecycle control
#             if isinstance(request, RegisterExperimentRequest):
#                 self._manager.start_experiment(request.experiment_id)

#             # simulation messages
#             elif message_registry.should_record(request):
#                 self._manager.record(
#                     message_registry.frame_type_for(request),
#                     request.SerializeToString(),
#                 )

#             return handler(request, context)
#         return wrapped

#     def _wrap_stream_unary(self, handler):
#         def wrapped(request_iterator, context):
#             def recording_iterator():
#                 for request in request_iterator:
#                     if message_registry.should_record(request):
#                         self._manager.record(
#                             message_registry.frame_type_for(request),
#                             request.SerializeToString(),
#                         )
#                     yield request

#             return handler(recording_iterator(), context)
#         return wrapped