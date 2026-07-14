from enum import Enum
import logging
from pathlib import Path
import gc
import netCDF4
import numpy as np
import xarray as xr
import zarr.codecs
from surimi.v1 import output_creator_service_pb2, simulation_pb2, experiment_step_pb2, update_catch_disposition_statistics_pb2, update_biomass_statistics_pb2
from surimi.v1 import update_sales_statistics_pb2, update_species_prices_statistics_pb2, update_fishing_activity_statistics_pb2, finalise_experiment_pb2, update_sales_statistics_pb2
from surimi.v1 import update_stock_assessment_pb2
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
        logging.info(f"Entered surimi_output with output directory: {output_directory}")
        self.simulation = simulation
        self.output_location = output_directory
        self.experiment_id = experiment_id
        self.file_type = file_type
        self.end_date_time = end_date_time

        if self.file_type == OutputType.ZARR:
            self.output_location = output_directory + "/zarr"
        else:
            self.output_location = output_directory + f"/{self.experiment_id}.nc"

        logging.info(f"NetCDF file will use end date: {self.end_date_time}")
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

        # Fast O(1) coordinate → array-index lookup (keyed to 6 d.p. to absorb
        # minor floating-point differences between the grid and cell coordinates)
        self._lat_to_idx: dict[float, int] = {round(float(v), 6): i for i, v in enumerate(self.lat_values)}
        self._lon_to_idx: dict[float, int] = {round(float(v), 6): i for i, v in enumerate(self.lon_values)}

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
        logging.info(f"Time axis: {N_TIME} monthly steps from {self.time_list[0]} to {self.time_list[-1]}")
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

        self.category_codes = list(dict.fromkeys(price_category.category_code for price_category in self.simulation.items.price_categories))

        self.market_codes = list(dict.fromkeys(market.market_code for market in self.simulation.items.markets))

        N_FLEET = len(self.fleet_pairs)
        N_SPECIES = len(self.species_pairs)
        N_MARKET = len(self.market_codes)
        N_CATEGORY = len(self.category_codes)

        logging.info(f"Defined {N_SPECIES} species/stage combinations, {N_FLEET} fleet segments, {N_MARKET} market codes, {N_CATEGORY} category codes.")

        #
        # ----------------------------------------------------------
        # 4. ALLOCATE IN-MEMORY ARRAYS / OPEN OUTPUT FILE
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

        # Store dimension sizes for use in update handlers
        self.N_TIME     = N_TIME
        self.N_SPECIES  = N_SPECIES
        self.N_FLEET    = N_FLEET
        self.N_LAT      = N_LAT
        self.N_LON      = N_LON
        self.N_MARKET   = N_MARKET
        self.N_CATEGORY = N_CATEGORY

        if self.file_type == OutputType.NET_CDF:
            # Open the output file immediately and write spatial data one time step
            # at a time.  This avoids allocating ~40 GB of arrays for large simulations.
            self.gross_data   = None
            self.live_data    = None
            self.dead_data    = None
            self.biomass_data = None
            self._nc_file     = None
            self._init_netcdf_file(N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON, N_MARKET, N_CATEGORY)
        else:
            # Zarr: pre-allocate full in-memory arrays
            spatial_shape_fleet   = (N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON)
            spatial_shape_biomass = (N_TIME, N_SPECIES, N_LAT, N_LON)
            spatial_bytes_fleet   = int(np.prod(spatial_shape_fleet)) * 4  # float32 = 4 bytes
            spatial_bytes_biomass = int(np.prod(spatial_shape_biomass)) * 4
            total_spatial_bytes   = 3 * spatial_bytes_fleet + spatial_bytes_biomass
            logging.info(f"Allocating spatial arrays: 3x {spatial_shape_fleet} ({spatial_bytes_fleet / 1024**2:.1f} MB each) + biomass {spatial_shape_biomass} ({spatial_bytes_biomass / 1024**2:.1f} MB) = {total_spatial_bytes / 1024**2:.1f} MB total")
            try:
                self.gross_data   = np.full(spatial_shape_fleet,   np.nan, dtype=np.float32)
                self.live_data    = np.full(spatial_shape_fleet,   np.nan, dtype=np.float32)
                self.dead_data    = np.full(spatial_shape_fleet,   np.nan, dtype=np.float32)
                self.biomass_data = np.full(spatial_shape_biomass, np.nan, dtype=np.float32)
                logging.info(f"Pre-allocated spatial arrays with shape {self.gross_data.shape} and fill value {self.fill_value}")
            except MemoryError as e:
                logging.error(f"Memory allocation failed: {e}")
                raise
        # Total data variables (time, species, fleet) – no lat/lon
        self.gross_total_data = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.live_total_data  = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.dead_total_data  = np.full((N_TIME, N_SPECIES, N_FLEET), np.nan, dtype=np.float32)
        self.biomass_total_data  = np.full((N_TIME, N_SPECIES), np.nan, dtype=np.float32)
        logging.info("done pre-allocating total data arrays")

        # Price data variable (time, species, market, category)
        self.price_data = np.full((N_TIME, N_SPECIES, N_MARKET, N_CATEGORY), np.nan, dtype=np.float32)
        self.sales_value_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_MARKET), np.nan, dtype=np.float32)
        self.sales_quantity_data  = np.full((N_TIME, N_SPECIES, N_FLEET, N_MARKET), np.nan, dtype=np.float32)

        # Fishing activity data variable (time, fleet) 
        self.fishing_activity_data  = np.full((N_TIME, N_FLEET), np.nan, dtype=np.float32)

        # Stock assessment records — collected on arrival, year axis built at write time
        # Each entry: (year: int, sp_idx: int, stock_status: float, exploitation: float)
        self._stock_assessment_records: list[tuple] = []

    def handle_message(self, message):
        """
        Generic entry point for all recorded messages.
        Dispatch is driven by message_registry.
        """
        method_name = message_registry.netcdf_method_for(message)
        if method_name is None:
            logging.error(f"Unknown message type: {type(message).__name__}. No handler method found.")
            return  # unknown or unsupported message

        method = getattr(self, method_name)
        method(message)

    def experiment_step(self, request: experiment_step_pb2.ExperimentStepRequest):
        logging.info(f"ExperimentStep for experiment {request.experiment_id}")

    def UpdateBiomassStatistics(self, request: update_biomass_statistics_pb2.UpdateBiomassStatisticsRequest):
        logging.info(f"UpdateBiomass for experiment {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Build a single-time-step buffer (never holds more than one slice in RAM)
        biomass_slice = np.full((self.N_SPECIES, self.N_LAT, self.N_LON), np.nan, dtype=np.float32)

        for disp in request.biomass_statistics_summary.biomass_grids_statistics:
            try:
                sp_idx = self.species_pairs.index((disp.species.species_code, disp.species.life_stage))
            except ValueError:
                raise ValueError(
                    f"Unknown species combination (code='{disp.species.species_code}', life_stage='{disp.species.life_stage}')"
                )

            self.biomass_total_data[t_index, sp_idx] = sum(cell.biomass.mean for cell in disp.biomass_cells_statistics)

            cells = disp.biomass_cells_statistics
            if cells:
                lat_is = [self._lat_to_idx.get(round(c.latitude, 6),  self.find_nearest_index(self.lat_values, c.latitude))  for c in cells]
                lon_is = [self._lon_to_idx.get(round(c.longitude, 6), self.find_nearest_index(self.lon_values, c.longitude)) for c in cells]
                biomass_slice[sp_idx, lat_is, lon_is] = [c.biomass.mean for c in cells]

            logging.debug(f"Updated biomass for species '{disp.species.species_code}' (stage='{disp.species.life_stage}'). {len(disp.biomass_cells_statistics)} cells updated.")

        # Write slice to the appropriate backing store
        if self.file_type == OutputType.NET_CDF:
            self._nc_biomass[t_index] = biomass_slice
        else:
            self.biomass_data[t_index] = biomass_slice

        logging.info(f"Updated biomass for time index {t_index}. {len(request.biomass_statistics_summary.biomass_grids_statistics)} grids updated.")

    def UpdateSalesStatistics(self, request: update_sales_statistics_pb2.UpdateSalesStatisticsRequest):
        logging.info(f"UpdateSales for experiment {request.experiment_id}")

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

            logging.debug(f"Updated sales for species '{sale.species.species_code}' (stage='{sale.species.life_stage}'). {len(market.sales_statistics)} sales updated.")

        logging.info(f"Updated sales for time index {t_index} {len(request.sales_statistics_summary.market_sales_statistics)} markets updated.")

    def UpdateCatchDispositionStatistics(self, request: update_catch_disposition_statistics_pb2.UpdateCatchDispositionStatisticsRequest):
        logging.info(f"UpdateCatchDispositionStatistics for experiment {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.start_date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Build per-time-step buffers (never hold the full time-series in RAM)
        gross_slice = np.full((self.N_SPECIES, self.N_FLEET, self.N_LAT, self.N_LON), np.nan, dtype=np.float32)
        live_slice  = np.full_like(gross_slice, np.nan)
        dead_slice  = np.full_like(gross_slice, np.nan)

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

            cells = disp.disposition_cells_statistics
            if cells:
                lat_is    = [self._lat_to_idx.get(round(c.latitude, 6),  self.find_nearest_index(self.lat_values, c.latitude))  for c in cells]
                lon_is    = [self._lon_to_idx.get(round(c.longitude, 6), self.find_nearest_index(self.lon_values, c.longitude)) for c in cells]
                gross_slice[sp_idx, fleet_idx, lat_is, lon_is] = [c.gross_catch.mean   for c in cells]
                live_slice[sp_idx, fleet_idx, lat_is, lon_is]  = [c.live_discards.mean for c in cells]
                dead_slice[sp_idx, fleet_idx, lat_is, lon_is]  = [c.dead_discards.mean for c in cells]

            logging.debug(f"Updated catch disposition for species '{disp.species.species_code}' (stage='{disp.species.life_stage}'), fleet '{disp.fleet_segment.gear_code}'/'{disp.fleet_segment.country_code}'. {len(disp.disposition_cells_statistics)} cells updated.")

        # Write slices to the appropriate backing store
        if self.file_type == OutputType.NET_CDF:
            self._nc_gross_catch[t_index]   = gross_slice
            self._nc_live_discards[t_index] = live_slice
            self._nc_dead_discards[t_index] = dead_slice
        else:
            self.gross_data[t_index] = gross_slice
            self.live_data[t_index]  = live_slice
            self.dead_data[t_index]  = dead_slice

        logging.info(f"Updated catch disposition for time index {t_index}. {len(request.catch_disposition_statistics_summary.disposition_grids_statistics)} grids updated.")

    def UpdateSpeciesPriceStatistics(self, request: update_species_prices_statistics_pb2.UpdateSpeciesPriceStatisticsRequest):
        logging.info(f"UpdateSpeciesPriceStatistics for experiment {request.experiment_id}")

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
        logging.info(f"UpdateFishingActivityStatistics for experiment {request.experiment_id}")

        # Convert protobuf Timestamp to Python datetime
        date_str = request.start_date_time.ToDatetime().strftime("%Y-%m-%d")
        t_index = self.find_time_index(date_str)

        # Clear this time slice (NaN represents missing data)

        for activity in request.fishing_activity_statistics_summary.fishing_activities_statistics:

            gear = activity.fleet_segment.gear_code
            country_code = activity.fleet_segment.country_code
            fleet_idx = self.fleet_pairs.index((gear, country_code))

            self.fishing_activity_data[t_index, fleet_idx] = activity.fishing_activity_ratio.mean

            logging.debug(f"Updated fishing activity for fleet '{activity.fleet_segment.gear_code}'/'{activity.fleet_segment.country_code}'.")

        logging.info(f"Updated fishing activity for time index {t_index}. {len(request.fishing_activity_statistics_summary.fishing_activities_statistics)} activities updated.")

    def UpdateStockAssessment(self, request: update_stock_assessment_pb2.UpdateStockAssessmentRequest):
        logging.info(f"UpdateStockAssessment for experiment {request.experiment_id}")

        for species_assessment in request.stock_assessment_summary.species_stock_assessments:
            try:
                sp_idx = self.species_pairs.index((species_assessment.species.species_code, species_assessment.species.life_stage))
            except ValueError:
                raise ValueError(
                    f"Unknown species combination (code='{species_assessment.species.species_code}', life_stage='{species_assessment.species.life_stage}')"
                )

            for sa in species_assessment.stock_assessments:
                self._stock_assessment_records.append((sa.year, sp_idx, sa.stock_status, sa.exploitation))

        logging.info(f"Collected stock assessment records for {len(request.stock_assessment_summary.species_stock_assessments)} species.")


    def finalise(self):
       # print(f"Finalise for simulation {request.experiment_id}")
        try:
            self._write_dataset()
            logging.info(f"Surimi output written to: {self.output_location}")
        finally:
            self._release_memory()
            logging.info("Released in-memory XArray buffers")

    #
    # ----------------------------------------------------------
    # 5. XARRAY DATASET CONSTRUCTION & WRITING
    # ----------------------------------------------------------
    #

    def _write_dataset(self):
        """Write output to the backing file.

        For NetCDF the large spatial arrays are already written incrementally;
        only the remaining small variables need to be added.
        For Zarr the full in-memory arrays are serialised in one shot via xarray.
        """
        if self.file_type == OutputType.NET_CDF:
            self._finalize_netcdf_file()
            return

        # --- Zarr path ---
        dims_5d    = ("time", "species", "fleet", "lat", "lon")
        dims_4d    = ("time", "species", "lat", "lon")
        dims_3d    = ("time", "species", "fleet")
        dims_2d    = ("time", "species")
        dims_price = ("time", "species", "market", "category")
        dims_sale  = ("time", "species", "fleet", "market")
        dims_activity = ("time", "fleet")

        # Build stock assessment arrays from collected records
        _stock_vars: dict = {}
        _stock_coords: dict = {}
        if self._stock_assessment_records:
            _sa_years = sorted(set(r[0] for r in self._stock_assessment_records))
            _N_YEAR = len(_sa_years)
            _N_SPECIES = len(self.species_pairs)
            _stock_status_arr = np.full((_N_YEAR, _N_SPECIES), np.nan, dtype=np.float32)
            _exploitation_arr = np.full((_N_YEAR, _N_SPECIES), np.nan, dtype=np.float32)
            for _year, _sp_idx, _ss, _expl in self._stock_assessment_records:
                _y_idx = _sa_years.index(_year)
                _stock_status_arr[_y_idx, _sp_idx] = _ss
                _exploitation_arr[_y_idx, _sp_idx] = _expl
            _stock_vars = {
                "stock_status": (("year", "species"), _stock_status_arr),
                "exploitation": (("year", "species"), _exploitation_arr),
            }
            _stock_coords = {"year": ("year", _sa_years)}

        ds = xr.Dataset(
            data_vars={
                "gross_catch":   (dims_5d, self.gross_data, {"units": self.mass_unit}),
                "live_discards": (dims_5d, self.live_data,  {"units": self.mass_unit}),
                "dead_discards": (dims_5d, self.dead_data,  {"units": self.mass_unit}),
                "biomass":       (dims_4d, self.biomass_data, {"units": self.mass_unit}),
                "gross_catch_total":   (dims_3d, self.gross_total_data, {"units": self.mass_unit}),
                "live_discards_total": (dims_3d, self.live_total_data,  {"units": self.mass_unit}),
                "dead_discards_total": (dims_3d, self.dead_total_data,  {"units": self.mass_unit}),
                "biomass_total":       (dims_2d, self.biomass_total_data, {"units": self.mass_unit}),
                "price": (dims_price, self.price_data, {"units": "EUR"}),
                "sales_value":    (dims_sale, self.sales_value_data, {"units": "EUR"}),
                "sales_quantity": (dims_sale, self.sales_quantity_data, {"units": "kg"}),
                "fishing_activity": (dims_activity, self.fishing_activity_data),
                **_stock_vars,
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
                **_stock_coords,
            },
        )

        encoding = {}
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
        ds.close()

    # ----------------------------------------------------------
    # NetCDF4 incremental-write helpers
    # ----------------------------------------------------------

    def _init_netcdf_file(self, N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON, N_MARKET, N_CATEGORY):
        """Create the NetCDF4 file, define all dimensions / coordinates, and
        open the large spatial variables for incremental time-slice writes."""

        Path(self.output_location).parent.mkdir(parents=True, exist_ok=True)
        nc = netCDF4.Dataset(self.output_location, "w", format="NETCDF4")
        self._nc_file = nc

        # Dimensions (time is unlimited so the file stays valid even if fewer
        # steps are written than expected)
        nc.createDimension("time",     None)
        nc.createDimension("species",  N_SPECIES)
        nc.createDimension("fleet",    N_FLEET)
        nc.createDimension("lat",      N_LAT)
        nc.createDimension("lon",      N_LON)
        nc.createDimension("market",   N_MARKET)
        nc.createDimension("category", N_CATEGORY)

        # Time coordinate (CF-convention: numeric seconds since epoch)
        time_var = nc.createVariable("time", "f8", ("time",))
        time_var.units    = "seconds since 1970-01-01 00:00:00"
        time_var.calendar = "gregorian"
        epoch     = datetime(1970, 1, 1)
        time_vals = np.array([(t - epoch).total_seconds() for t in self.time_list], dtype=np.float64)
        time_var[:] = time_vals

        # Spatial coordinate variables
        lat_var     = nc.createVariable("lat", "f4", ("lat",))
        lat_var[:]  = self.lat_values.astype(np.float32)
        lon_var     = nc.createVariable("lon", "f4", ("lon",))
        lon_var[:]  = self.lon_values.astype(np.float32)

        # Label / lookup variables (variable-length strings)
        def _str_var(name, dim, values):
            v = nc.createVariable(name, str, (dim,))
            v[:] = np.array(values, dtype=object)

        _str_var("species_code",        "species",  self.species_codes)
        _str_var("species_life_stage",  "species",  self.species_stages)
        _str_var("market_code",         "market",   self.market_codes)
        _str_var("price_category_code", "category", self.category_codes)
        _str_var("fleet_gear_code",     "fleet",    self.fleet_gear_codes)
        _str_var("fleet_country_code",  "fleet",    self.fleet_country_codes)

        # Large spatial variables — defined here but written one time slice at
        # a time in UpdateBiomassStatistics / UpdateCatchDispositionStatistics
        chunksizes_5d = self._optimal_chunksizes((N_TIME, N_SPECIES, N_FLEET, N_LAT, N_LON))
        chunksizes_4d = self._optimal_chunksizes((N_TIME, N_SPECIES, N_LAT, N_LON))

        self._nc_gross_catch = nc.createVariable(
            "gross_catch", "f4", ("time", "species", "fleet", "lat", "lon"),
            fill_value=self.fill_value, chunksizes=chunksizes_5d, zlib=True, complevel=1)
        self._nc_gross_catch.units = self.mass_unit

        self._nc_live_discards = nc.createVariable(
            "live_discards", "f4", ("time", "species", "fleet", "lat", "lon"),
            fill_value=self.fill_value, chunksizes=chunksizes_5d, zlib=True, complevel=1)
        self._nc_live_discards.units = self.mass_unit

        self._nc_dead_discards = nc.createVariable(
            "dead_discards", "f4", ("time", "species", "fleet", "lat", "lon"),
            fill_value=self.fill_value, chunksizes=chunksizes_5d, zlib=True, complevel=1)
        self._nc_dead_discards.units = self.mass_unit

        self._nc_biomass = nc.createVariable(
            "biomass", "f4", ("time", "species", "lat", "lon"),
            fill_value=self.fill_value, chunksizes=chunksizes_4d, zlib=True, complevel=1)
        self._nc_biomass.units = self.mass_unit

        nc.sync()
        logging.info(f"NetCDF4 file opened for incremental writes: {self.output_location}")

    def _finalize_netcdf_file(self):
        """Write remaining in-memory (small) variables to the open NetCDF4 file
        and close it."""
        nc = self._nc_file

        def _make_float_var(name, dims, data, units=None):
            chunks = self._optimal_chunksizes(data.shape)
            v = nc.createVariable(name, "f4", dims,
                fill_value=self.fill_value, chunksizes=chunks, zlib=True, complevel=1)
            if units:
                v.units = units
            v[:] = data

        _make_float_var("gross_catch_total",   ("time", "species", "fleet"), self.gross_total_data,   units=self.mass_unit)
        _make_float_var("live_discards_total", ("time", "species", "fleet"), self.live_total_data,    units=self.mass_unit)
        _make_float_var("dead_discards_total", ("time", "species", "fleet"), self.dead_total_data,    units=self.mass_unit)
        _make_float_var("biomass_total",       ("time", "species"),          self.biomass_total_data, units=self.mass_unit)
        _make_float_var("price",               ("time", "species", "market", "category"), self.price_data,         units="EUR")
        _make_float_var("sales_value",         ("time", "species", "fleet", "market"),   self.sales_value_data,   units="EUR")
        _make_float_var("sales_quantity",      ("time", "species", "fleet", "market"),   self.sales_quantity_data, units="kg")
        _make_float_var("fishing_activity",    ("time", "fleet"),            self.fishing_activity_data)

        # Stock assessment (optional — year dimension created on demand)
        if self._stock_assessment_records:
            sa_years = sorted(set(r[0] for r in self._stock_assessment_records))
            N_YEAR   = len(sa_years)
            N_SP     = len(self.species_pairs)
            stock_status_arr = np.full((N_YEAR, N_SP), np.nan, dtype=np.float32)
            exploitation_arr = np.full((N_YEAR, N_SP), np.nan, dtype=np.float32)
            for year, sp_idx, ss, expl in self._stock_assessment_records:
                y_idx = sa_years.index(year)
                stock_status_arr[y_idx, sp_idx] = ss
                exploitation_arr[y_idx, sp_idx] = expl

            nc.createDimension("year", N_YEAR)
            year_var     = nc.createVariable("year", "i4", ("year",))
            year_var[:]  = np.array(sa_years, dtype=np.int32)

            sv = nc.createVariable("stock_status", "f4", ("year", "species"),
                fill_value=self.fill_value, zlib=True, complevel=1)
            sv[:] = stock_status_arr
            ev = nc.createVariable("exploitation", "f4", ("year", "species"),
                fill_value=self.fill_value, zlib=True, complevel=1)
            ev[:] = exploitation_arr

        nc.sync()
        nc.close()
        self._nc_file = None
        logging.info("NetCDF4 file finalised and closed.")

    def _release_memory(self):
        """Drop references to large in-memory buffers so Python can reclaim memory."""
        # Close any still-open NetCDF4 file handle (safety net in case finalise failed)
        if getattr(self, "_nc_file", None) is not None:
            try:
                self._nc_file.close()
            except Exception:
                pass
            self._nc_file = None

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

        self._stock_assessment_records = []
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

