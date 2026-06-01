# import grpc
# from surimi.v1 import regulations_provider_service_pb2, regulations_provider_service_pb2_grpc
# from common_functions import log_and_abort
# from server.experiment import experiment
# from server.experiment_recorder_manager import experiment_recorder_manager
# from server.message_registry import message_registry
# from server.simulation import simulation

# class RegulationsProviderService(regulations_provider_service_pb2_grpc.RegulationsProviderServiceServicer):
#     def __init__(self, simulation_dictionary: dict[str, simulation], experiment_dictionary: dict[str, experiment]):
#         self.simulation_dictionary: dict[str, simulation] = simulation_dictionary  # Will hold the current simulation instance
#         self.experiment_dictionary: dict[str, experiment] = experiment_dictionary  # Will hold the current experiment instance

#     def UpdateFishingActivity(self, request: regulations_provider_service_pb2.UpdateFishingActivityRequest, context: grpc.ServicerContext):
#         if not request.simulation_id in self.simulation_dictionary.keys():
#             log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Simulation Id {request.simulation_id} not known. Cannot update fishing activity.")

#         print(f"Update Fishing Activity for simulation {request.simulation_id} ")

#         experiment_id = self.simulation_dictionary[request.simulation_id].experiment_id
#         if experiment_id not in self.experiment_dictionary:
#             log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update fishing activity.")

#         manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
#         manager.record(
#             message_registry.frame_type_for(request),
#             request.SerializeToString(),
#         )

#         return regulations_provider_service_pb2.UpdateFishingActivityResponse(
#             simulation_id=request.simulation_id
#         )
