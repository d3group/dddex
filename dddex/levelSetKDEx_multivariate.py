"""We introduce model-agnostic approaches to turn multivariate point predictors into conditional density estimators."""

# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/02_levelSetKDEx_multivariate.ipynb.

# %% ../nbs/02_levelSetKDEx_multivariate.ipynb 4
from __future__ import annotations
from fastcore.docments import *
from fastcore.test import *
from fastcore.utils import *

import pandas as pd
import numpy as np
import faiss
from scipy.spatial.distance import cdist
from scipy.optimize import linear_sum_assignment
from scipy.spatial import KDTree
from sklearn.base import BaseEstimator
from sklearn.exceptions import NotFittedError
from sklearn.tree import DecisionTreeRegressor

from collections import defaultdict
from joblib import Parallel, delayed, dump, load
import copy
import warnings

from .baseClasses import BaseLSx, BaseWeightsBasedEstimator_multivariate
from .levelSetKDEx_univariate import generateBins
from .wSAA import SampleAverageApproximation, RandomForestWSAA, RandomForestWSAA_LGBM
from .utils import restructureWeightsDataList_multivariate

# %% auto 0
__all__ = ['LevelSetKDEx_multivariate', 'LevelSetKDEx_multivariate_DT', 'LevelSetKDEx_multivariate_gessaman']

# %% ../nbs/02_levelSetKDEx_multivariate.ipynb 6
class LevelSetKDEx_multivariate(BaseWeightsBasedEstimator_multivariate, BaseLSx):
    """
    `LevelSetKDEx` turns any point forecasting model into an estimator of the underlying conditional density.
    The name 'LevelSet' stems from the fact that this approach interprets the values of the point forecasts
    as a similarity measure between samples. 
    In this version of the LSx algorithm, we are grouping the point predictions of the samples specified via `X`
    based on a k-means clustering algorithm. The number of clusters is determined by the `nClusters` parameter.  
    In order to ensure theoretical asymptotic optimality of the algorithm, it has to be ensured that the number
    of training observations receiving positive weight is at least minClusterSize, while minClusterSize has to be 
    an element of o(N) meaning minClusterSize / N -> 0 as N -> infinity.
    To ensure this, each cluster is checked for its size and clusters being smaller than minClusterSize have to be
    modified. For every cluster that is too small, we are recurvely searching for the closest other cluster until
    the size of the combined cluster is at least minClusterSize. The clusters are not actually merged in the traditional
    sense, though. Instead, the observations of the neighboring clusters are also used to compute the weights for the respective
    cluster that is too small. As a consequence, a training observation can be part of the weights of more than one cluster.
    Let's say we have three clusters A, B and C, minClusterSize = 10, the sizes of the clusters are 4, 4 and 20. Furthermore,
    assume B is the closest cluster to A and C the closest to B. The set of indices are given then as follows:
    A: A + B + C
    B: B + C
    C: C
    This way it is ensured that the number of training observations receiving positive weight is at least 10 for every cluster. This
    control over the minimal number of samples receiving positive weight ensures the convergence of the estimation error.
    At the same time, the above algorithm ensure that the distance of the samples receiving positive weight can't get arbitrarily high
    ensuring the convergence of the approximation error.
    """
    
    def __init__(self, 
                 estimator, # Model with a .fit and .predict-method (implementing the scikit-learn estimator interface).
                 nClusters: int=None, # Number of clusters being created while running fit.
                 minClusterSize: int=None, # Minimum size of a cluster. If a cluster is smaller than this value, it will be merged with another cluster.
                 ):
        
        super(BaseEstimator, self).__init__(estimator = estimator)
        
        # Check if binSize is int
        if not isinstance(nClusters, int):
            raise ValueError("'nClusters' must be an integer!")
        
        # Check if minClusterSize is int
        if not isinstance(minClusterSize, int):
            raise ValueError("'minClusterSize' must be an integer!")
        
        self.nClusters = nClusters
        self.minClusterSize = minClusterSize
        
        self.yTrain = None
        self.yPredTrain = None
        self.indicesPerBin = None
        self.lowerBoundPerBin = None
        self.fitted = False
    
    #---
    
    def fit(self, 
            X: np.ndarray, # Feature matrix used by `estimator` to predict `y`.
            y: np.ndarray, # 1-dimensional target variable corresponding to the feature matrix `X`.
            ):
        """
        Fit `LevelSetKDEx` model by grouping the point predictions of the samples specified via `X`
        according to a k-means clustering algorithm. The number of clusters is determined by the `nClusters` parameter.
        In order to ensure theoretical asymptotic optimality of the algorithm, it has to be ensured that the number
        of training observations receiving positive weight is at least minClusterSize, while minClusterSize has to be
        an element of o(N) meaning minClusterSize / N -> 0 as N -> infinity.
        The specifics on how the clusters are created can be found in the class documentation.
        """
        
        # Checks
        if self.nClusters is None:
            raise ValueError("'nClusters' must be specified to fit the LSx estimator!")
        
        if self.minClusterSize is None:
            raise ValueError("'minClusterSize' must be specified to fit the LSx estimator!")
            
        if self.nClusters > y.shape[0]:
            raise ValueError("'nClusters' mustn't be bigger than the size of 'y'!")
        
        if self.minClusterSize > y.shape[0]:
            raise ValueError("'minClusterSize' mustn't be bigger than the size of 'y'!")
        
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
            try:
                self.estimator.fit(X = X, y = y)                
            except:
                raise ValueError("Couldn't fit 'estimator' with user specified 'X' and 'y'!")
            else:
                yPred = self.estimator.predict(X)
        
        #---
        
        if len(y.shape) == 1:
            y = y.reshape(-1, 1)
            yPred = yPred.reshape(-1, 1)
        
        #---

        # Modify yPred to be compatible with faiss
        yPredMod = yPred.astype(np.float32)
        
        # Train kmeans model based on the faiss library
        kmeans = faiss.Kmeans(d = yPredMod.shape[1], k = self.nClusters)
        kmeans.train(yPredMod)

        # Get cluster centers created by faiss. IMPORTANT NOTE: not all clusters are used! We will handle that further below.
        centers = kmeans.centroids
        clusters = np.arange(centers.shape[0])
        
        # Compute the cluster assignment for each sample
        clusterAssignments = kmeans.assign(yPredMod)[1]
        
        # Based on the clusters and cluster assignments, we can now compute the indices belonging to each bin / cluster
        indicesPerBin = [[] for i in range(self.nClusters)]
        clusterSizes = [0 for i in range(self.nClusters)]

        for index, cluster in enumerate(clusterAssignments):
            indicesPerBin[cluster].append(index)
            clusterSizes[cluster] += 1

        clusterSizes = np.array(clusterSizes)

        # Just needed for a check in the end
        maxSizeOfExistingClusters = np.max(clusterSizes)

        #---

        # clustersTooSmall is the array of all clusters that are too small.
        clustersTooSmall = np.where(np.array(clusterSizes) < self.minClusterSize)[0]
        
        if len(clustersTooSmall) > 0:
            
            indicesPerBinNew = copy.deepcopy(indicesPerBin)

            # We are searching for the closest other cluster for each cluster that is too small
            # As we don't know how many nearest neighbors we need, we are setting k to the number of clusters
            nearestClusters = KDTree(centers).query(centers[clustersTooSmall], k = centers.shape[0])[1]

            # sizeNearestClusters is an array of shape (len(clustersTooSmall), self.nClusters)
            sizeNearestClusters = clusterSizes[nearestClusters]

            # Calculating the cumulative sum of the cluster sizes over each row allows us to find out 
            # which cluster is the first one that is big enough to make the current cluster big enough
            clusterSizesCumSum = np.cumsum(sizeNearestClusters, axis = 1)

            # argmax returns the first index where the condition is met.
            necessaryClusters = (clusterSizesCumSum >= self.minClusterSize).argmax(axis = 1)
            
            # We are now creating the new indicesPerBin list by extending the indices of the clusters that are too small
            for i, cluster in enumerate(clustersTooSmall):
                clustersToAdd = nearestClusters[i, 0:necessaryClusters[i] + 1]
                    
                indicesPerBinNew[cluster] = np.concatenate([indicesPerBin[cluster] for cluster in clustersToAdd])
                clusterSizes[cluster] = len(indicesPerBinNew[cluster])

                # Following our intended logic, the resulting clusters can't be bigger than minClusterSize + maxSizeOfExistingClusters
                if len(indicesPerBinNew[cluster]) > self.minClusterSize + maxSizeOfExistingClusters:
                    raise Warning("The cluster size is bigger than minClusterSize + maxSizeOfExistingClusters. This should not happen!")

            # indicesPerBin is only turned into a dictionary to be consistent with the other implementations of LevelSetKDEx
            self.indicesPerBin = {cluster: np.array(indicesPerBinNew[cluster], dtype = 'uintc') for cluster in range(len(indicesPerBinNew))}
            self.clusterSizes = pd.Series(clusterSizes)
        
        else:
            self.indicesPerBin = {cluster: np.array(indicesPerBin[cluster], dtype = 'uintc') for cluster in range(len(indicesPerBin))}
            self.clusterSizes = pd.Series(clusterSizes)
            
        #---
        
        self.yTrain = y
        self.yPredTrain = yPred
        self.centers = centers
        self.kmeans = kmeans
        self.fitted = True

    #---
    
    def getWeights(self, 
                   X: np.ndarray, # Feature matrix for which conditional density estimates are computed.
                   # Specifies structure of the returned density estimates. One of: 
                   # 'all', 'onlyPositiveWeights', 'summarized', 'cumDistribution', 'cumDistributionSummarized'
                   outputType: str='onlyPositiveWeights', 
                   # Optional. List with length X.shape[0]. Values are multiplied to the estimated 
                   # density of each sample for scaling purposes.
                   scalingList: list=None, 
                   ) -> list: # List whose elements are the conditional density estimates for the samples specified by `X`.
        
        # __annotations__ = BaseWeightsBasedEstimator.getWeights.__annotations__
        __doc__ = BaseWeightsBasedEstimator_multivariate.getWeights.__doc__
        
        if not self.fitted:
            raise NotFittedError("This LevelSetKDEx instance is not fitted yet. Call 'fit' with "
                                 "appropriate arguments before trying to compute weights.")
        
        #---
        
        yPred = self.estimator.predict(X).astype(np.float32)
        
        if len(yPred.shape) == 1:
            yPred = yPred.reshape(-1, 1)
            
        #---

        clusterPerPred = self.kmeans.assign(yPred)[1]
        
        #---
        
        neighborsList = [self.indicesPerBin[cluster] for cluster in clusterPerPred]
        
        weightsDataList = [(np.repeat(1 / len(neighbors), len(neighbors)), np.array(neighbors)) for neighbors in neighborsList]
        
        weightsDataList = restructureWeightsDataList_multivariate(weightsDataList = weightsDataList, 
                                                                  outputType = outputType, 
                                                                  y = self.yTrain,
                                                                  scalingList = scalingList,
                                                                  equalWeights = True)
        
        return weightsDataList
    

# %% ../nbs/02_levelSetKDEx_multivariate.ipynb 8
class LevelSetKDEx_multivariate_DT(BaseWeightsBasedEstimator_multivariate, BaseLSx):
    """
    `LevelSetKDEx` turns any point forecasting model into an estimator of the underlying conditional density.
    The name 'LevelSet' stems from the fact that this approach interprets the values of the point forecasts
    as a similarity measure between samples. 
    TBD.
    """
    
    def __init__(self, 
                 estimator, # Model with a .fit and .predict-method (implementing the scikit-learn estimator interface).
                 max_depth: int=8, # Maximum depth of the decision tree used to generate the bins.
                 min_samples_leaf: int=100, # Minimum number of samples required to be in a bin.
                 ):
        
        super(BaseEstimator, self).__init__(estimator = estimator)

        # Check if max_depth is integer
        if not isinstance(max_depth, (int, np.int32, np.int64)):
            raise ValueError("'max_depth' must be an integer!")
        
        # Check if max_depth is bigger than 0
        if max_depth <= 0:
            raise ValueError("'max_depth' must be bigger than 0!")
        
        # Check if min_samples_leaf is integer or float
        if not isinstance(min_samples_leaf, (int, np.int32, np.int64, float, np.float32, np.float64)):
            raise ValueError("'min_samples_leaf' must be an integer or float!")
        
        # Check if min_samples_leaf is bigger than 0
        if min_samples_leaf <= 0:
            raise ValueError("'min_samples_leaf' must be bigger than 0!")

        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        
        self.yTrain = None
        self.yPredTrain = None
        self.drf = None
        self.fitted = False
    
    #---
    
    def fit(self: LevelSetKDEx_DT, 
            X: np.ndarray, # Feature matrix used by `estimator` to predict `y`.
            y: np.ndarray, # 1-dimensional target variable corresponding to the feature matrix `X`.
            ):
        """
        TBD.
        """

        # Check if max_depth is integer
        if not isinstance(self.max_depth, (int, np.int32, np.int64)):
            raise ValueError("'max_depth' must be an integer!")
        
        # Check if min_samples_leaf is integer or float
        if not isinstance(self.min_samples_leaf, (int, np.int32, np.int64, float, np.float32, np.float64)):
            raise ValueError("'min_samples_leaf' must be an integer or float!")
            
        if self.min_samples_leaf > y.shape[0]:
            raise ValueError("'min_samples_leaf' mustn't be bigger than the size of 'y'!")
        
        if X.shape[0] != y.shape[0]:
            raise ValueError("'X' and 'y' must contain the same number of samples!")
        
        #---
        
        try:
            yPred = self.estimator.predict(X)
            
        except NotFittedError:
            try:
                self.estimator.fit(X = X, y = y)                
            except:
                raise ValueError("Couldn't fit 'estimator' with user specified 'X' and 'y'!")
            else:
                yPred = self.estimator.predict(X)
        
        #---
        
        tree = DecisionTreeRegressor(max_depth = self.max_depth, min_samples_leaf = self.min_samples_leaf)

        tree.fit(X = yPred, y = y)
        leafIndicesTrain = tree.apply(yPred)

        indicesPerBin = defaultdict(list)

        for index, leafIndex in enumerate(leafIndicesTrain):
            indicesPerBin[leafIndex].append(index)
        
        #---
        
        # IMPORTANT: In case 'y' is given as a pandas.Series, we can potentially run into indexing 
        # problems later on.
        self.yTrain = np.array(y)
        
        self.yPredTrain = yPred
        self.tree = tree
        self.indicesPerBin = indicesPerBin
        self.fitted = True
        
    #---
    
    def getWeights(self: LevelSetKDEx_DT, 
                   X: np.ndarray, # Feature matrix for which conditional density estimates are computed.
                   # Specifies structure of the returned density estimates. One of: 
                   # 'all', 'onlyPositiveWeights', 'summarized', 'cumDistribution', 'cumDistributionSummarized'
                   outputType: str='onlyPositiveWeights', 
                   # Optional. List with length X.shape[0]. Values are multiplied to the estimated 
                   # density of each sample for scaling purposes.
                   scalingList: list=None, 
                   ) -> list: # List whose elements are the conditional density estimates for the samples specified by `X`.
        
        # __annotations__ = BaseWeightsBasedEstimator.getWeights.__annotations__
        __doc__ = BaseWeightsBasedEstimator_multivariate.getWeights.__doc__
        
        if not self.fitted:
            raise NotFittedError("This LevelSetKDEx instance is not fitted yet. Call 'fit' with "
                                 "appropriate arguments before trying to compute weights.")
        
        #---
        
        yPred = self.estimator.predict(X)
        leafIndicesTest = self.tree.apply(yPred)

        weightsDataList = [(np.repeat(1 / len(self.indicesPerBin[leafIndex]), len(self.indicesPerBin[leafIndex])),
                            np.array(self.indicesPerBin[leafIndex])) for leafIndex in leafIndicesTest]
        
        #---

        weightsDataList = restructureWeightsDataList_multivariate(weightsDataList = weightsDataList, 
                                                                  outputType = outputType, 
                                                                  y = self.yTrain,
                                                                  scalingList = scalingList,
                                                                  equalWeights = True)
        
        return weightsDataList
    
    

# %% ../nbs/02_levelSetKDEx_multivariate.ipynb 10
class LevelSetKDEx_multivariate_gessaman(BaseWeightsBasedEstimator_multivariate, BaseLSx):
    """
    `LevelSetKDEx` turns any point forecasting model into an estimator of the underlying conditional density.
    The name 'LevelSet' stems from the fact that this approach interprets the values of the point forecasts
    as a similarity measure between samples. 
    In this version of the LSx algorithm, we are applying the so-called Gessaman rule to create statistically
    equivalent blocks of samples. In essence, the algorithm is a multivariate extension of the univariate
    LevelSetKDEx algorithm based on bin-building. 
    We are creating equally sized bins of samples based on the point predictions of the samples specified via `X`
    for every coordinate axis. Every bin of one axis is combined with the bins of all other axes resulting in
    a total of nBinsPerDim^dim many bins. 
    Example: Let's say we have 100000 samples, the binSize is given as 20 and the number of dimension
    is 3. As the binSize is given as 20, we want to create 5000 bins alltogether. Hence, there have to be
    5000^(1/dim) = 5000^(1/3) = 17 bins per dimension. 
    IMPORTANT NOTE: The getWeights function is not yet finished and has to be completed.
    """
    
    def __init__(self, 
                 estimator, # Model with a .fit and .predict-method (implementing the scikit-learn estimator interface).
                 nBinsPerDim: int=None, # Number of samples belonging to each bin.
                 ):
        
        super(BaseEstimator, self).__init__(estimator = estimator)
        
        # Check if nBinsPerDim is int
        if not isinstance(nBinsPerDim, int):
            raise ValueError("'binSize' must be an integer!")
        
        self.nBinsPerDim = nBinsPerDim
        
        self.yTrain = None
        self.yPredTrain = None
        self.indicesPerBin = None
        self.lowerBoundPerBin = None
        self.fitted = False
    
    #---
    
    def fit(self, 
            X: np.ndarray, # Feature matrix used by `estimator` to predict `y`.
            y: np.ndarray, # 1-dimensional target variable corresponding to the feature matrix `X`.
            ):
        """
        Fit `LevelSetKDEx` model by grouping the point predictions of the samples specified via `X`
        according to a simple binning rule called Gessaman rule based on the point predictions of the samples.
        """
        
        # Checks
        if self.nBinsPerDim is None:
            raise ValueError("'binSize' must be specified to fit the LSx estimator!")
        
        if self.nBinsPerDim > y.shape[0]:
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
            try:
                self.estimator.fit(X = X, y = y)                
            except:
                raise ValueError("Couldn't fit 'estimator' with user specified 'X' and 'y'!")
            else:
                yPred = self.estimator.predict(X)
        
        #---
        
        if len(y.shape) == 1:
            y = y.reshape(-1, 1)
            yPred = yPred.reshape(-1, 1)
        
        #---

        

        # We have to calculate the size of the bins for every coordinate axis
        dim = yPred.shape[1]

        for j in range(dim):

            yPredDim = yPred[:, j]

            if j == 0:
                
                binSize_firstAxis = int(np.ceil(yPredDim.shape[0] / self.nBinsPerDim))

                indicesPerBin, lowerBounds = generateBins(binSize = binSize_firstAxis,
                                                          yPred = yPredDim)
                
                indicesPerBin = {(bin, ): indices for bin, indices in indicesPerBin.items()}
                lowerBounds = {(bin, ): [lowerBound] for bin, lowerBound in lowerBounds.items()}

            else:
                
                indicesPerBin_ToAdd = {}
                lowerBounds_ToAdd = {}

                for bin in indicesPerBin.keys():

                    yPredDim_bin = yPredDim[indicesPerBin[bin]]
                    binSize_newAxis = int(np.ceil(yPredDim_bin.shape[0] / self.nBinsPerDim))
                    
                    indicesPerBin_newAxis, lowerBounds_newAxis = generateBins(binSize = binSize_newAxis,
                                                                              yPred = yPredDim_bin)
                    
                    indicesPerBin_ToAdd.update({bin + (bin_new, ): indicesPerBin[bin][indices] for bin_new, indices in indicesPerBin_newAxis.items()})
                    lowerBounds_ToAdd.update({bin + (bin_new, ): lowerBounds[bin] + [lowerBound] for bin_new, lowerBound in lowerBounds_newAxis.items()})

                indicesPerBin = indicesPerBin_ToAdd
                lowerBounds = lowerBounds_ToAdd

        # Transform the indices given by indicesPerBin into numpy arrays
        indicesPerBin = {bin: np.array(indices) for bin, indices in indicesPerBin.items()}

        # Transform the lower bounds given by lowerBounds into a pandas dataframe
        lowerBoundsDf = pd.DataFrame(lowerBounds).T
            
        #---
        
        self.yTrain = y
        self.yPredTrain = yPred
        self.lowerBoundsDf = lowerBoundsDf
        self.indicesPerBin = indicesPerBin
        self.fitted = True

    #---
    
    def getWeights(self, 
                   X: np.ndarray, # Feature matrix for which conditional density estimates are computed.
                   # Specifies structure of the returned density estimates. One of: 
                   # 'all', 'onlyPositiveWeights', 'summarized', 'cumDistribution', 'cumDistributionSummarized'
                   outputType: str='onlyPositiveWeights', 
                   # Optional. List with length X.shape[0]. Values are multiplied to the estimated 
                   # density of each sample for scaling purposes.
                   scalingList: list=None, 
                   ) -> list: # List whose elements are the conditional density estimates for the samples specified by `X`.
        
        # __annotations__ = BaseWeightsBasedEstimator.getWeights.__annotations__
        __doc__ = BaseWeightsBasedEstimator_multivariate.getWeights.__doc__
        
        if not self.fitted:
            raise NotFittedError("This LevelSetKDEx instance is not fitted yet. Call 'fit' with "
                                 "appropriate arguments before trying to compute weights.")
        
        #---
        
        yPred = self.estimator.predict(X).astype(np.float32)
        
        if len(yPred.shape) == 1:
            yPred = yPred.reshape(-1, 1)
            
        #---

        # IMPORTANT NOTE: THE CODE BELOW IS NOT FINISHED YET. IT IS JUST A STARTING POINT.

        lowerBounds_firstDim = self.lowerBoundsDf.iloc[:, 0]

        # Filter lowerBounds_firstDim to only contain unique values
        lowerBounds_firstDim = lowerBounds_firstDim.unique()

        binPerPred_firstDim = np.searchsorted(a = lowerBounds_firstDim, v = yPred[:, 0], side = 'right') - 1

        # The code has to be continued here. We have to find the correct bin for the second dimension, then the third dimension etc.
        # It seems like we have to iterate over the observations unfortunately. Of course, there could be ways to do the search in
        # batches, but the code would be much more complicated.
        for ySingle in yPred:
            for j in range(1, yPred.shape[1]):

                print(j)
        
        #---
        
        neighborsList = [self.indicesPerBin[cluster] for cluster in clusterPerPred]
        
        weightsDataList = [(np.repeat(1 / len(neighbors), len(neighbors)), np.array(neighbors)) for neighbors in neighborsList]
        
        weightsDataList = restructureWeightsDataList_multivariate(weightsDataList = weightsDataList, 
                                                                  outputType = outputType, 
                                                                  y = self.yTrain,
                                                                  scalingList = scalingList,
                                                                  equalWeights = True)
        
        return weightsDataList
    
