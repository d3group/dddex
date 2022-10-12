# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/00_core.ipynb.

# %% auto 0
__all__ = ['BaseWeightsBasedPredictor']

# %% ../nbs/00_core.ipynb 5
from abc import ABC, abstractmethod

import pandas as pd
import numpy as np

from sklearn.neighbors import NearestNeighbors 
from sklearn.ensemble import RandomForestRegressor

from collections import Counter
from collections import defaultdict

from joblib import Parallel, delayed, dump, load
import ipdb

# %% ../nbs/00_core.ipynb 7
class BaseWeightsBasedPredictor(ABC):
    
    @abstractmethod
    def __init__(self):
        """Define weights-based predictor"""
    
    #---
    
    @abstractmethod
    def fit(self, X, Y):
        """Fit weights-based predictor on given training data"""
    
    #---
    
    @abstractmethod
    def getWeightsData(self, X, scalingList = None):
        """Compute weights of feature array X"""
    
    #---
    
    def predict(self, X, probs = [0.1, 0.5, 0.9], outputAsDf = False, scalingList = None):
        
        distributionDataList = self.getWeightsData(X = X,
                                                   outputType = 'cumulativeDistribution',
                                                   scalingList = scalingList)
        
        quantilesDict = {prob: [] for prob in probs}
        
        for probsDistributionFunction, YDistributionFunction in distributionDataList:
        
            for prob in probs:
                quantileIndex = np.where(probsDistributionFunction >= prob)[0][0]
                quantile = YDistributionFunction[quantileIndex]
                quantilesDict[prob].append(quantile)
        
        quantilesDf = pd.DataFrame(quantilesDict)
        
        # Just done to make the dictionary contain arrays rather than lists of the quantiles.
        quantilesDict = {prob: np.array(quantiles) for prob, quantiles in quantilesDict.items()}
        
        #---
        
        if outputAsDf:
            return quantilesDf
        
        else:
            return quantilesDict
    
