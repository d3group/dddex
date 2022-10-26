# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/04_loadData.ipynb.

# %% ../nbs/04_loadData.ipynb 3
from __future__ import annotations
from fastcore.docments import *
from fastcore.test import *
from fastcore.utils import *

import pandas as pd
import numpy as np

from os.path import join
from tsfresh.feature_extraction import MinimalFCParameters
from tsfresh.utilities.dataframe_functions import roll_time_series
from tsfresh import extract_features
import pathlib

# %% auto 0
__all__ = ['loadDataYaz', 'add_lag_features']

# %% ../nbs/04_loadData.ipynb 5
def loadDataYaz(testDays = 28, returnXY = True, daysToCut = 0):
    
    currentFile = __file__
    scriptPath = os.path.realpath(currentFile)  # /home/user/test/my_script.py
    dirPath = os.path.dirname(scriptPath)  # /home/user/test
    
    dataDirPath = join(dirPath, 'datasets')
    dataPath = join(dataDirPath, 'dataYaz.csv')
    
    data = pd.read_csv(dataPath)
    
    # Cutting away daysToCut-many at end of data: Useful for evaluating
    # evaluation on data in a rolled manner
    cutOffDate = data.dayIndex.max() - daysToCut
    data = data[data['dayIndex'] <= cutOffDate].reset_index(drop = True)
    
    # Label
    if isinstance(testDays, int):
        nDaysTest = testDays
    else:
        tsSizes = data.groupby(['id']).size()
        nDaysTest = int(tsSizes.iloc[0] * testDays)
        
    cutoffDateTest = data.dayIndex.max() - nDaysTest
    data['label'] = ['train' if data.dayIndex.iloc[i] <= cutoffDateTest else 'test' for i in range(data.shape[0])]    

    # Normalize Demand
    scalingData = data[data.label == 'train'].groupby('id')['demand'].agg('max').reset_index()
    scalingData.rename(columns = {'demand': 'scalingValue'}, inplace = True)
    data = pd.merge(data, scalingData, on = 'id')

    data['demand'] = data.demand / data.scalingValue

    #---

    # Add lag features
    y = pd.DataFrame(data['demand'])
    X = data.drop(columns = ['demand'])

    # set lag features
    fc_parameters = MinimalFCParameters()

    # delete length features
    del fc_parameters['length']

    # create lag features
    X, y = add_lag_features(X = X, 
                            y = y, 
                            column_id = ['id'], 
                            column_sort = 'dayIndex', 
                            feature_dict = fc_parameters, 
                            time_windows = [(7, 7), (14, 14), (28, 28)])
    
    data = pd.concat([y, X], axis = 1)
                      
    # Turn y from Series or dataframe to flatted array
    y = np.ravel(y)
    
    #---
    
    X = np.array(data.drop(['demand', 'label', 'id'], axis = 1))
    
    XTrain = X[data['label'] == 'train']
    yTrain = y[data['label'] == 'train']
    
    XTest = X[data['label'] == 'test']
    yTest = y[data['label'] == 'test']
    
    #---
    
    if returnXY:
        return data, XTrain, yTrain, XTest, yTest
    else:
        return data    
    

# %% ../nbs/04_loadData.ipynb 8
def add_lag_features(X, y, column_id, column_sort, feature_dict, time_windows):
    """
    Create lag features for y and add them to X
    Parameters:
    -----------
    X: pandas.DataFrame 
    feature matrix to which TS features are added.
    y: pandas.DataFrame, 
    time series to compute the features for.
    column_id: list, 
    list of column names to group by, e.g. ["shop","product"]. If set to None, 
    either there should be nothing to groupby or each group should be 
    represented by a separate target column in y. 
    column_sort: str,
    column name used to sort the DataFrame. If None, will be filled by an 
    increasing number, meaning that the order of the passed dataframes are used 
    as “time” for the time series.
    feature_dict: dict,
    dictionary containing feature calculator names with the corresponding 
    parameters
    time_windows : list of tuples, 
    each tuple (min_timeshift, max_timeshift), represents the time shifts for 
    ech time windows to comupute e.g. [(7,7),(1,14)] for two time windos 
    a) time window with a fix size of 7 and b) time window that starts with size
    1 and increases up to 14. Then shifts by 1 for each step. 
    """

    if column_id == None:
        X['id'] = 1

    else:
        X['id'] = X[column_id].astype(str).agg('_'.join, axis = 1)

    if column_sort == None:
        X['time'] = range(X.shape[0])  

    else:
        X["time"] = X[column_sort].copy()
    
    y = pd.concat([y, X[['id', 'time']]], axis = 1)
    X = X.set_index(['id', 'time'])
  
    for window in time_windows:
        
        # create time series for given time window 
        df_rolled = roll_time_series(y, 
                                     column_id = "id", 
                                     column_sort = "time", 
                                     min_timeshift = window[0]-1, 
                                     max_timeshift = window[1]-1,
                                     disable_progressbar = True)
        
        df_rolled['id'] = df_rolled['id'].apply(lambda x: (x[0], x[1] + 1))

        # create lag features for given time window 
        df_features = extract_features(df_rolled, 
                                       column_id = "id", 
                                       column_sort = "time",
                                       default_fc_parameters = feature_dict,
                                       disable_progressbar = True)

        # Add time window to feature name for clarification 
        feature_names = df_features.columns.to_list()
        feature_names = [name + "_" + str(window[1]) for name in feature_names]
        df_features.columns = feature_names
        
        # add features for given time window to feature matrix temp
        X = pd.concat([X, df_features], axis = 1)
    
    y = y.set_index(['id', 'time'])
    y_column_names = y.columns.to_list()

    df = pd.concat([X, y],axis = 1)
    df = df.dropna()
    df = df.reset_index(drop = False, inplace = False, names = ['id', 'time']).drop(['time'], axis = 1, inplace = False)

    y = df[y_column_names]
    X = df.drop(y_column_names, axis = 1)

    return X, y
