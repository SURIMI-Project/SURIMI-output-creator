import os

from concurrent import futures
import grpc
from server.experiment import experiment
from server.exception_metadata_interceptor import exception_metadata_interceptor
from server.version_metadata_interceptor import version_metadata_interceptor
from server.output_creator_service import OutputCreatorService
from surimi.v1 import output_creator_service_pb2_grpc, output_creator_service_pb2
from vault_service import VaultService
from dotenv import load_dotenv
from importlib.metadata import version, PackageNotFoundError
from grpc_reflection.v1alpha import reflection

def serve():
    load_dotenv()  # Load environment variables from .env file
    
    experiment_dictionary: dict[str, experiment] = {}   # This dictionary will hold the current experiment instance, keyed by experiment_id
    VaultService.LoadVaultSecretsInEnvironmentVariables()  # Load secrets from Vault into environment variables

    # Print all environment variables
    print("=" * 80)
    print("Environment Variables:")
    print("=" * 80)
    for key, value in sorted(os.environ.items()):
        print(f"{key}: {value}")
    print("=" * 80)


    # manager = experiment_recorder_manager()


    version = load_version("surimi_surimi_protocol_grpc_python")
    print("Starting gRPC Server... with protocol version:", version)

    # Create the server with 100MB message size limit
    max_message_length = 100 * 1024 * 1024  # 100MB
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[exception_metadata_interceptor(), version_metadata_interceptor(version)], #, recording_interceptor(manager)],
        options=[
            ('grpc.max_send_message_length', max_message_length),
            ('grpc.max_receive_message_length', max_message_length),
        ]
    )

    output_creator_service = OutputCreatorService(experiment_dictionary, version)  # Instantiate the output creator service
    output_creator_service_pb2_grpc.add_OutputCreatorServiceServicer_to_server(output_creator_service, server)

    # the reflection service will be aware of "OutputCreatorService" and "ServerReflection" services.
    SERVICE_NAMES = (
        output_creator_service_pb2.DESCRIPTOR.services_by_name['OutputCreatorService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)

    # Bind the server to a port
    server.add_insecure_port("[::]:5189")
    server.start()
    print("[OK] gRPC Server running on port 5189")

    # Wait for the server to stop
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n[INFO] Server shutting down...")

def load_version(package_name: str) -> str:
    try:
        # Example: "1.78.10101.47+7fb5c33e314a"
        package_version = version(package_name)
        return package_version.split("+", 1)[1] if "+" in package_version else package_version
    except PackageNotFoundError:
        return "unknown"
        
if __name__ == "__main__":
    serve()