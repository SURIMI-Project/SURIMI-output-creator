import struct
from dataclasses import dataclass
from typing import Iterator, Optional

from message_registry import message_registry


# Must match recorder.py exactly
#
# <  little-endian
# I  uint32 payload length
# I  uint32 frame type
# q  int64 timestamp ns
# I  uint32 sequence number
# H  uint16 flags
# H  uint16 header crc (unused)
# I  uint32 payload crc32
# I  uint32 reserved
frame_header_struct = struct.Struct("<I I q I H H I I")
frame_header_size = frame_header_struct.size  # 32 bytes

# Safety limit (must be >= grpc.max_receive_message_length)
MAX_PAYLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True)
class recorded_message:
    """
    Fully reconstructed message from the binary log.
    """
    sequence: int
    timestamp_ns: int
    message: object


class binary_reader:
    """
    Sequential, crash-safe reader for experiment binary log files.

    - Streams messages (very low memory usage)
    - Stops cleanly on partial/corrupt tail frames
    - Uses message_registry for deserialization
    """

    def __init__(self, path: str):
        self._path = path

    def __iter__(self) -> Iterator[recorded_message]:
        return self._read()

    def _read(self) -> Iterator[recorded_message]:
        """
        Generator yielding recorded_message objects.

        Reading stops immediately and safely when:
        - EOF is reached
        - A partial header is encountered
        - A partial payload is encountered
        - An invalid payload length is detected
        """

        with open(self._path, "rb") as f:
            while True:
                # --- Read header ---
                header_bytes = f.read(frame_header_size)
                if len(header_bytes) < frame_header_size:
                    # Clean EOF or truncated tail → stop
                    return

                (
                    payload_len,
                    frame_type,
                    timestamp_ns,
                    sequence,
                    flags,
                    header_crc,
                    payload_crc,
                    reserved,
                ) = frame_header_struct.unpack(header_bytes)

                # --- Validate payload length ---
                if payload_len <= 0 or payload_len > MAX_PAYLOAD_SIZE:
                    # Corrupt or invalid frame → stop
                    return

                # --- Read payload ---
                payload = f.read(payload_len)
                if len(payload) < payload_len:
                    # Partial write → stop
                    return

                # --- Deserialize using shared registry ---
                message = message_registry.deserialize(frame_type, payload)
                if message is None:
                    # Unknown or unsupported frame type → skip
                    continue

                yield recorded_message(
                    sequence=sequence,
                    timestamp_ns=timestamp_ns,
                    message=message,
                )