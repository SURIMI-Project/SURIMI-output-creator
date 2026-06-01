# SURIMI OutputCreator — Copilot Instructions

## Project overview
This is a Python gRPC server that receives simulation statistics from the SURIMI fisheries model and writes them to NetCDF output files. It is deployed as a Docker container and listens on port 5189.

## Tech stack
- **Python** (gRPC server using `grpcio`, interceptors via `grpc-interceptor`)
- **Protobuf / gRPC** — generated stubs are installed as a pip package (`surimi-surimi-protocol-grpc-python`). There are **no `.proto` files in this repo**.
- **NetCDF4 / xarray / numpy** — for writing output files
- **boto3** — S3 upload of results
- **hvac** — HashiCorp Vault for secrets
- **PyYAML**, **python-dotenv**
- **pytest** — integration tests under `integration_tests/`

## Package conventions
- Proto-generated modules are imported from `surimi.v1`, e.g. `from surimi.v1 import output_creator_service_pb2_grpc`
- `surimi` and `surimi.v1` are namespace packages (no `__init__.py`). If Pylance can't find them, create empty `__init__.py` files in `.venv/Lib/site-packages/surimi/` and `.venv/Lib/site-packages/surimi/v1/`.
- Application modules live in `server/` and are imported as `from server.experiment import experiment`.
- `common_functions.py` (at root of `server/`) contains shared helpers like `log_and_abort`.

## Key modules
| File | Purpose |
|---|---|
| `server/app.py` | Entry point — builds and starts the gRPC server |
| `server/output_creator_service.py` | Main gRPC servicer — handles all RPC methods |
| `server/experiment.py` | `experiment` dataclass — holds state per experiment |
| `server/experiment_recorder_manager.py` | Manages binary recording of messages |
| `server/netcdf_worker_main.py` | Subprocess that writes NetCDF files |
| `server/s3_storage.py` | S3 upload helper |
| `server/vault_service.py` | Loads Vault secrets into environment variables |

## gRPC service patterns
- All RPC handlers receive `(request, context: grpc.ServicerContext)`.
- Use `log_and_abort(context, grpc.StatusCode.INVALID_ARGUMENT, "message")` to abort with an error — never raise exceptions directly.
- Validate that `request.experiment_id` exists in `experiment_dictionary` **before** accessing it.
- When creating a new experiment, instantiate `experiment(experiment_id, recorder_manager, simulation)` and insert it into `experiment_dictionary` before reading from it.

## Integration tests
- Test fixtures (JSON files) are under `integration_tests/grpc_messages/<MessageType>/`.
- Each JSON file must be parseable by `google.protobuf.json_format.ParseDict` with `ignore_unknown_fields=False`.
- `quantity` and `value` fields in statistics messages are `DoubleStatistics` message objects (`{ "mean": ..., "p05": ..., "p95": ... }`), not bare floats.

## Updating the gRPC SDK
Install the new version from the Buf Schema Registry:
```bash
pip install surimi-surimi-protocol-grpc-python==<version> --extra-index-url https://buf.build/gen/python
```
Then update `requirements.txt` accordingly.

## Docker
```bash
# Build
docker build -f Dockerfile . -t rikkert242/outputcreator:latest
# Run (gRPC on localhost:5189)
docker run -p 5189:5189 rikkert242/outputcreator:latest
```

## Environment / secrets
- Copy `.env.example` to `.env` and fill in Vault credentials. The `.env` file is not committed.
- `VaultService.LoadVaultSecretsInEnvironmentVariables()` is called at startup to inject secrets.
