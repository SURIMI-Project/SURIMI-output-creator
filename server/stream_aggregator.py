from collections import defaultdict
import numpy as np


class stream_output_creator:
    def __init__(self, simulation_ids, writer):
        """
        simulation_ids : iterable
            Expected simulation IDs, e.g. [1, 2, 3, 4, 5]

        writer : callable
            Function with signature:
            writer(variable, month, mean, p05, p95)
        """
        self.expected_sims = set(simulation_ids)
        self.writer = writer

        # buffer[variable][month][simulation_id] = value
        self.buffer = defaultdict(lambda: defaultdict(dict))

    def process(self, *, variable, month, simulation_id, value):
        """
        Process a single message from the stream.
        """
        month_buf = self.buffer[variable][month]
        month_buf[simulation_id] = value

        # Check completeness
        if month_buf.keys() == self.expected_sims:
            self._aggregate_and_flush(variable, month, month_buf)

    def _aggregate_and_flush(self, variable, month, month_buf):
        values = np.array(list(month_buf.values()), dtype=float)

        mean = float(values.mean())
        p05 = float(np.percentile(values, 5))
        p95 = float(np.percentile(values, 95))

        self.writer(
            variable=variable,
            month=month,
            mean=mean,
            p05=p05,
            p95=p95,
        )

        # Free memory
        del self.buffer[variable][month]
        if not self.buffer[variable]:
            del self.buffer[variable]