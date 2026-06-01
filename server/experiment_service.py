# import sys

# import grpc
# from server.experiment import experiment
# from server.simulation import simulation
# from surimi.v1 import experiment_service_pb2, experiment_service_pb2_grpc
# from common_functions import log_and_abort
# from server.experiment_recorder_manager import experiment_recorder_manager
# from pathlib import Path
# import yaml
# from google.protobuf.json_format import MessageToDict
# import subprocess
# from datetime import datetime


# class ExperimentService(experiment_service_pb2_grpc.ExperimentServiceServicer):
#     def __init__(self, simulation_dictionary, experiment_dictionary):
#         self.simulation_dictionary: dict[str, simulation] = simulation_dictionary  # Will hold the current simulation instance
#         self.experiment_dictionary: dict[str, experiment] = experiment_dictionary  # Will hold the current experiment instance

#     def InitialiseExperiment(self, request: experiment_service_pb2.InitialiseExperimentRequest, context: grpc.ServicerContext):
#         if request.experiment_id in self.experiment_dictionary.keys():
#            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} already exists. Cannot register experiment again.")          

#         print(f"Register Experiment {request.experiment_id} with {len(request.simulation_ids)} simulations")

#         # Create all simulations that are part of this experiment
#         for simulation_id in request.simulation_ids:
#             self.simulation_dictionary[simulation_id] = simulation(experiment_id = request.experiment_id)

#         # Create a new experiment instance and add it to the experiment dictionary
#         experiment_instance = experiment(experiment_recorder_manager(), request.simulation)
#         self.experiment_dictionary[request.experiment_id] = experiment_instance
#         experiment_instance.experiment_recorder_manager.start_experiment(request.experiment_id)
        
#         return experiment_service_pb2.InitialiseExperimentResponse(
#             experiment_id=request.experiment_id
#         )


#     def FinaliseExperiment(self, request: experiment_service_pb2.FinaliseExperimentRequest, context: grpc.ServicerContext):
#         if request.experiment_id not in self.experiment_dictionary.keys():
#            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot finalise experiment.")          

#         print(f"Finalise Experiment {request.experiment_id}")


#         self.experiment_dictionary[request.experiment_id].experiment_recorder_manager.finalise_experiment(request.experiment_id)

#         # Create output directory for the experiment if it doesn't exist    
#         output_directory = Path(__file__).parent.parent.resolve() / Path("experiments") / request.experiment_id
#         # try:
#         #     output_directory.mkdir(parents=True, exist_ok=True)
#         #     print(f"Created simulation directory: {output_directory}")
#         # except FileExistsError:
#         #     print(f"Directory already exists: {output_directory}")
#         # except Exception as e:
#         #     print(f"Directory creation failed: {str(e)}")

#         # write the contract as a yaml file to be used by the netcdf worker
#         contract_path = output_directory / "contract.yaml"
#         with open(contract_path, "w") as f:
#             yaml.dump(MessageToDict(self.simulation_dictionary[request.experiment_id].simulation), f, default_flow_style=False)
#         print(f"Wrote contract to {contract_path}")

#         self.start_netcdf_subprocess(request.experiment_id, self.simulation_dictionary[request.experiment_id].end_date_time)

#         return experiment_service_pb2.FinaliseExperimentResponse(
#             experiment_id=request.experiment_id
#         )

#     def start_netcdf_subprocess(self, experiment_id: str, end_date_time: datetime):
#         subprocess.Popen(
#             [
#                 sys.executable,
#                 "server/netcdf_worker_main.py",
#                 experiment_id,
#                 f"experiments/{experiment_id}/{experiment_id}.bin",
#                 f"experiments/{experiment_id}",
#                 end_date_time.isoformat()
#             ],
#         )
