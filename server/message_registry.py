from typing import Dict, Type
from google.protobuf.message import Message


from surimi.v1.update_biomass_statistics_pb2 import UpdateBiomassStatisticsRequest
from surimi.v1.update_sales_statistics_pb2 import UpdateSalesStatisticsRequest
from surimi.v1.update_catch_disposition_statistics_pb2 import UpdateCatchDispositionStatisticsRequest
from surimi.v1.update_fishing_activity_statistics_pb2 import UpdateFishingActivityStatisticsRequest
from surimi.v1.update_species_prices_statistics_pb2 import UpdateSpeciesPriceStatisticsRequest


class message_registry:
    """
    Central registry for all recorded protobuf message types.
    """

    # ---- canonical mapping (SOURCE OF TRUTH) ----

    _entries: Dict[str, dict] = {
        "surimi.v1.UpdateBiomassStatisticsRequest": {
            "frame_type": 2,
            "proto_cls": UpdateBiomassStatisticsRequest,
            "netcdf_method": "UpdateBiomassStatistics",
        },
        "surimi.v1.UpdateSalesStatisticsRequest": {
            "frame_type": 3,
            "proto_cls": UpdateSalesStatisticsRequest,
            "netcdf_method": "UpdateSalesStatistics",
        },
        "surimi.v1.UpdateCatchDispositionStatisticsRequest": {
            "frame_type": 4,
            "proto_cls": UpdateCatchDispositionStatisticsRequest,
            "netcdf_method": "UpdateCatchDispositionStatistics",
        },
        "surimi.v1.UpdateFishingActivityStatisticsRequest": {
            "frame_type": 5,
            "proto_cls": UpdateFishingActivityStatisticsRequest,
            "netcdf_method": "UpdateFishingActivityStatistics",
        },
        "surimi.v1.UpdateSpeciesPriceStatisticsRequest": {
            "frame_type": 6,
            "proto_cls": UpdateSpeciesPriceStatisticsRequest,
            "netcdf_method": "UpdateSpeciesPriceStatistics",
        },
    }

    # ---- derived indices ----
    _frame_type_to_entry = {
        v["frame_type"]: v for v in _entries.values()
    }

    _proto_cls_to_entry = {
        v["proto_cls"]: v for v in _entries.values()
    }

    # ---------- recording ----------

    @classmethod
    def should_record(cls, message: Message) -> bool:
        return message.DESCRIPTOR.full_name in cls._entries

    @classmethod
    def frame_type_for(cls, message: Message) -> int:
        return cls._entries[
            message.DESCRIPTOR.full_name
        ]["frame_type"]

    # ---------- reading ----------

    @classmethod
    def deserialize(cls, frame_type: int, payload: bytes) -> Message | None:
        entry = cls._frame_type_to_entry.get(frame_type)
        if entry is None:
            return None

        msg = entry["proto_cls"]()
        msg.ParseFromString(payload)
        return msg

    # ---------- NetCDF dispatch ----------

    @classmethod
    def netcdf_method_for(cls, message: Message) -> str | None:
        entry = cls._proto_cls_to_entry.get(type(message))
        if entry is None:
            return None
        return entry["netcdf_method"]
