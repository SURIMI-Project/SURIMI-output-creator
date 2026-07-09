import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from binary_reader import binary_reader
from server.surimi_output import OutputType, surimi_output
from server.logging_formatter import configure_logging
from surimi.v1 import simulation_pb2
from server.s3_storage import S3_Storage

def main():
    configure_logging()
    experiment_id = sys.argv[1]     # The experiment ID is passed as the first command-line argument
    experiment_path = sys.argv[2]   # The path to the binary file containing the recorded protobuf messages for this experiment is passed as the second command-line argument
    output_path = sys.argv[3]       # The path to the output directory where the netCDF file should be written is passed as the third command-line argument
    end_date_time = datetime.fromisoformat(sys.argv[4])  # The end date and time for the netCDF file is passed as the fourth command-line argument
    simulation: simulation_pb2.Simulation = simulation_pb2.Simulation.FromString(bytes.fromhex(sys.argv[5]))  # The simulation protobuf is passed as a hex-encoded serialized protobuf

    try:
        logging.info(f"Processing experiment {experiment_id}")

        reader = binary_reader(experiment_path)

        output = surimi_output(simulation, output_path, experiment_id, end_date_time, file_type=OutputType.NET_CDF)

        for record in reader:
            output.handle_message(record.message)

        output.finalise()

        simulation_dir = Path(__file__).parent.parent / "experiments" / experiment_id
        S3_Storage.UploadFilesToS3(str(simulation_dir), f"output_creator/experiments/{experiment_id}")

        logging.info(f"Finished experiment {experiment_id}")

    except Exception as e:
        logging.error(f"Error processing experiment {experiment_id}: {type(e).__name__}: {str(e)}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()