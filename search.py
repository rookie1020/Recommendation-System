#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat May 12 20:23:28 2018

@author: J Rishabh Kumar
@content: computes accuracy metrics for an algorithm on various combinations of parameters, 
            over a cross-validation procedure.
"""

from abc import ABCMeta, abstractmethod
from itertools import product
import numpy as np
from joblib import Parallel
from joblib import delayed
from six import moves, string_types, with_metaclass

from split import get_cv
from cross_validate import fit_and_score
from dataset import DatasetUserFolds
from utility import get_rng


class BaseSearchCV(with_metaclass(ABCMeta)):

    @abstractmethod
    def __init__(self, algo_class, measures=['rmse', 'mae'], cv=None,
                 refit=False, return_train_measures=False, n_jobs=1,
                 pre_dispatch='2*n_jobs', joblib_verbose=0):

        self.algo_class = algo_class
        self.measures = [measure.lower() for measure in measures]
        self.cv = cv

        if isinstance(refit, string_types):
            if refit.lower() not in self.measures:
                raise ValueError('It looks like the measure you want to use '
                                 'with refit ({}) is not in the measures '
                                 'parameter')

            self.refit = refit.lower()

        elif refit is True:
            self.refit = self.measures[0]

        else:
            self.refit = False

        self.return_train_measures = return_train_measures
        self.n_jobs = n_jobs
        self.pre_dispatch = pre_dispatch
        self.joblib_verbose = joblib_verbose

    def fit(self, data):

        if self.refit and isinstance(data, DatasetUserFolds):
            raise ValueError('refit cannot be used when data has been '
                             'loaded with load_from_folds().')

        cv = get_cv(self.cv)

        delayed_list = (
            delayed(fit_and_score)(self.algo_class(**params), trainset,
                                   testset, self.measures,
                                   self.return_train_measures)
            for params, (trainset, testset) in product(self.param_combinations,
                                                       cv.split(data))
        )
        out = Parallel(n_jobs=self.n_jobs,
                       pre_dispatch=self.pre_dispatch,
                       verbose=self.joblib_verbose)(delayed_list)

        (test_measures_dicts,
         train_measures_dicts,
         fit_times,
         test_times) = zip(*out)

        test_measures = dict()
        train_measures = dict()
        new_shape = (len(self.param_combinations), cv.get_n_folds())
        for m in self.measures:
            test_measures[m] = np.asarray([d[m] for d in test_measures_dicts])
            test_measures[m] = test_measures[m].reshape(new_shape)
            if self.return_train_measures:
                train_measures[m] = np.asarray([d[m] for d in
                                                train_measures_dicts])
                train_measures[m] = train_measures[m].reshape(new_shape)

        cv_results = dict()
        best_index = dict()
        best_params = dict()
        best_score = dict()
        best_estimator = dict()
        for m in self.measures:
            # cv_results: set measures for each split and each param comb
            for split in range(cv.get_n_folds()):
                cv_results['split{0}_test_{1}'.format(split, m)] = \
                    test_measures[m][:, split]
                if self.return_train_measures:
                    cv_results['split{0}_train_{1}'.format(split, m)] = \
                        train_measures[m][:, split]

            # cv_results: set mean and std over all splits (testset and
            # trainset) for each param comb
            mean_test_measures = test_measures[m].mean(axis=1)
            cv_results['mean_test_{}'.format(m)] = mean_test_measures
            cv_results['std_test_{}'.format(m)] = test_measures[m].std(axis=1)
            if self.return_train_measures:
                mean_train_measures = train_measures[m].mean(axis=1)
                cv_results['mean_train_{}'.format(m)] = mean_train_measures
                cv_results['std_train_{}'.format(m)] = \
                    train_measures[m].std(axis=1)

            # cv_results: set rank of each param comb
            indices = cv_results['mean_test_{}'.format(m)].argsort()
            cv_results['rank_test_{}'.format(m)] = np.empty_like(indices)
            cv_results['rank_test_{}'.format(m)][indices] = np.arange(
                len(indices)) + 1  # sklearn starts rankings at 1 as well.

            # set best_index, and best_xxxx attributes
            if m in ('mae', 'rmse'):
                best_index[m] = mean_test_measures.argmin()
            elif m in ('fcp',):
                best_index[m] = mean_test_measures.argmax()
            best_params[m] = self.param_combinations[best_index[m]]
            best_score[m] = mean_test_measures[best_index[m]]
            best_estimator[m] = self.algo_class(**best_params[m])

        # Cv results: set fit and train times (mean, std)
        fit_times = np.array(fit_times).reshape(new_shape)
        test_times = np.array(test_times).reshape(new_shape)
        for s, times in zip(('fit', 'test'), (fit_times, test_times)):
            cv_results['mean_{}_time'.format(s)] = times.mean(axis=1)
            cv_results['std_{}_time'.format(s)] = times.std(axis=1)

        # cv_results: set params key and each param_* values
        cv_results['params'] = self.param_combinations
        for param in self.param_combinations[0]:
            cv_results['param_' + param] = [comb[param] for comb in
                                            self.param_combinations]

        if self.refit:
            best_estimator[self.refit].fit(data.build_full_trainset())

        self.best_index = best_index
        self.best_params = best_params
        self.best_score = best_score
        self.best_estimator = best_estimator
        self.cv_results = cv_results

    def test(self, testset, verbose=False):
        """Call ``test()`` on the estimator with the best found parameters
        (according the the ``refit`` parameter). See :meth:`AlgoBase.test()
        <surprise.prediction_algorithms.algo_base.AlgoBase.test>`.

        Only available if ``refit`` is not ``False``.
        """

        if not self.refit:
            raise ValueError('refit is False, cannot use test()')

        return self.best_estimator[self.refit].test(testset, verbose)

    def predict(self, *args):
        """Call ``predict()`` on the estimator with the best found parameters
        (according the the ``refit`` parameter). See :meth:`AlgoBase.predict()
        <surprise.prediction_algorithms.algo_base.AlgoBase.predict>`.

        Only available if ``refit`` is not ``False``.
        """

        if not self.refit:
            raise ValueError('refit is False, cannot use predict()')

        return self.best_estimator[self.refit].predict(*args)


class GridSearchCV(BaseSearchCV):
    """The :class:`GridSearchCV` class computes accuracy metrics for an
    algorithm on various combinations of parameters, over a cross-validation
    procedure. This is useful for finding the best set of parameters for a
    prediction algorithm. It is analogous to `GridSearchCV
    <http://scikit-learn.org/stable/modules/generated/sklearn.
    model_selection.GridSearchCV.html>`_ from scikit-learn.

    See an example in the :ref:`User Guide <tuning_algorithm_parameters>`.

    Args:
        algo_class(:obj:`AlgoBase \
            <surprise.prediction_algorithms.algo_base.AlgoBase>`): The class
            of the algorithm to evaluate.
        param_grid(dict): Dictionary with algorithm parameters as keys and
            list of values as keys. All combinations will be evaluated with
            desired algorithm. Dict parameters such as ``sim_options`` require
            special treatment, see :ref:`this note<grid_search_note>`.
        measures(list of string): The performance measures to compute. Allowed
            names are function names as defined in the :mod:`accuracy
            <surprise.accuracy>` module.  Default is ``['rmse', 'mae']``.
        cv(cross-validation iterator, int or ``None``): Determines how the
            ``data`` parameter will be split (i.e. how trainsets and testsets
            will be defined). If an int is passed, :class:`KFold
            <surprise.model_selection.split.KFold>` is used with the
            appropriate ``n_splits`` parameter. If ``None``, :class:`KFold
            <surprise.model_selection.split.KFold>` is used with
            ``n_splits=5``.
        refit(bool or str): If ``True``, refit the algorithm on the whole
            dataset using the set of parameters that gave the best average
            performance for the first measure of ``measures``. Other measures
            can be used by passing a string (corresponding to the measure
            name). Then, you can use the ``test()`` and ``predict()`` methods.
            ``refit`` can only be used if the ``data`` parameter given to
            ``fit()`` hasn't been loaded with :meth:`load_from_folds()
            <surprise.dataset.Dataset.load_from_folds>`. Default is ``False``.
        return_train_measures(bool): Whether to compute performance measures on
            the trainsets. If ``True``, the ``cv_results`` attribute will
            also contain measures for trainsets. Default is ``False``.
        n_jobs(int): The maximum number of parallel training procedures.

            - If ``-1``, all CPUs are used.
            - If ``1`` is given, no parallel computing code is used at all,\
                which is useful for debugging.
            - For ``n_jobs`` below ``-1``, ``(n_cpus + n_jobs + 1)`` are\
                used.  For example, with ``n_jobs = -2`` all CPUs but one are\
                used.

            Default is ``1``.
        pre_dispatch(int or string): Controls the number of jobs that get
            dispatched during parallel execution. Reducing this number can be
            useful to avoid an explosion of memory consumption when more jobs
            get dispatched than CPUs can process. This parameter can be:

            - ``None``, in which case all the jobs are immediately created\
                and spawned. Use this for lightweight and fast-running\
                jobs, to avoid delays due to on-demand spawning of the\
                jobs.
            - An int, giving the exact number of total jobs that are\
                spawned.
            - A string, giving an expression as a function of ``n_jobs``,\
                as in ``'2*n_jobs'``.

            Default is ``'2*n_jobs'``.
        joblib_verbose(int): Controls the verbosity of joblib: the higher, the
            more messages.

    Attributes:
        best_estimator (dict of AlgoBase):
            Using an accuracy measure as key, get the algorithm that gave the
            best accuracy results for the chosen measure, averaged over all
            splits.
        best_score (dict of floats):
            Using an accuracy measure as key, get the best average score
            achieved for that measure.
        best_params (dict of dicts):
            Using an accuracy measure as key, get the parameters combination
            that gave the best accuracy results for the chosen measure (on
            average).
        best_index  (dict of ints):
            Using an accuracy measure as key, get the index that can be used
            with ``cv_results`` that achieved the highest accuracy for that
            measure (on average).
        cv_results (dict of arrays):
            A dict that contains accuracy measures over all splits, as well as
            train and test time for each parameter combination. Can be imported
            into a pandas `DataFrame` (see :ref:`example
            <cv_results_example>`).
    """
    def __init__(self, algo_class, param_grid, measures=['rmse', 'mae'],
                 cv=None, refit=False, return_train_measures=False, n_jobs=1,
                 pre_dispatch='2*n_jobs', joblib_verbose=0):

        super(GridSearchCV, self).__init__(
            algo_class=algo_class, measures=measures, cv=cv, refit=refit,
            return_train_measures=return_train_measures, n_jobs=n_jobs,
            pre_dispatch=pre_dispatch, joblib_verbose=joblib_verbose)

        self.param_grid = self._parse_options(param_grid.copy())
        self.param_combinations = [dict(zip(self.param_grid, v)) for v in
                                   product(*self.param_grid.values())]



