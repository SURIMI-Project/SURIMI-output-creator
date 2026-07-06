from enum import Enum
from pathlib import Path
import gc
import numpy as np
import xarray as xr
import zarr.codecs
from surimi.v1 import output_creator_service_pb2, simulation_pb2, experiment_step_pb2, update_catch_disposition_statistics_pb2, update_biomass_statistics_pb2
from surimi.v1 import update_sales_statistics_pb2, update_species_prices_statistics_pb2, update_fishing_activity_statistics_pb2, finalise_experiment_pb2, update_sales_statistics_pb2
from surimi.v1 import double_statistics_pb2
from datetime import datetime
from dateutil.relativedelta import relativedelta
from message_registry import message_registry

class OutputType(Enum):
    NET_CDF = "NetCDF"
    ZARR = "Zarr"

# Target chunk size in bytes (~1 MB) used when computing NetCDF chunk sizes
_TARGET_CHUNK_BYTES = 1_048_576


class surimi_output:
    def __init__(self, simulation: simulation_pb2.Simulation, output_directory, experiment_id, end_date_time, file_type: OutputType):
        print(f"Entered surimi_output with output directory: {output_directory}")
        self.simulation = simulation
        self.output_location = output_directory
        self.experiment_id = experiment_id
        self.file_type = file_type
        self.end_date_time = end_date_time

        if self.file_type == OutputType.ZARR:
            self.output_location = output_directory + "/zarr"
        else:
            self.output_location = output_directory + f"/{self.experiment_id}.nc"

        print(f"NetCDF file will use end date: {self.end_date_time}")
        # ----------------------------------------------------------
        # 1. RASTER GRID DEFINITION
        # ----------------------------------------------------------
        #

        xres = self.simulation.geography.xres
        yres = self.simulation.geography.yres
        ncol = self.simulation.geography.ncol
        nrow = self.simulation.geography.nrow

        xmin = self.simulation.geography.xmin
        ymin = self.simulation.geography.ymin

        # Compute center coordinates of each grid cell
        self.lon_values = xmin + (np.arange(ncol) + 0.5) * xres
        self.lat_values = ymin + (np.arange(nrow) + 0.5) * yres

        N_LAT = len(self.lat_values)
        N_LON = len(self.lon_values)

        #
        # ----------------------------------------------------------
        # 2. BUILD MONTHLY TIME AXIS
        # ----------------------------------------------------------
        #

        start_date: datetime = self.simulation.start_date_time.ToDatetime()
        end_date: datetime = self.end_date_time

        self.time_list: list[datetime] = []
        current: datetime = start_date
        while current <= end_date:          # include end date
            self.time_list.append(current)
            current = current + relativedelta(months=1)

        N_TIME = len(self.time_list)
        print(f"Time axis: {N_TIME} monthly steps from {self.time_list[0]} to {self.time_list[-1]}")
        #
        # ----------------------------------------------------------
        # 3. SPECIES & FLEET DEFINITIONS
        # ----------------------------------------------------------
        #

        # Build a list of all unique combinations of (species_code, life_stage)
        self.species_pairs = set()

        # Add baseline entries for each species_code with empty life_stage
        for species_code in {species.species_code for species in self.simulation.items.species}:
            self.species_pairs.add((species_code, ""))

        for species in self.simulation.items.species:
            if species.life_stage != "":
                # Only add the specific life stage if it's not empty, since we already added the baseline entry
                self.species_pairs.add((species.species_code, species.life_stage))

        # Convert to sorted list for stable ordering
        self.species_pairs = sorted(self.species_pairs)

        self.species_codes  = [sp[0] for sp in self.species_pairs]
        self.species_stages = [sp[1] for sp in self.species_pairs]

        # Build fleet list as unique (gear, country) tuples
        self.fleet_pairs = set()

        for fleet_segment in self.simulation.items.fleet_segments:
            gear = fleet_segment.gear_code
            country = fleet_segment.country_code
            self.fleet_pairs.add((gear, country))

        self.fleet_pairs = sorted(self.fleet_pairs)
        self.fleet_gear_codes    = [g for (g, c) in self.fleet_pairs]
        self.fleet_country_codes = [c for (g, c) in self.fleet_pairs]

        self.category_codes: list[str] = [""]  # TODO: populate from simulation definition when available

        self.market_codes = list(dict.fromkeys(market.market_code for market in self.simulation.items.markets))

        N_FLEET = len(self.fleet_pairs)
        N_SPECIES = len(self.species_pairs)
        N_MARKET = len(self.market_codes)
        N_CATEGORY = len(self.category_codes)

        print(f"Defined {N_SPECIES} species/stage combinations, {N_FLEET} fleet segments, {N_MARKET} market codes, {N_CATEGORY} category codes.")
        
        #
        # ----------------------------------------------------------
        # 4. PRE-ALLOCATE IN-MEMORY ARRAYS (NaN = missing)
        # ----------------------------------------------------------
        #

        self.fill_value = np.float32(1.0e20)
        self.mass_unit = next(
            (
                getattr(unit, "unit", None) or getattr(unit, "value", None)
                for unit in self.simulation.standards.measurements.units
                if getattr(unit, "quantity", "").lower() == "mass"
            ),
            "kg",
        )

        # Spatial data variables (time, species, fleet, lat, lon)
        # spatial_shape_fleet = (N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON)
        # spatial_bytes = int(np.prod(spatial_shape_fleet)) * 4  # float32 = 4 bytes
        # print(f"Allocating 3 spatial arrays of shape {spatial_shape_fleet} = {spatial_bytes / 1024**2:.1f} MB each, {3 * spatial_bytes / 1024**2:.1f} MB total")

        self.gross_data = np.full((N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON), np.nan, dtype=np.float32)
        print("done pre-allocating gross data array")

        self.live_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON), np.nan, dtype=np.float32)
        print("done pre-allocating live data array")

        self.dead_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON), np.nan, dtype=np.float32)
        print("done pre-allocating dead data array")

        self.biomass_data  = np.full((N_TIME, N_SPECIES, N_LAT, N_LON), np.nan, dtype=np.float32)
        print("done pre-allocating biomass data array")

        print(f"Pre-allocated data arrays: gross/live/dead catch and discards with shape {self.gross_data.shape} and fill value {self.fill_value}")
        # Total data variables (time, species, fleet) – no lat/lon
        self.gross_total_data = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.live_total_data  = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.dead_total_data  = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.biomass_total_data  = np.full((N_TIME, N_SPECIES), np.nan, dtype=np.float32)
        print("done pre-allocating total data arrays")

        # Price data variable (time, species, market, category)
        self.price_data = np.full((N_TIME, N_SPECIES, N_MARKET, N_CATEGORY), np.nan, dtype=np.float32)
        self.sales_value_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_MARKET), np.nan, dtype=np.float32)
        self.sales_quantity_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_MARKET), np.nan, dtype=np.float32)

        # Fishing activity data variable (time, fleet) 
        self.fishing_activity_data  = np.full((N_TIME, N_FLEET), np.nan, dtype=np.float32)

    def handle_message(self, message):
        """
        Generic entry point for all recorded messages.
        Dispatch is driven by message_registry.
        """
        method_name = message_registry.netcdf_method_for(message)
        if method_name is None:
            return  # unknown or unsupported message

        method = getattr(self, method_name)
        method(message)

    def experiment_step(self, request: experiment_step_pb2.ExperimentStepRequest):
        print(f"ExperimentStep for simulation {request.experiment_id}")

    def UpdateBiomassStatistics(self, request: update_biomass_statistics_pb2.UpdateBiomassStatisticsRequest):
        print(f"UpdateBiomass for simulation {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Clear this time slice (NaN represents missing data)
        self.biomass_data[t_index] = np.nan

        for disp in request.biomass_statistics_summary.biomass_grids_statistics:
            try:
                sp_idx = self.species_pairs.index((disp.species.species_code, disp.species.life_stage))
            except ValueError:
                raise ValueError(
                    f"Unknown species combination (code='{disp.species.species_code}', life_stage='{disp.species.life_stage}')"
                )

            self.biomass_total_data[t_index, sp_idx] = sum(cell.biomass.mean for cell in disp.biomass_cells_statistics)

            for cell in disp.biomass_cells_statistics:
                lat_i = self.find_nearest_index(self.lat_values, cell.latitude)
                lon_i = self.find_nearest_index(self.lon_values, cell.longitude)

                self.biomass_data[t_index, sp_idx, lat_i, lon_i] = cell.biomass.mean

            print(f"Updated biomass for species '{disp.species.species_code}' (stage='{disp.species.life_stage}'). {len(disp.biomass_cells_statistics)} cells updated.")

        print(f"Updated biomass for time index {t_index}. {len(request.biomass_statistics_summary.biomass_grids_statistics)} grids updated.")

    def UpdateSalesStatistics(self, request: update_sales_statistics_pb2.UpdateSalesStatisticsRequest):
        print(f"UpdateSales for simulation {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.start_date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Clear this time slice (NaN represents missing data)
        # self.sales_data[t_index] = np.nan

        for market in request.sales_statistics_summary.market_sales_statistics:
            market_idx = self.market_codes.index(market.market_code)

            for sale in market.sales_statistics:
                try:
                    sp_idx = self.species_pairs.index((sale.species.species_code, sale.species.life_stage))
                except ValueError:
                    raise ValueError(
                        f"Unknown species combination (code='{sale.species.species_code}', life_stage='{sale.species.life_stage}')"
                    )
                #self.sales_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_MARKET), np.nan, dtype=np.float32)

                gear = sale.fleet_segment.gear_code
                country_code = sale.fleet_segment.country_code or 'FRA' # TODO!!!! THIS MUST BE FIXED: TEMPORARY HACK TO HANDLE MISSING COUNTRY CODE IN SALES DATA. REMOVE THIS AND REQUIRE VALID COUNTRY CODE INSTEAD.
                fleet_idx = self.fleet_pairs.index((gear, country_code))

                self.sales_value_data[t_index, sp_idx, fleet_idx, market_idx] = sale.value.mean
                self.sales_quantity_data[t_index, sp_idx, fleet_idx, market_idx] = sale.quantity.mean

            print(f"Updated sales for species '{sale.species.species_code}' (stage='{sale.species.life_stage}'). {len(market.sales_statistics)} sales updated.")

        print(f"Updated sales for time index {t_index} {len(request.sales_statistics_summary.market_sales_statistics)} markets updated.")

    def UpdateCatchDispositionStatistics(self, request: update_catch_disposition_statistics_pb2.UpdateCatchDispositionStatisticsRequest):
        print(f"UpdateCatchDispositionStatistics for simulation {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.start_date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Clear this time slice (NaN represents missing data)
        self.gross_data[t_index] = np.nan
        self.live_data[t_index]  = np.nan
        self.dead_data[t_index]  = np.nan

        for disp in request.catch_disposition_statistics_summary.disposition_grids_statistics:
            try:
                sp_idx = self.species_pairs.index((disp.species.species_code, disp.species.life_stage))
            except ValueError:
                raise ValueError(
                    f"Unknown species combination (code='{disp.species.species_code}', life_stage='{disp.species.life_stage}')"
                )

            gear = disp.fleet_segment.gear_code
            country_code = disp.fleet_segment.country_code
            fleet_idx = self.fleet_pairs.index((gear, country_code))

            self.gross_total_data[t_index, sp_idx, fleet_idx] = sum(cell.gross_catch.mean for cell in disp.disposition_cells_statistics)
            self.live_total_data[t_index, sp_idx, fleet_idx]  = sum(cell.live_discards.mean for cell in disp.disposition_cells_statistics)
            self.dead_total_data[t_index, sp_idx, fleet_idx]  = sum(cell.dead_discards.mean for cell in disp.disposition_cells_statistics)

            for cell in disp.disposition_cells_statistics:
                lat_i = self.find_nearest_index(self.lat_values, cell.latitude)
                lon_i = self.find_nearest_index(self.lon_values, cell.longitude)

                self.gross_data[t_index, sp_idx, fleet_idx, lat_i, lon_i] = cell.gross_catch.mean
                self.live_data[t_index, sp_idx, fleet_idx, lat_i, lon_i]  = cell.live_discards.mean
                self.dead_data[t_index, sp_idx, fleet_idx, lat_i, lon_i]  = cell.dead_discards.mean

            print(f"Updated catch disposition for species '{disp.species.species_code}' (stage='{disp.species.life_stage}'), fleet '{disp.fleet_segment.gear_code}'/'{disp.fleet_segment.country_code}'. {len(disp.disposition_cells_statistics)} cells updated.")

        print(f"Updated catch disposition for time index {t_index}. {len(request.catch_disposition_statistics_summary.disposition_grids_statistics)} grids updated.")

    def UpdateSpeciesPriceStatistics(self, request: update_species_prices_statistics_pb2.UpdateSpeciesPriceStatisticsRequest):
        print(f"UpdateSpeciesPriceStatistics for simulation {request.experiment_id}")

        for price in request.species_price_statistics_summary.species_prices_statistics:
            try:
                sp_idx = self.species_pairs.index((price.species.species_code, price.species.life_stage))
            except ValueError:
                raise ValueError(
                    f"Unknown species combination (code='{price.species.species_code}', life_stage='{price.species.life_stage}')"
                )
            cat_idx = self.category_codes.index(price.category_code)
            market_idx = self.market_codes.index(price.market_code)

            date_str = request.date_time.ToDatetime().strftime("%Y-%m-%d")
            time_idx = self.find_time_index(date_str)

            self.price_data[time_idx, sp_idx, market_idx, cat_idx] = price.price.mean

    def UpdateFishingActivityStatistics(self, request: update_fishing_activity_statistics_pb2.UpdateFishingActivityStatisticsRequest):
        print(f"UpdateFishingActivityStatistics for simulation {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.start_date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Clear this time slice (NaN represents missing data)

        for activity in request.fishing_activity_statistics_summary.fishing_activities_statistics:

            gear = activity.fleet_segment.gear_code
            country_code = activity.fleet_segment.country_code
            fleet_idx = self.fleet_pairs.index((gear, country_code))

            self.fishing_activity_data[t_index, fleet_idx] = activity.fishing_activity_ratio.mean

            print(f"Updated fishing activity for fleet '{activity.fleet_segment.gear_code}'/'{activity.fleet_segment.country_code}'.")

        print(f"Updated fishing activity for time index {t_index}. {len(request.fishing_activity_statistics_summary.fishing_activities_statistics)} activities updated.")

    def finalise(self):
       # print(f"Finalise for simulation {request.experiment_id}")
        try:
            self._write_dataset()
            print(f"Surimi output written to: {self.output_location}")
        finally:
            self._release_memory()
            print("Released in-memory XArray buffers")

    #
    # ----------------------------------------------------------
    # 5. XARRAY DATASET CONSTRUCTION & WRITING
    # ----------------------------------------------------------
    #

    def _write_dataset(self):
        """Build an xarray Dataset from in-memory arrays and write to output."""

        dims_5d    = ("time", "species", "fleet", "lat", "lon")
        dims_4d    = ("time", "species", "lat", "lon")
        dims_3d    = ("time", "species", "fleet")
        dims_2d    = ("time", "species")
        dims_price = ("time", "species", "market", "category")
        dims_sale  = ("time", "species", "fleet", "market")
        dims_activity = ("time", "fleet")

        ds = xr.Dataset(
            data_vars={
                # Spatial data variables
                "gross_catch":   (dims_5d, self.gross_data, {"units": self.mass_unit}),
                "live_discards": (dims_5d, self.live_data,  {"units": self.mass_unit}),
                "dead_discards": (dims_5d, self.dead_data,  {"units": self.mass_unit}),
                "biomass":       (dims_4d, self.biomass_data, {"units": self.mass_unit}),

                # Total (non-spatial) data variables
                "gross_catch_total":   (dims_3d, self.gross_total_data, {"units": self.mass_unit}),
                "live_discards_total": (dims_3d, self.live_total_data,  {"units": self.mass_unit}),
                "dead_discards_total": (dims_3d, self.dead_total_data,  {"units": self.mass_unit}),
                "biomass_total":       (dims_2d, self.biomass_total_data, {"units": self.mass_unit}),

                # Price data variable
                "price": (dims_price, self.price_data, {"units": "EUR"}),

                # Sales data variables
                "sales_value":    (dims_sale, self.sales_value_data, {"units": "EUR"}),
                "sales_quantity": (dims_sale, self.sales_quantity_data, {"units": "kg"}),

                # Fishing activity data variable
                "fishing_activity": (dims_activity, self.fishing_activity_data),

                # Lookup / label variables
                "species_code":        ("species", self.species_codes),
                "species_life_stage":  ("species", self.species_stages),
                "market_code":         ("market", self.market_codes),
                "price_category_code": ("category", self.category_codes),
                "fleet_gear_code":     ("fleet", self.fleet_gear_codes),
                "fleet_country_code":  ("fleet", self.fleet_country_codes),
            },
            coords={
                "time": ("time", self.time_list),
                "lat":  ("lat", self.lat_values.astype(np.float32)),
                "lon":  ("lon", self.lon_values.astype(np.float32)),
            },
        )

        # ----------------------------------------------------------
        # Per-variable encoding
        # ----------------------------------------------------------
        encoding = {}

        if(self.file_type == OutputType.ZARR):
            for name, var in ds.data_vars.items():
                if var.dtype.kind == "f":
                    encoding[name] = {
                        "chunks": self._optimal_chunksizes(var.shape),
                        "compressor": zarr.codecs.BloscCodec(cname="zstd", clevel=3, shuffle=zarr.codecs.BloscShuffle.shuffle),
                        "fill_value": self.fill_value,
                    }
            ds.to_zarr(
                self.output_location,
                mode="w",
                encoding=encoding,
                consolidated=True,
            )
        else:
            for name, var in ds.data_vars.items():
                if var.dtype.kind == "f":  # float variables only
                    encoding[name] = {
                        "_FillValue": self.fill_value,
                        "zlib": True,
                        "complevel": 4,
                        "chunksizes": self._optimal_chunksizes(var.shape),
                    }

            # Time coordinate encoding (CF-conventions)
            encoding["time"] = {
                "units": "seconds since 1970-01-01 00:00:00",
                "calendar": "gregorian",
            }
            ds.to_netcdf(
                    self.output_location,
                    format="NETCDF4",
                    unlimited_dims=["time"],
                    encoding=encoding,
                )

        ds.close()

    def _release_memory(self):
        """Drop references to large in-memory buffers so Python can reclaim memory."""
        large_buffer_attributes = [
            "gross_data",
            "live_data",
            "dead_data",
            "biomass_data",
            "gross_total_data",
            "live_total_data",
            "dead_total_data",
            "biomass_total_data",
            "price_data",
            "sales_value_data",
            "sales_quantity_data",
        ]

        for attribute in large_buffer_attributes:
            if hasattr(self, attribute):
                setattr(self, attribute, None)

        gc.collect()

    @staticmethod
    def _optimal_chunksizes(shape, target_bytes=_TARGET_CHUNK_BYTES):
        """Return chunk sizes with time=1 and other dims as large as possible.

        Keeps full slices along non-time axes unless the resulting chunk
        would exceed *target_bytes* (default 1 MB) for float32 elements.
        In that case, the largest trailing dimension is progressively halved.
        """
        if not shape:
            return shape
        element_size = 4  # float32
        chunks = list(shape)
        chunks[0] = 1  # always chunk one time-step at a time
        while (np.prod(chunks) * element_size) > target_bytes and len(chunks) > 1:
            max_dim = max(range(1, len(chunks)), key=lambda i: chunks[i])
            chunks[max_dim] = max(1, chunks[max_dim] // 2)
        return tuple(chunks)

    #
    # ----------------------------------------------------------
    # 6. HELPER FUNCTIONS
    # ----------------------------------------------------------
    #

    @staticmethod
    def find_nearest_index(array, value):
        """Find nearest index in a 1D coordinate array."""
        return int(np.abs(array - value).argmin())

    def find_time_index(self, date_str):
        """Convert 'YYYY-MM-DD' → time index in monthly list."""
        d = datetime.fromisoformat(date_str)
        for i, t in enumerate(self.time_list):
            if t.year == d.year and t.month == d.month:
                return i
        raise ValueError(f"Date {date_str} is outside range {self.time_list[0]} → {self.time_list[-1]}")

