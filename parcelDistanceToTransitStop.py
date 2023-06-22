import arcpy
from arcpy import env, management, analysis
import os
from datetime import date
from arcgis import GIS
from arcgis.features import GeoAccessor
from arcgis.features import GeoSeriesAccessor
import pandas as pd
import glob

import h5py
from simpledbf import Dbf5
import numpy as np
import random


import orca_wfrc.orca as sim

@sim.step('parcelDistanceToTransitStop')
def parcelDistanceToTransitStop(year, settings, store):
    if year in settings['tdm']['run_years']:
        arcpy.env.overwriteOutput = True
        arcpy.env.parallelProcessingFactor = "90%"

        pd.options.display.max_columns = None

        # fill NA values in Spatially enabled dataframes (ignores SHAPE column)
        def fill_na_sedf(df_with_shape_column, fill_value=0):
            if 'SHAPE' in list(df_with_shape_column.columns):
                df = df_with_shape_column.copy()
                shape_column = df['SHAPE'].copy()
                del df['SHAPE']
                return df.fillna(fill_value).merge(shape_column, left_index=True, right_index=True, how='inner')
            else:
                raise Exception("Dataframe does not include 'SHAPE' column")

        if not os.path.exists('Outputs'):
            os.makedirs('Outputs')

        outputs = ['.\\Outputs', "scratch.gdb", 'results.gdb']
        gdb = os.path.join(outputs[0], outputs[1])
        gdb2 = os.path.join(outputs[0], outputs[2])

        if not arcpy.Exists(gdb):
            arcpy.CreateFileGDB_management(outputs[0], outputs[1])

        if not arcpy.Exists(gdb2):
            arcpy.CreateFileGDB_management(outputs[0], outputs[2])

        parcels = r"\\server1\Volumef\SHARED\Andy\_FromJosh\Parcel Distance 2 Transit Stops\remm_base_year.gdb\parcels"
        parcel_df = pd.DataFrame.spatial.from_featureclass(parcels)[['OBJECTID', 'parcel_id']].copy()

        "process the transit stops with x and y needed"

        stopsdir = settings['tdm']['main_dir'] + str(year) + '/0_InputProcessing/'
        allstopsfile =os.path.join(stopsdir, f'c_StopsAllModes.dbf')
        mo7stopsfile = os.path.join(stopsdir, f'c_StopsMode7.dbf')
        mo8stopsfile = os.path.join(stopsdir, f'c_StopsMode8.dbf')

        allstops = Dbf5(allstopsfile)
        allstops = allstops.to_dataframe()
        mo7stops = Dbf5(mo7stopsfile)
        mo7stops = mo7stops.to_dataframe()
        mo8stops = Dbf5(mo8stopsfile)
        mo8stops = mo8stops.to_dataframe()
        mo7stops = mo7stops[['N']]
        mo8stops = mo8stops[['N']]
        mo7stops['mode7'] = 1
        mo8stops['mode8'] = 1
        result = allstops.merge(mo7stops, how='left', left_on='N', right_on='N').merge(mo8stops, how='left', left_on='N',
                                                                                       right_on='N')
        result = result.fillna(0)
        result['rail'] = 1
        result.rail[(result.mode7 == 0) & (result.mode8 == 0)] = 0
        tdmTransitStopsfolder =settings['tdm']['main_dir'] + str(year) + '/6_REMM'
        # tdmTransitStopsfolder = os.path.join(settings['tdm']['main_dir'], str(year) + '/6_REMM')
        tdmTransitStopsfolderFile= os.path.join(tdmTransitStopsfolder,'/TransitStopsTest'+str(year)+'.csv')
        result.to_csv(tdmTransitStopsfolderFile)

        csvs = glob.glob(os.path.join(tdmTransitStopsfolderFile))


        d = date.today().strftime("%Y%m%d")

        for csv in csvs:


            # convert to xy
            # year = os.path.basename(csv).split('.')[0].split('_')[1]
            print('working on {}'.format(year))
            result = os.path.join(outputs[0], os.path.basename(csv).split('.')[0] + '.shp')
            print('--converting xy to points')
            transit_pts = arcpy.management.XYTableToPoint(in_table=os.path.realpath(csv),
                                                          out_feature_class=os.path.realpath(result),
                                                          x_field="X", y_field="Y",
                                                          coordinate_system=arcpy.SpatialReference('NAD 1983 UTM Zone 12N'))
            transit_pts_lyr = arcpy.MakeFeatureLayer_management(transit_pts, 'transit_pts_lyr')

            # ========
            # rail
            # ========
            query = """ rail = 1 """
            arcpy.SelectLayerByAttribute_management(transit_pts_lyr, 'NEW_SELECTION', query)

            # generate near table
            result2 = os.path.join(outputs[0], os.path.basename(csv).split('.')[0] + '_rail.csv')
            print('--rail near table')
            rail = arcpy.analysis.GenerateNearTable(parcels, transit_pts_lyr, result2, closest='CLOSEST', method='PLANAR')
            rail_df = pd.read_csv(result2)
            rail_df = rail_df[['IN_FID', 'NEAR_DIST']].copy()
            rail_df.columns = ['IN_FID', 'dist2rails']
            rail_df['dist2rails'] = rail_df['dist2rails'] * 3.28084  # convert from meters to ft

            # ========
            # bus
            # ========
            arcpy.SelectLayerByAttribute_management(transit_pts_lyr, 'CLEAR_SELECTION')

            # generate near table
            result3 = os.path.join(outputs[0], os.path.basename(csv).split('.')[0] + '_bus.csv')
            print('--bus near table')
            bus = arcpy.analysis.GenerateNearTable(parcels, transit_pts_lyr, result3, closest='CLOSEST', method='PLANAR')
            bus_df = pd.read_csv(result3)
            bus_df = bus_df[['IN_FID', 'NEAR_DIST']].copy()
            bus_df.columns = ['IN_FID', 'dist2busst']
            bus_df['dist2busst'] = bus_df['dist2busst'] * 3.28084  # convert from meters to ft

            # ===============
            # create table
            # ===============
            print('--exporting final table')
            df = parcel_df.merge(rail_df, left_on='OBJECTID', right_on='IN_FID', how='left')
            df = df.merge(bus_df, left_on='OBJECTID', right_on='IN_FID', how='left')
            df = df[['parcel_id', 'dist2rails', 'dist2busst']].copy()
            out_csv = os.path.join(outputs[0], f"parceldistance2transitstops_{year}_{d}.csv")
            df.to_csv(out_csv, index=False)
            arcpy.TableToTable_conversion(out_csv, outputs[0], f"parceldistance2transitstops_{year}_{d}.dbf")
            arcpy.TableToTable_conversion(out_csv, tdmTransitStopsfolder, f"parceldistance2transitstops.dbf")
            os.remove(out_csv)

        # cleanup
        xmls = glob.glob(os.path.join('.\\Outputs','*.xml'))
        cpgs = glob.glob(os.path.join('.\\Outputs','*.cpg'))
        junk = xmls + cpgs

        if len(junk) > 0:
            for j in junk:
                os.remove(j)
