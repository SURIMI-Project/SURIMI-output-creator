from typing import Any

from server.stream_output-creator import stream_output-creator

from server.binary_reader import binary_reader, recorded_message
from surimi.v1.ecology_consumer_service_pb2 import UpdateBiomassRequest
from surimi.v1.market_provider_service_pb2 import UpdateSalesRequest
from surimi.v1.catch_consumer_service_pb2 import UpdateCatchDispositionRequest
from surimi.v1.regulations_provider_service_pb2 import UpdateFishingActivityRequest
from surimi.v1.species_price_consumer_service_pb2 import UpdateSpeciesPricesRequest

class message_helper:
    @staticmethod
    def class_name(message: Any) -> str:
        return type(message).__name__

    @staticmethod
    def proto_full_name(message: Any) -> str | None:
        descriptor = getattr(message, "DESCRIPTOR", None)
        if descriptor is None:
            return None
        return getattr(descriptor, "full_name", None)

    @staticmethod
    def simulation_id(message: Any) -> str | None:
        if not hasattr(message, "simulation_id"):
            return None

        simulation_id = getattr(message, "simulation_id")
        if not isinstance(simulation_id, str):
            return None

        if simulation_id == "":
            return None

        return simulation_id
    
    @staticmethod
    def month(message: Any) -> str | None:
        if hasattr(message, "date_time"):
           date_time = getattr(message, "date_time").ToDatetime().strftime("%Y-%m-%d")

        elif hasattr(message, "start_date_time"):
            date_time = getattr(message, "start_date_time").ToDatetime().strftime("%Y-%m-%d")

        simulation_id = getattr(message, "simulation_id")
        if not isinstance(simulation_id, str):
            return None

        if simulation_id == "":
            return None

        return simulation_id



class stream_reader :
    def __init__(self, experiment_path: str):
        self.experiment_path = experiment_path


    def read(self):
        reader = binary_reader(self.experiment_path)

        aggretator : stream_output-creator = stream_output-creator(
            simulation_ids=["0026cb5a-35f6-4609-9fc3-8aa0ac5701e2","916a3ae5-2c16-4cd0-a24e-6883300263c2"],
                writer=self.write_to_netcdf
            )

        for record in reader:
            # Deserialize the payload using the message registry
            msg = record.message
            class_name = message_helper.class_name(msg)
            simulation_id = message_helper.simulation_id(msg)
            month = message_helper.month(msg) 

            print(f"{class_name[:39]:<39}{simulation_id} {month}")
            # aggretator.process(
            #     variable=class_name,         # 'catch' | 'sale' | 'biomass'
            #     month=msg.month,               # e.g. datetime or YYYY-MM
            #     simulation_id=simulation_id,  # 1..5
            #     value=record.message,
            # )
            
    def write_to_netcdf(variable, month, mean, p05, p95):
        print(f"writing to netcdf: {variable} {month} {mean} {p05} {p95}")        