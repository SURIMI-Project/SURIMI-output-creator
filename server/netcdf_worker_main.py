import sys
import traceback
import yaml
from datetime import datetime
from pathlib import Path
from google.protobuf.json_format import ParseDict
from binary_reader import binary_reader
from server.surimi_output import OutputType, surimi_output
from surimi.v1 import simulation_pb2
from server.s3_storage import S3_Storage

def main():
    experiment_id = sys.argv[1]
    experiment_path = sys.argv[2]
    output_path = sys.argv[3]
    end_date_time = datetime.fromisoformat(sys.argv[4])

    try:
        print(f"Processing experiment {experiment_id}")

        # read the contract from the yaml file to get the end date time for the netcdf file
        contract_path = Path(__file__).parent.parent / "experiments" / experiment_id / "contract.yaml"
        with open(contract_path, "r") as f:
            contract_dict = yaml.safe_load(f)
        simulation: simulation_pb2.Simulation = ParseDict(contract_dict, simulation_pb2.Simulation())
    
        reader = binary_reader(experiment_path)
        output = surimi_output(simulation, output_path, experiment_id, end_date_time, file_type=OutputType.NET_CDF)

        for record in reader:
            output.handle_message(record.message)

        output.finalise()

        simulation_dir = Path(__file__).parent.parent / "experiments" / experiment_id
        S3_Storage.UploadFilesToS3(str(simulation_dir), f"surimi-output-creator/experiments/{experiment_id}")

        print(f"Finished experiment {experiment_id}")

    except Exception as e:
        print(f"Error processing experiment {experiment_id}: {type(e).__name__}: {str(e)}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()