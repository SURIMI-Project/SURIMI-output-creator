import logging
import sys
from datetime import datetime
import grpc
from server.message_registry import message_registry
from server.common_functions import log_and_abort, require_not_empty
# from server.stream_reader import stream_reader
from server.experiment import experiment
from surimi.v1 import output_creator_service_pb2_grpc, initialise_experiment_pb2, experiment_step_pb2, finalise_experiment_pb2, cancel_experiment_pb2, get_protocol_version_pb2, update_catch_disposition_statistics_pb2, update_biomass_statistics_pb2, update_sales_statistics_pb2, update_fishing_activity_statistics_pb2, update_species_prices_statistics_pb2, simulation_pb2, update_stock_assessment_pb2
from common_functions import log_and_abort, require_not_empty
from google.protobuf.json_format import MessageToDict
import subprocess
from server.experiment_recorder_manager import experiment_recorder_manager

class OutputCreatorService(output_creator_service_pb2_grpc.OutputCreatorServiceServicer):
    def __init__(self, experiment_dictionary, version: str):
        self.experiment_dictionary: dict[str, experiment] = experiment_dictionary  # Will hold the current experiment instance
        self.version = version

    def InitialiseExperiment(self, request: initialise_experiment_pb2.InitialiseExperimentRequest, context: grpc.ServicerContext):
        require_not_empty(context, request.experiment_id, "experiment_id")
        require_not_empty(context, request.scenario_name, "scenario_name")

        if request.experiment_id in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, 
                      f"Experiment Id {request.experiment_id} is already initialised.")
            return

        end_date_info = ""
        if request.end_date_time.seconds != 0 or request.end_date_time.nanos != 0:
            end_date_info = f" and end date {request.end_date_time.ToDatetime()}"
        logging.info(f"Init experiment {request.experiment_id} for scenario {request.scenario_name} with start date {request.simulation.start_date_time.ToDatetime()} and step size {request.simulation.time_step} and end_date_time {end_date_info}")

        new_exp = experiment(request.experiment_id, experiment_recorder_manager(), request.simulation)
        new_exp.end_date_time = request.end_date_time.ToDatetime()
        self.experiment_dictionary[request.experiment_id] = new_exp

        new_exp.experiment_recorder_manager.start_experiment(request.experiment_id)

        # TODO: If you also capture this message, you don't need to write the contract to a yaml file and read it again in the netcdf worker
        # manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        # manager.record(
        #     message_registry.frame_type_for(request),
        #     request.SerializeToString(),
        # )
    
        return initialise_experiment_pb2.InitialiseExperimentResponse(
            experiment_id=request.experiment_id
        )

    def ExperimentStep(self, request: experiment_step_pb2.ExperimentStepRequest, context: grpc.ServicerContext):
        if request.experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot simulate step.")

        sim = self.experiment_dictionary[request.experiment_id].simulation

        if sim.time_step != "P1M":
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Error in experiment {request.experiment_id}: Only monthly steps are supported. Please set the step size to P1M.")

        logging.info(f"SimulateStep for experiment {request.experiment_id}")

        # sim.netcdf_file_instance.simulate_step(request)

        return experiment_step_pb2.ExperimentStepResponse(
            experiment_id=request.experiment_id
        )

    def FinaliseExperiment(self, request: finalise_experiment_pb2.FinaliseExperimentRequest, context: grpc.ServicerContext):
        # TESTING.....................


        # reader = stream_reader(f"experiments/3e8fc93a-a7a2-4abe-ba04-089559547068/3e8fc93a-a7a2-4abe-ba04-089559547068.bin")
        # reader.read()
    
        # END OF TESTING.....................

        if request.experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Simulation Id {request.experiment_id} not known. Cannot finalise.")

        logging.info(f"Finalise for simulation {request.experiment_id}")
        self.experiment_dictionary[request.experiment_id].isFinalised = True

        self.experiment_dictionary[request.experiment_id].experiment_recorder_manager.finalise_experiment(request.experiment_id)

        # check if this is the last simulation of the experiment to finish, if so, we can finalise the experiment and upload the results to S3. We check this by looking at the end date of the simulation, if it is in the past, it means that this simulation has finished all its steps and is now being finalised. If there are other simulations that are not yet finalised and have an end date in the past, it means that they have also finished their steps but are not yet finalised, so we should not finalise the experiment yet.
        experiment_id = self.experiment_dictionary[request.experiment_id].experiment_id

        self.start_netcdf_subprocess(experiment_id, self.experiment_dictionary[request.experiment_id].end_date_time, self.experiment_dictionary[request.experiment_id].simulation)

        return finalise_experiment_pb2.FinaliseExperimentResponse(
            experiment_id=request.experiment_id
        )

    def CancelExperiment(self, request: cancel_experiment_pb2.CancelExperimentRequest, context: grpc.ServicerContext):
        if request.experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Simulation Id {request.experiment_id} not known. cannot cancel.")

        logging.info(f"Cancel for simulation {request.experiment_id}")

        exp = self.experiment_dictionary[request.experiment_id]
        exp.experiment_recorder_manager.cancel_experiment(request.experiment_id)
        del self.experiment_dictionary[request.experiment_id]

        return cancel_experiment_pb2.CancelExperimentResponse(
            experiment_id=request.experiment_id
        )

    def GetProtocolVersion(self, request: get_protocol_version_pb2.GetProtocolVersionRequest, context: grpc.ServicerContext):
        return get_protocol_version_pb2.GetProtocolVersionResponse(
            protocol_version = self.version 
        )


    def UpdateCatchDispositionStatistics(self, request: update_catch_disposition_statistics_pb2.UpdateCatchDispositionStatisticsRequest, context: grpc.ServicerContext):
        if not request.experiment_id in self.experiment_dictionary.keys():
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Simulation Id {request.experiment_id} not known.")

        logging.info(f"Update CatchDisposition for simulation {request.experiment_id} ")

        # print(request)  # for debugging purposes

        experiment_id = self.experiment_dictionary[request.experiment_id].experiment_id
        if experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update catch disposition.")

        manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        manager.record(
            message_registry.frame_type_for(request),
            request.SerializeToString(),
        )

        return update_catch_disposition_statistics_pb2.UpdateCatchDispositionStatisticsResponse(
            experiment_id=request.experiment_id
        )

    def UpdateBiomassStatistics(self, request : update_biomass_statistics_pb2.UpdateBiomassStatisticsRequest, context):
        if request.experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Simulation Id {request.experiment_id} not known. Cannot update biomass.")

        logging.info(f"Update biomass for simulation {request.experiment_id}")
        # print(request)  # for debugging purposes

        experiment_id = self.experiment_dictionary[request.experiment_id].experiment_id
        if(experiment_id not in self.experiment_dictionary):
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update biomass.")

        manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        manager.record(
            message_registry.frame_type_for(request),
            request.SerializeToString(),
        )
        return update_biomass_statistics_pb2.UpdateBiomassStatisticsResponse(
            experiment_id=request.experiment_id
        )

    def UpdateFishingActivityStatistics(self, request: update_fishing_activity_statistics_pb2.UpdateFishingActivityStatisticsRequest, context: grpc.ServicerContext):
        if not request.experiment_id in self.experiment_dictionary.keys():
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot update fishing activity.")

        logging.info(f"Update Fishing Activity for experiment {request.experiment_id} ")

        experiment_id = self.experiment_dictionary[request.experiment_id].experiment_id
        if experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update fishing activity.")

        manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        manager.record(
            message_registry.frame_type_for(request),
            request.SerializeToString(),
        )

        return update_fishing_activity_statistics_pb2.UpdateFishingActivityStatisticsResponse(
            experiment_id=request.experiment_id
        )
    
    def UpdateSalesStatistics(self, request: update_sales_statistics_pb2.UpdateSalesStatisticsRequest, context: grpc.ServicerContext):
        if not request.experiment_id in self.experiment_dictionary.keys():
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot update sales.")

        logging.info(f"Update Sales for experiment {request.experiment_id} ")

        # print(request)  # for debugging purposes

        experiment_id = self.experiment_dictionary[request.experiment_id].experiment_id
        if experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update sales.")

        manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        manager.record(
            message_registry.frame_type_for(request),
            request.SerializeToString(),
        )

        return update_sales_statistics_pb2.UpdateSalesStatisticsResponse(
            experiment_id=request.experiment_id
        )
    
    def UpdateSpeciesPriceStatistics(self, request: update_species_prices_statistics_pb2.UpdateSpeciesPriceStatisticsRequest, context: grpc.ServicerContext):
        if not request.experiment_id in self.experiment_dictionary.keys():
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot update species prices.")

        logging.info(f"Update SpeciesPrices for experiment {request.experiment_id} ")

        # print(request)  # for debugging purposes

        experiment_id = request.experiment_id
        if experiment_id not in self.experiment_dictionary:
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update species prices.")

        manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        manager.record(
            message_registry.frame_type_for(request),
            request.SerializeToString(),
        )

        return update_species_prices_statistics_pb2.UpdateSpeciesPriceStatisticsResponse(
            experiment_id=request.experiment_id
        )

    def UpdateStockAssessment(self, request: update_stock_assessment_pb2.UpdateStockAssessmentRequest, context: grpc.ServicerContext):
        if not request.experiment_id in self.experiment_dictionary.keys():
            log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {request.experiment_id} not known. Cannot update stock assessment.")

        logging.info(f"Update StockAssessment for experiment {request.experiment_id} ")

        logging.info(str(request))  # for debugging purposes

        # experiment_id = request.experiment_id
        # if experiment_id not in self.experiment_dictionary:
        #     log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"Experiment Id {experiment_id} not known. Cannot update stock assessment.")

        # manager : experiment_recorder_manager = self.experiment_dictionary[experiment_id].experiment_recorder_manager
        # manager.record(
        #     message_registry.frame_type_for(request),
        #     request.SerializeToString(),
        # )

        return update_stock_assessment_pb2.UpdateStockAssessmentResponse(
            experiment_id=request.experiment_id
        )

    def start_netcdf_subprocess(self, experiment_id: str, end_date_time: datetime, simulation: simulation_pb2.Simulation):
        subprocess.Popen(
            [
                sys.executable,
                "server/netcdf_worker_main.py",
                experiment_id,
                f"experiments/{experiment_id}/{experiment_id}.bin",
                f"experiments/{experiment_id}",
                end_date_time.isoformat(),
                simulation.SerializeToString().hex(),
            ],
        )