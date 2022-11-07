# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_levelSetKDEx.ipynb.

# %% ../nbs/01_levelSetKDEx.ipynb 4
from __future__ import annotations
from fastcore.docments import *
from fastcore.test import *
from fastcore.utils import *

import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors

from collections import defaultdict, Counter
from joblib import Parallel, delayed, dump, load
import copy
import warnings

from sklearn.base import BaseEstimator
from sklearn.exceptions import NotFittedError
from .baseClasses import BaseWeightsBasedEstimator
from .wSAA import SAA
from .utils import restructureWeightsDataList

# %% auto 0
__all__ = ['BaseLSx', 'LevelSetKDEx', 'generateBins', 'LevelSetKDEx_kNN', 'binSizeCV', 'scoresForFold', 'getCostRatio']

# %% ../nbs/01_levelSetKDEx.ipynb 7
class BaseLSx:
    
    def __init__(self, 
                 estimator, # (Fitted) object with a .predict-method.
                 binSize: int = None, # Size of the bins created to group the training samples.
                 weightsByDistance: bool = False,
                 ):
        
        if not (hasattr(estimator, 'predict') and callable(estimator.predict)):
            raise ValueError("'estimator' has to have a 'predict'-method!")
            
        if not (isinstance(binSize, (int, np.integer)) or binSize is None):
            raise ValueError("'binSize' has to be an integer!")
            
        self.estimator = copy.deepcopy(estimator)
        self.binSize = binSize
        self.weightsByDistance = weightsByDistance      
        
    #---
    
    def pointPredict(self, 
                     X):
        
        return self.estimator.predict(X)
    
    #---
    
    def refitPointEstimator(self,
                            X, 
                            y
                            ):
        
        setattr(self, 'estimator', self.estimator.fit(X = X, y = y))

# %% ../nbs/01_levelSetKDEx.ipynb 9
class LevelSetKDEx(BaseWeightsBasedEstimator, BaseLSx):
    """
    `LevelSetKDEx`
    """
    
    def __init__(self, 
                 estimator, # Object with a .predict-method (fitted).
                 binSize: int | None = None, # Size of the neighbors considered to compute conditional density.
                 weightsByDistance: bool = False,
                 ):
        
        super(BaseEstimator, self).__init__(estimator = estimator,
                                            binSize = binSize,
                                            weightsByDistance = weightsByDistance)

        self.yTrain = None
        self.yTrainPred = None
        self.indicesPerBin = None
        self.lowerBoundPerBin = None
        self.fitted = False
    
    #---
    
    def fit(self, 
            X: np.ndarray, # Feature matrix used by 'estimator' to predict 'y'.
            y: np.ndarray, # 1-dimensional target variable corresponding to the features 'X'.
            ):
        
        # Checks
        if self.binSize is None:
            raise ValueError("'binSize' must be specified to fit the LSx estimator!")
            
        if self.binSize > y.shape[0]:
            raise ValueError("'binSize' mustn't be bigger than the size of 'y'!")
        
        if X.shape[0] != y.shape[0]:
            raise ValueError("'X' and 'y' must contain the same number of samples!")
        
        # IMPORTANT: In case 'y' is given as a pandas.Series, we can potentially run into indexing 
        # problems later on.
        if isinstance(y, pd.Series):
            y = y.ravel()
        
        #---
        
        try:
            yPred = self.estimator.predict(X)
        except NotFittedError:
            # warnings.warn("The object 'estimator' must have been fitted already!"
            #               "'estimator' will be fitted with 'X' and 'y' to enable point predicting!",
            #               stacklevel = 2)
            try:
                self.estimator.fit(X = X, y = y)                
            except:
                raise ValueError("Couldn't fit 'estimator' with user specified 'X' and 'y'!")
            else:
                yPred = self.estimator.predict(X)
        
        #---
        
        indicesPerBin, lowerBoundPerBin = generateBins(binSize = self.binSize,
                                                       yPred = yPred)

        self.yTrain = y
        self.yTrainPred = yPred
        self.indicesPerBin = indicesPerBin
        self.lowerBoundPerBin = lowerBoundPerBin
        self.fitted = True
        
    #---
    
    def getWeights(self, 
                   X: np.ndarray, # Feature matrix of samples for which conditional density estimates are computed.
                   outputType: 'all' | # Specifies structure of output.
                               'onlyPositiveWeights' | 
                               'summarized' | 
                               'cumulativeDistribution' | 
                               'cumulativeDistributionSummarized' = 'onlyPositiveWeights', 
                   scalingList: list | np.ndarray | None = None, # List or array with same size as self.yTrain containing floats being multiplied with self.yTrain.
                   ):
        
        if not self.fitted:
            raise NotFittedError("This LevelSetKDEx instance is not fitted yet. Call 'fit' with "
                                 "appropriate arguments before trying to compute weights.")
        
        yPred = self.estimator.predict(X)
        
        binPerPred = np.searchsorted(a = self.lowerBoundPerBin, v = yPred, side = 'right') - 1
        neighborsList = [self.indicesPerBin[binIndex] for binIndex in binPerPred]
        
        if self.weightsByDistance:

            predDistances = [np.abs(self.yTrainPred[neighborsList[i]] - yPred[i]) for i in range(len(neighborsList))]

            weightsDataList = list()
            
            for i in range(X.shape[0]):
                distances = predDistances[i]
                distancesCloseZero = np.isclose(distances, 0)
                
                if np.any(distancesCloseZero):
                    indicesCloseZero = neighborsList[i][np.where(distancesCloseZero)[0]]
                    weightsDataList.append((np.repeat(1 / len(indicesCloseZero), len(indicesCloseZero)),
                                            indicesCloseZero))
                    
                else:                                 
                    inverseDistances = 1 / distances

                    weightsDataList.append((inverseDistances / inverseDistances.sum(), 
                                            np.array(neighborsList[i])))
            
            weightsDataList = restructureWeightsDataList(weightsDataList = weightsDataList, 
                                                         outputType = outputType, 
                                                         y = self.yTrain,
                                                         scalingList = scalingList,
                                                         equalWeights = False)
        
        else:
            weightsDataList = [(np.repeat(1 / len(neighbors), len(neighbors)), np.array(neighbors)) for neighbors in neighborsList]

            weightsDataList = restructureWeightsDataList(weightsDataList = weightsDataList, 
                                                         outputType = outputType, 
                                                         y = self.yTrain,
                                                         scalingList = scalingList,
                                                         equalWeights = True)
        
        return weightsDataList
    

# %% ../nbs/01_levelSetKDEx.ipynb 14
def generateBins(binSize: int, # Size of the bins of values being grouped together.
                 yPred: np.ndarray, # 1-dimensional array of predicted values.
                 ):
    "Used to generate the bin-structure induced by the Level-Set-Forecaster algorithm"
    
    predIndicesSort = np.argsort(yPred)
    yPredSorted = yPred[predIndicesSort]

    currentBinSize = 0
    binIndex = 0
    trainIndicesLeft = len(yPred)
    indicesPerBin = defaultdict(list)
    lowerBoundPerBin = dict()
    
    for i in range(len(yPred)):
        
        if i == 0:
            lowerBoundPerBin[binIndex] = np.NINF
            
        currentBinSize += 1
        trainIndicesLeft -= 1

        indicesPerBin[binIndex].append(predIndicesSort[i])
        
        if trainIndicesLeft < binSize:
            indicesPerBin[binIndex].extend(predIndicesSort[np.arange(i+1, len(yPred), 1)])
            break

        if currentBinSize >= binSize and yPredSorted[i] < yPredSorted[i+1]:
            lowerBoundPerBin[binIndex + 1] = (yPredSorted[i] + yPredSorted[i+1]) / 2
            binIndex += 1
            currentBinSize = 0
           
    indicesPerBin = {binIndex: np.array(indices) for binIndex, indices in indicesPerBin.items()}
    
    lowerBoundPerBin = pd.Series(lowerBoundPerBin)
    lowerBoundPerBin.index.name = 'binIndex'
    
    return indicesPerBin, lowerBoundPerBin

# %% ../nbs/01_levelSetKDEx.ipynb 16
class LevelSetKDEx_kNN(BaseWeightsBasedEstimator, BaseLSx):
    """
     `LevelSetKDEx_kNN` turns any point predictor that has a .predict-method 
    into an estimator of the condititional density of the underlying distribution.
    The basic idea of each level-set based approach is to interprete the point forecast
    generated by the underlying point predictor as a similarity measure of samples.
    In the case of the `LevelSetKDEx_kNN` defined here, for every new samples
    'binSize'-many training samples are computed whose point forecast is closest
    to the point forecast of the new sample.
    The resulting empirical distribution of these 'nearest' training samples are 
    viewed as our estimation of the conditional distribution of each the new sample 
    at hand.
    
    NOTE 1: The `LevelSetKDEx_kNN` class can only be applied to estimators that 
    have been fitted already.
    
    NOTE 2: In contrast to the standard `LevelSetKDEx`, it is possible to apply
    `LevelSetKDEx_kNN` to arbitrary dimensional point predictors.
    """
    
    def __init__(self, 
                 estimator, # Object with a .predict-method (fitted).
                 binSize: int | None = None, # Size of the neighbors considered to compute conditional density.
                 weightsByDistance: bool = False,
                 ):
        
        super(BaseEstimator, self).__init__(estimator = estimator,
                                            binSize = binSize,
                                            weightsByDistance = weightsByDistance)
        
        self.yTrain = None
        self.yTrainPred = None
        self.nearestNeighborsOnPreds = None
        self.fitted = False
        
    #---
    
    def fit(self: LevelSetKDEx_kNN, 
            X: np.ndarray, # Feature matrix used by 'estimator' to predict 'y'.
            y: np.ndarray, # Target variable corresponding to features 'X'.
            ):
        
        # Checks
        if self.binSize is None:
            raise ValueError("'binSize' must be specified to fit the LSx estimator!")
            
        if self.binSize > y.shape[0]:
            raise ValueError("'binSize' mustn't be bigger than the size of 'y'!")
            
        if X.shape[0] != y.shape[0]:
            raise ValueError("'X' and 'y' must contain the same number of samples!")
            
        # IMPORTANT: In case 'y' is given as a pandas.Series, we can potentially run into indexing 
        # problems later on.
        if isinstance(y, pd.Series):
            y = y.ravel()
        
        #---
        
        try:
            yPred = self.estimator.predict(X)
        except NotFittedError:
            # warnings.warn("The object 'estimator' must have been fitted already!"
            #               "'estimator' will be fitted with 'X' and 'y' to enable point predicting!",
            #               stacklevel = 2)
            try:
                self.estimator.fit(X = X, y = y)                
            except:
                raise ValueError("Couldn't fit 'estimator' with user specified 'X' and 'y'!")
            else:
                yPred = self.estimator.predict(X)

        #---
        
        yPred_reshaped = np.reshape(yPred, newshape = (len(yPred), 1))
        
        nn = NearestNeighbors(algorithm = 'kd_tree')
        nn.fit(X = yPred_reshaped)

        #---

        self.yTrain = y
        self.yTrainPred = yPred
        self.nearestNeighborsOnPreds = nn
        self.fitted = True
        
    #---
    
    def getWeights(self: LevelSetKDEx_kNN, 
                   X: np.ndarray, # Feature matrix of samples for which conditional density estimates are computed.
                   outputType: 'all' | # Specifies structure of output.
                               'onlyPositiveWeights' | 
                               'summarized' | 
                               'cumulativeDistribution' | 
                               'cumulativeDistributionSummarized' = 'onlyPositiveWeights', 
                   scalingList: list | np.ndarray | None = None, # List or array with same size as self.yTrain containing floats being multiplied with self.yTrain.
                  ):
        
        if not self.fitted:
            raise NotFittedError("This LevelSetKDEx_kNN instance is not fitted yet. Call 'fit' with "
                                 "appropriate arguments before trying to compute weights.")
            
        #---

        nn = self.nearestNeighborsOnPreds

        #---

        yPred = self.estimator.predict(X)   
        yPred_reshaped = np.reshape(yPred, newshape = (len(yPred), 1))

        distancesMatrix, neighborsMatrix = nn.kneighbors(X = yPred_reshaped, 
                                                         n_neighbors = self.binSize + 1)

        #---

        neighborsList = list(neighborsMatrix[:, 0:self.binSize])
        distanceCheck = np.where(distancesMatrix[:, self.binSize - 1] == distancesMatrix[:, self.binSize])
        indicesToMod = distanceCheck[0]

        for index in indicesToMod:
            distanceExtremePoint = np.absolute(yPred[index] - self.yTrainPred[neighborsMatrix[index, self.binSize-1]])

            neighborsByRadius = nn.radius_neighbors(X = yPred_reshaped[index:index + 1], 
                                                    radius = distanceExtremePoint, return_distance = False)[0]
            neighborsList[index] = neighborsByRadius

        #---
        
        if self.weightsByDistance:
            binSizesReal = [len(neighbors) for neighbors in neighborsList]
            binSizeMax = max(binSizesReal)
            
            distancesMatrix, neighborsMatrix = nn.kneighbors(X = yPred_reshaped, 
                                                             n_neighbors = binSizeMax)
            
            weightsDataList = list()
            
            for i in range(X.shape[0]):
                distances = distancesMatrix[i, 0:binSizesReal[i]]
                distancesCloseZero = np.isclose(distances, 0)
                
                if np.any(distancesCloseZero):
                    indicesCloseZero = neighborsMatrix[i, np.where(distancesCloseZero)[0]]
                    weightsDataList.append((np.repeat(1 / len(indicesCloseZero), len(indicesCloseZero)),
                                            indicesCloseZero))
                    
                else:                                 
                    inverseDistances = 1 / distances

                    weightsDataList.append((inverseDistances / inverseDistances.sum(), 
                                            np.array(neighborsList[i])))
            
            weightsDataList = restructureWeightsDataList(weightsDataList = weightsDataList, 
                                                         outputType = outputType, 
                                                         y = self.yTrain,
                                                         scalingList = scalingList,
                                                         equalWeights = False)
            
        else:
            weightsDataList = [(np.repeat(1 / len(neighbors), len(neighbors)), np.array(neighbors)) for neighbors in neighborsList]

            weightsDataList = restructureWeightsDataList(weightsDataList = weightsDataList, 
                                                         outputType = outputType, 
                                                         y = self.yTrain,
                                                         scalingList = scalingList,
                                                         equalWeights = True)

        return weightsDataList
      

# %% ../nbs/01_levelSetKDEx.ipynb 21
class binSizeCV:

    def __init__(self,
                 estimatorLSx, # Object with a .predict-method (fitted).
                 cvFolds, # Specifies cross-validation-splits. Identical to 'cv' used for cross-validation in sklearn.
                 binSizeGrid: list | np.ndarray = [4, 7, 10, 15, 20, 30, 40, 50, 60, 70, 80, 
                                                   100, 125, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900,
                                                   1000, 1250, 1500, 1750, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000], # binSize (int) values being evaluated.                
                 probs: list | np.ndarray = [i / 100 for i in range(1, 100, 1)], # list or array of floats between 0 and 1. p-quantiles being predicted to evaluate performance of LSF.
                 refitPerProb: bool = False, # If True, for each p-quantile a fitted LSF with best binSize to predict it is returned. Otherwise only one LSF is returned that is best over all probs.
                 n_jobs: int | None = None, # number of folds being computed in parallel.
                 ):
        
        # CHECKS  
        if not isinstance(estimatorLSx, (LevelSetKDEx, LevelSetKDEx_kNN)):
            raise ValueError("'estimatorLSx' has to be a 'LevelSetKDEx'- or a 'LevelSetKDEx_kNN'-object!")               
            
        if np.any(np.array(probs) > 1) or np.any(np.array(probs) < 0): 
            raise ValueError("probs must only contain numbers between 0 and 1!")            
            
        if any([not isinstance(binSize, int) for binSize in binSizeGrid]): 
            raise ValueError("'binSizeGrid' may only contain integer values!")
        
        self.estimatorLSx = copy.deepcopy(estimatorLSx)
        self.probs = probs
        self.binSizeGrid = binSizeGrid        
        self.cvFolds = cvFolds
        self.refitPerProb = refitPerProb
        self.n_jobs = n_jobs
        
        self.bestBinSize = None
        self.bestBinSize_perProb = None
        self.bestEstimatorLSx = None
        self.cv_results = None
        self.cv_results_raw = None
        

# %% ../nbs/01_levelSetKDEx.ipynb 23
@patch
def fit(self: binSizeCV, 
        X, 
        y):
    
    # Making sure that X and y are arrays to ensure correct subsetting via implicit indices.
    X = np.array(X)
    y = np.array(y)
    
    nSmallestTrainSample = len(self.cvFolds[0][0])
    self.binSizeGrid = [binSize for binSize in self.binSizeGrid if binSize <= nSmallestTrainSample]
    
    # scoresPerFold = Parallel(n_jobs = self.n_jobs)(delayed(scoresForFold)(cvFold = cvFold,
    #                                                                       binSizeGrid = self.binSizeGrid,
    #                                                                       probs = self.probs,
    #                                                                       estimatorLSx = copy.deepcopy(self.estimatorLSx),
    #                                                                       y = y,
    #                                                                       X = X) for cvFold in self.cvFolds)
    
    scoresPerFold = [scoresForFold(cvFold = cvFold,
                                   binSizeGrid = self.binSizeGrid,
                                   probs = self.probs,
                                   estimatorLSx = copy.deepcopy(self.estimatorLSx),
                                   y = y,
                                   X = X) for cvFold in self.cvFolds]

    self.cv_results_raw = scoresPerFold
    meanCostsDf = sum(scoresPerFold) / len(scoresPerFold)
    self.cv_results = meanCostsDf
    
    #---

    meanCostsPerBinSize = meanCostsDf.mean(axis = 1)
    binSizeBestOverall = meanCostsPerBinSize.index[np.argmin(meanCostsPerBinSize)]
    self.bestBinSize = binSizeBestOverall

    binSizeBestPerProb = meanCostsDf.idxmin(axis = 0)
    self.bestBinSize_perProb = binSizeBestPerProb

    #---

    if self.refitPerProb:
        bestEstimatorDict = dict()
        
        for prob in self.probs:
            # Note: In future versions this could has to be flexible in the sense
            # to be able to create new objects with the best parameters found via CV
            # where the tuned parameters can vary (currently only binSize).
            estimatorLSx = copy.deepcopy(self.estimatorLSx)
            estimatorLSx.set_params(binSize = binSizeBestPerProb.loc[prob])
            estimatorLSx.fit(X = X, y = y)

            bestEstimatorDict[prob] = estimatorLSx

        self.bestEstimatorLSx = bestEstimatorDict

    else:
        estimatorLSx = copy.deepcopy(self.estimatorLSx)
        estimatorLSx.set_params(binSize = binSizeBestOverall)
        estimatorLSx.fit(X = X, y = y)

        self.bestEstimatorLSx = estimatorLSx

# %% ../nbs/01_levelSetKDEx.ipynb 26
# This function evaluates the newsvendor performance for different bin sizes for one specific fold.
# The considered bin sizes

def scoresForFold(cvFold, binSizeGrid, probs, estimatorLSx, y, X):
    
    indicesTrain = cvFold[0]
    indicesTest = cvFold[1]
    
    yTrainFold = y[indicesTrain]
    XTrainFold = X[indicesTrain]
    
    yTestFold = y[indicesTest]
    XTestFold = X[indicesTest]
    
    estimatorLSx.refitPointEstimator(X = XTrainFold, y = yTrainFold)
    
    #---
       
    SAA_fold = SAA()
    SAA_fold.fit(y = yTrainFold)
    
    # By setting 'X = None', the SAA results are only computed for a single observation (they are independent of X anyway).
    # In order to receive the final dataframe of SAA results, we simply duplicate this single row as many times as needed.
    quantilesDictSAAOneOb = SAA_fold.predict(X = None, probs = probs, outputAsDf = False)
    quantilesDictSAA = {prob: np.repeat(quantile, len(XTestFold)) for prob, quantile in quantilesDictSAAOneOb.items()}
    
    #---
                                                   
    costRatiosPerBinSize = defaultdict(dict)

    for binSize in iter(binSizeGrid):
        
        estimatorLSx.set_params(binSize = binSize)
        
        estimatorLSx.fit(X = XTrainFold,
                         y = yTrainFold)
        
        quantilesDict = estimatorLSx.predict(X = XTestFold,
                                             probs = probs,
                                             outputAsDf = False)
        
        #---

        costRatioDict = dict()
        
        for prob in probs:            
            costRatioDict[prob] = getCostRatio(decisions = quantilesDict[prob], 
                                               decisionsSAA = quantilesDictSAA[prob], 
                                               yTest = yTestFold, 
                                               prob = prob)
        
        costRatiosPerBinSize[binSize] = costRatioDict
    
    #---
    
    costRatioDf = pd.DataFrame.from_dict(costRatiosPerBinSize, orient = 'index')
    
    return costRatioDf

# %% ../nbs/01_levelSetKDEx.ipynb 28
def getCostRatio(decisions, decisionsSAA, yTest, prob):

    # Newsvendor Costs of our model
    cost = np.array([prob * (yTest[i] - decisions[i]) if yTest[i] > decisions[i] 
                     else (1 - prob) * (decisions[i] - yTest[i]) 
                     for i in range(len(yTest))]).sum()
    
    # Newsvendor Costs of SAA
    costSAA = np.array([prob * (yTest[i] - decisionsSAA[i]) if yTest[i] > decisionsSAA[i] 
                        else (1 - prob) * (decisionsSAA[i] - yTest[i]) 
                        for i in range(len(yTest))]).sum()
    
    #---
    
    # We have to capture the special case of costSAA == 0, because then we can't compute the 
    # Cost-Ratio using the actual definition.
    if costSAA > 0:
        costRatio = cost / costSAA
    else:
        if cost == 0:
            costRatio = 0
        else:
            costRatio = 1
    
    return costRatio
