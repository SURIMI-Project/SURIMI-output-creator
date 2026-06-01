# from typing import Dict, Type
# from google.protobuf.message import Message

# from surimi.v1.ecology_consumer_service_pb2 import UpdateBiomassRequest
# from surimi.v1.market_provider_service_pb2 import UpdateSalesRequest
# from surimi.v1.catch_consumer_service_pb2 import UpdateCatchDispositionRequest
# from surimi.v1.regulations_provider_service_pb2 import UpdateFishingActivityRequest
# from surimi.v1.species_price_consumer_service_pb2 import UpdateSpeciesPricesRequest


# class message_registry:
#     """
#     Central registry for all recorded protobuf message types.
#     """

#     # ---- canonical mapping (SOURCE OF TRUTH) ----

#     _entries: Dict[str, dict] = {
#         "surimi.v1.UpdateBiomassRequest": {
#             "frame_type": 1,
#             "proto_cls": UpdateBiomassRequest,
#             "netcdf_method": "UpdateBiomass",
#         },
#         "surimi.v1.UpdateSalesRequest": {
#             "frame_type": 2,
#             "proto_cls": UpdateSalesRequest,
#             "netcdf_method": "UpdateSales",
#         },
#         "surimi.v1.UpdateCatchDispositionRequest": {
#             "frame_type": 3,
#             "proto_cls": UpdateCatchDispositionRequest,
#             "netcdf_method": "UpdateCatchDisposition",
#         },
#         "surimi.v1.UpdateFishingActivityRequest": {
#             "frame_type": 4,
#             "proto_cls": UpdateFishingActivityRequest,
#             "netcdf_method": "UpdateFishingActivity",
#         },
#         "surimi.v1.UpdateSpeciesPricesRequest": {
#             "frame_type": 5,
#             "proto_cls": UpdateSpeciesPricesRequest,
#             "netcdf_method": "UpdateSpeciesPrices",
#         },
#     }

#     # ---- derived indices ----
#     _frame_type_to_entry = {
#         v["frame_type"]: v for v in _entries.values()
#     }

#     _proto_cls_to_entry = {
#         v["proto_cls"]: v for v in _entries.values()
#     }

#     # ---------- recording ----------

#     @classmethod
#     def should_record(cls, message: Message) -> bool:
#         return message.DESCRIPTOR.full_name in cls._entries

#     @classmethod
#     def frame_type_for(cls, message: Message) -> int:
#         return cls._entries[
#             message.DESCRIPTOR.full_name
#         ]["frame_type"]

#     # ---------- reading ----------

#     @classmethod
#     def deserialize(cls, frame_type: int, payload: bytes) -> Message | None:
#         entry = cls._frame_type_to_entry.get(frame_type)
#         if entry is None:
#             return None

#         msg = entry["proto_cls"]()
#         msg.ParseFromString(payload)
#         return msg

#     # ---------- NetCDF dispatch ----------

#     @classmethod
#     def netcdf_method_for(cls, message: Message) -> str | None:
#         entry = cls._proto_cls_to_entry.get(type(message))
#         if entry is None:
#             return None
#         return entry["netcdf_method"]
