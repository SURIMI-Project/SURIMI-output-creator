import threading
import struct
import time
import queue
import zlib

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


class binary_recorder:
    """
    Append-only, background-threaded binary recorder.
    """

    def __init__(self, path: str):
        self._queue = queue.SimpleQueue()
        self._stop_event = threading.Event()
        self._sequence = 0

        self._file = open(path, "ab", buffering=0)
        self._thread = threading.Thread(
            target=self._writer_loop,
            daemon=True
        )
        self._thread.start()

    def record(self, frame_type: int, payload: bytes):
        timestamp_ns = time.time_ns()
        seq = self._sequence
        self._sequence += 1

        self._queue.put((frame_type, payload, timestamp_ns, seq))

    def _writer_loop(self):
        while not self._stop_event.is_set():
            try:
                frame_type, payload, ts, seq = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            payload_crc = zlib.crc32(payload) & 0xffffffff

            header = frame_header_struct.pack(
                len(payload),
                frame_type,
                ts,
                seq,
                0,  # flags
                0,  # header crc (unused)
                payload_crc,
                0   # reserved
            )

            self._file.write(header)
            self._file.write(payload)

    def close(self):
        self._stop_event.set()
        self._thread.join()
        self._file.close()