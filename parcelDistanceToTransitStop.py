"""REMM transit distances without ArcPy or the ArcGIS Python API.

Install: uv add geopandas pyogrio
Keep the project's existing orca_wfrc dependency.
Distances use full parcel geometries (not centroids), in EPSG:26912,
then convert meters to feet. Compare against a known ArcPy year before use:
the original assumes its input parcel CRS uses meters; a different input
projection can give slightly different planar distances.
No stops in a category produces missing distances, never a false zero.
"""

from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio

import orca_wfrc.orca as sim


DEFAULT_PARCEL_GDB = (
    r"\\server1\Volumef\SHARED\Andy\_FromJosh\Parcel Distance 2 Transit Stops"
    r"\remm_base_year.gdb"
)
DISTANCE_CRS = "EPSG:26912"  # NAD83 / UTM zone 12N; meters
METERS_TO_FEET = 3.28084


def nearest_distance_feet(parcels, stops):
    """Return one distance per parcel row, including when nearest stops tie."""
    distances = pd.Series(np.nan, index=parcels.index, dtype="float64")
    if parcels.empty or stops.empty:
        return distances
    nearest = gpd.sjoin_nearest(
        parcels[[parcels.geometry.name]],
        stops[[stops.geometry.name]],
        how="left",
        distance_col="distance_m",
    )
    # Equidistant stops can yield multiple matches for a single parcel.
    minimum = nearest.groupby(level=0)["distance_m"].min()
    return minimum.reindex(parcels.index) * METERS_TO_FEET


def write_dbf(frame, path):
    """Write an attribute-only DBF using GDAL through Pyogrio."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A plain DataFrame creates a table without shapefile geometry.
    pyogrio.write_dataframe(
        pd.DataFrame(frame), path, driver="ESRI Shapefile", encoding="UTF-8"
    )


@sim.step("parcelDistanceToTransitStop")
def parcelDistanceToTransitStop(year, settings, store):
    if year not in settings["tdm"]["run_years"]:
        return

    tdm = settings["tdm"]
    output_dir = Path("Outputs")
    year_dir = Path(tdm["main_dir"]) / str(year)
    input_dir = year_dir / "0_InputProcessing"
    transit_dir = year_dir / "6_REMM"
    output_dir.mkdir(parents=True, exist_ok=True)
    transit_dir.mkdir(parents=True, exist_ok=True)

    # Optional settings overrides allow other users to choose their data path.
    parcels = gpd.read_file(
        tdm.get("parcel_gdb", DEFAULT_PARCEL_GDB),
        layer=tdm.get("parcel_layer", "parcels"),
        columns=["parcel_id"],
        engine="pyogrio",
    ).reset_index(drop=True)
    if "parcel_id" not in parcels.columns:
        raise ValueError("Parcel layer must contain parcel_id.")
    if parcels.crs is None:
        raise ValueError("Parcel layer has no CRS; define its correct CRS first.")
    bad = parcels.geometry.isna() | parcels.geometry.is_empty | ~parcels.geometry.is_valid
    if bad.any():
        raise ValueError(f"Parcel layer contains {bad.sum()} missing, empty, or invalid geometries.")
    parcels = parcels.to_crs(DISTANCE_CRS)

    def read_stops(filename, columns):
        frame = pyogrio.read_dataframe(input_dir / filename, read_geometry=False)
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{filename} is missing fields: {sorted(missing)}")
        return frame

    all_stops = read_stops("c_StopsAllModes.dbf", ["N", "X", "Y"])
    mode7 = read_stops("c_StopsMode7.dbf", ["N"])
    mode8 = read_stops("c_StopsMode8.dbf", ["N"])

    # Membership avoids duplicate stop rows from repeated mode identifiers.
    all_stops["mode7"] = all_stops["N"].isin(mode7["N"].dropna()).astype(int)
    all_stops["mode8"] = all_stops["N"].isin(mode8["N"].dropna()).astype(int)
    all_stops["rail"] = ((all_stops["mode7"] == 1) | (all_stops["mode8"] == 1)).astype(int)
    for column in ["X", "Y"]:
        all_stops[column] = pd.to_numeric(all_stops[column], errors="raise")
    if not np.isfinite(all_stops[["X", "Y"]].to_numpy(dtype=float)).all():
        raise ValueError("Transit stops contain missing or nonfinite X/Y coordinates.")

    all_stops.to_csv(transit_dir / f"TransitStopsTest{year}.csv", index=False)
    stops = gpd.GeoDataFrame(
        all_stops,
        geometry=gpd.points_from_xy(all_stops["X"], all_stops["Y"]),
        crs=DISTANCE_CRS,  # Same coordinate assumption as the original code.
    )

    result = pd.DataFrame({"parcel_id": parcels["parcel_id"]})
    result["dist2rails"] = nearest_distance_feet(parcels, stops.loc[stops["rail"] == 1])
    # Original CLEAR_SELECTION uses ALL stops here, including rail.
    result["dist2busst"] = nearest_distance_feet(parcels, stops)

    stamp = date.today().strftime("%Y%m%d")
    write_dbf(result, output_dir / f"parceldistance2transitstops_{year}_{stamp}.dbf")
    write_dbf(result, transit_dir / "parceldistance2transitstops.dbf")
