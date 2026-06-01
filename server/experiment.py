import datetime

from server.experiment_recorder_manager import experiment_recorder_manager
from surimi.v1 import simulation_pb2

class experiment:
    def __init__(self, experiment_id: str, experiment_recorder_manager, simulation: simulation_pb2.Simulation):
        self.experiment_recorder_manager = experiment_recorder_manager
        self.simulation: simulation_pb2.Simulation = simulation

        self.experiment_id: str = experiment_id
        self.end_date_time: datetime = None
        self.isFinalised: bool = False
