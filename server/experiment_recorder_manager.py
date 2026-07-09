import logging
import os
import threading
from binary_recorder import binary_recorder

class experiment_recorder_manager:
    """
    Controls recorder lifetime per experiment.
    Exactly one experiment at a time.
    """

    def __init__(self, base_dir: str = "experiments"):
        self._base_dir = base_dir
        self._lock = threading.Lock()
        self._recorder = None
        self._active_experiment_id = None

    def start_experiment(self, experiment_id: str):
        with self._lock:
            if self._recorder is not None:
                raise RuntimeError("experiment already active")

            os.makedirs(os.path.join(self._base_dir, experiment_id), exist_ok=True)
            path = os.path.join(self._base_dir, f"{experiment_id}/{experiment_id}.bin")

            self._recorder = binary_recorder(path)
            self._active_experiment_id = experiment_id

            logging.info(f"[recorder] started experiment {experiment_id}, writing to {os.path.abspath(path)}")

    def finalise_experiment(self, experiment_id: str):
        with self._lock:
            if self._active_experiment_id != experiment_id:
                logging.info(
                    f"[recorder] ignoring finalise for {experiment_id} "
                    f"(active={self._active_experiment_id})"
                )
                return

            self._recorder.close()
            self._recorder = None
            self._active_experiment_id = None

            logging.info(f"[recorder] finalised experiment {experiment_id}")

    def cancel_experiment(self, experiment_id: str):
        with self._lock:
            if self._active_experiment_id != experiment_id:
                logging.info(
                    f"[recorder] ignoring cancel for {experiment_id} "
                    f"(active={self._active_experiment_id})"
                )
                return

            bin_path = os.path.join(self._base_dir, experiment_id, f"{experiment_id}.bin")
            exp_dir  = os.path.join(self._base_dir, experiment_id)

            self._recorder.close()
            self._recorder = None
            self._active_experiment_id = None

            try:
                os.remove(bin_path)
                logging.info(f"[recorder] deleted partial recording: {bin_path}")
            except OSError as e:
                logging.error(f"[recorder] could not delete recording for {experiment_id}: {e}")

            try:
                os.rmdir(exp_dir)   # only succeeds if directory is now empty
            except OSError:
                pass

            logging.info(f"[recorder] cancelled experiment {experiment_id}")

    def record(self, frame_type: int, payload: bytes):
        with self._lock:
            if self._recorder is None:
                return
            self._recorder.record(frame_type, payload)

