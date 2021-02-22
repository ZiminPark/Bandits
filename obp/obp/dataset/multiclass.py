# Copyright (c) Yuta Saito, Yusuke Narita, and ZOZO Technologies, Inc. All rights reserved.
# Licensed under the Apache 2.0 License.

"""Class for Multi-Class Classification to Bandit Reduction."""
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np
from scipy.stats import rankdata
from sklearn.base import ClassifierMixin, is_classifier, clone
from sklearn.model_selection import train_test_split
from sklearn.utils import check_random_state, check_X_y

from .base import BaseBanditDataset
from ..types import BanditFeedback


@dataclass
class MultiClassToBanditReduction(BaseBanditDataset):
    """Class for handling multi-class classification data as logged bandit feedback data.

    Note
    -----
    A machine learning classifier such as logistic regression is used to construct behavior and evaluation policies as follows.

        1. Split the original data into training (:math:`\\mathcal{D}_{\\mathrm{tr}}`) and evaluation (:math:`\\mathcal{D}_{\\mathrm{ev}}`) sets.
        2. Train classifiers on :math:`\\mathcal{D}_{\\mathrm{tr}}` and obtain base deterministic policies :math:`\\pi_{\\mathrm{det},b}` and :math:`\\pi_{\\mathrm{det},e}`.
        3. Construct behavior (:math:`\\pi_{b}`) and evaluation (:math:`\\pi_{e}`) policies based on :math:`\\pi_{\\mathrm{det},b}` and :math:`\\pi_{\\mathrm{det},e}` as

            .. math::

                \\pi_b (a | x) := \\alpha_b \\cdot \\pi_{\\mathrm{det},b} (a|x) + (1.0 - \\alpha_b) \\cdot \\pi_{u} (a|x)

            .. math::

                \\pi_e (a | x) := \\alpha_e \\cdot \\pi_{\\mathrm{det},e} (a|x) + (1.0 - \\alpha_e) \\cdot \\pi_{u} (a|x)

            where :math:`\\pi_{u}` is a uniform random policy and :math:`\\alpha_b` and :math:`\\alpha_e` are set by the user.

        4. Measure the accuracy of the evaluation policy on :math:`\\mathcal{D}_{\\mathrm{ev}}` with its fully observed rewards
        and use it as the evaluation policy's ground truth policy value.

        5. Using :math:`\\mathcal{D}_{\\mathrm{ev}}`, an estimator :math:`\\hat{V}` estimates the policy value of the evaluation policy, i.e.,

            .. math::

                V(\\pi_e) \\approx \\hat{V} (\\pi_e; \\mathcal{D}_{\\mathrm{ev}})

        6. Evaluate the estimation performance of :math:`\\hat{V}` by comparing its estimate with the ground-truth policy value.

    Parameters
    -----------
    X: array-like, shape (n_rounds,n_features)
        Training vector of the original multi-class classification data,
        where n_rounds is the number of samples and n_features is the number of features.

    y: array-like, shape (n_rounds,)
        Target vector (relative to X) of the original multi-class classification data.

    base_classifier_b: ClassifierMixin
        Machine learning classifier used to construct a behavior policy.

    alpha_b: float, default=0.9
        Ration of a uniform random policy when constructing a **behavior** policy.
        Must be in the [0, 1) interval to make the behavior policy a stochastic one.

    dataset_name: str, default=None
        Name of the dataset.

    Examples
    ----------

    .. code-block:: python

        # evaluate the estimation performance of IPW using the `digits` data in sklearn
        >>> import numpy as np
        >>> from sklearn.datasets import load_digits
        >>> from sklearn.linear_model import LogisticRegression
        # import open bandit pipeline (obp)
        >>> from obp.dataset import MultiClassToBanditReduction
        >>> from obp.ope import OffPolicyEvaluation, InverseProbabilityWeighting as IPW

        # load raw digits data
        >>> X, y = load_digits(return_X_y=True)
        # convert the raw classification data into the logged bandit dataset
        >>> dataset = MultiClassToBanditReduction(
            X=X,
            y=y,
            base_classifier_b=LogisticRegression(random_state=12345),
            alpha_b=0.8,
            dataset_name="digits",
        )
        # split the original data into the training and evaluation sets
        >>> dataset.split_train_eval(eval_size=0.7, random_state=12345)
        # obtain logged bandit feedback generated by behavior policy
        >>> bandit_feedback = dataset.obtain_batch_bandit_feedback(random_state=12345)
        >>> bandit_feedback
        {
            'n_actions': 10,
            'n_rounds': 1258,
            'context': array([[ 0.,  0.,  0., ..., 16.,  1.,  0.],
                    [ 0.,  0.,  7., ..., 16.,  3.,  0.],
                    [ 0.,  0., 12., ...,  8.,  0.,  0.],
                    ...,
                    [ 0.,  1., 13., ...,  8., 11.,  1.],
                    [ 0.,  0., 15., ...,  0.,  0.,  0.],
                    [ 0.,  0.,  4., ..., 15.,  3.,  0.]]),
            'action': array([6, 8, 5, ..., 2, 5, 9]),
            'reward': array([1., 1., 1., ..., 1., 1., 1.]),
            'position': None,
            'pscore': array([0.82, 0.82, 0.82, ..., 0.82, 0.82, 0.82])
        }

        # obtain action choice probabilities by an evaluation policy and its ground-truth policy value
        >>> action_dist = dataset.obtain_action_dist_by_eval_policy(
            base_classifier_e=LogisticRegression(C=100, random_state=12345),
            alpha_e=0.9,
        )
        >>> ground_truth = dataset.calc_ground_truth_policy_value(action_dist=action_dist)
        >>> ground_truth
        0.865643879173291

        # off-policy evaluation using IPW
        >>> ope = OffPolicyEvaluation(bandit_feedback=bandit_feedback, ope_estimators=[IPW()])
        >>> estimated_policy_value = ope.estimate_policy_values(action_dist=action_dist)
        >>> estimated_policy_value
        {'ipw': 0.8662705029276045}

        # evaluate the estimation performance (accuracy) of IPW by relative estimation error (relative-ee)
        >>> relative_estimation_errors = ope.evaluate_performance_of_estimators(
                ground_truth_policy_value=ground_truth,
                action_dist=action_dist,
            )
        >>> relative_estimation_errors
        {'ipw': 0.000723881690137968}

    References
    ------------
    Miroslav Dudík, Dumitru Erhan, John Langford, and Lihong Li.
    "Doubly Robust Policy Evaluation and Optimization.", 2014.

    """

    X: np.ndarray
    y: np.ndarray
    base_classifier_b: ClassifierMixin
    alpha_b: float = 0.8
    dataset_name: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize Class."""
        if not is_classifier(self.base_classifier_b):
            raise ValueError(f"base_classifier_b must be a classifier")
        if not isinstance(self.alpha_b, float) or not (0.0 <= self.alpha_b < 1.0):
            raise ValueError(
                f"alpha_b must be a float in the [0,1) interval, but {self.alpha_b} is given"
            )

        self.X, y = check_X_y(X=self.X, y=self.y, ensure_2d=True, multi_output=False)
        self.y = (rankdata(y, "dense") - 1).astype(int)  # re-index action
        # fully observed labels
        self.y_full = np.zeros((self.n_rounds, self.n_actions))
        self.y_full[np.arange(self.n_rounds), y] = 1

    @property
    def len_list(self) -> int:
        """Length of recommendation lists."""
        return 1

    @property
    def n_actions(self) -> int:
        """Number of actions (number of classes)."""
        return np.unique(self.y).shape[0]

    @property
    def n_rounds(self) -> int:
        """Number of samples in the original multi-class classification data."""
        return self.y.shape[0]

    def split_train_eval(
        self,
        eval_size: Union[int, float] = 0.25,
        random_state: Optional[int] = None,
    ) -> None:
        """Split the original data into the training (used for policy learning) and evaluation (used for OPE) sets.

        Parameters
        ----------
        eval_size: float or int, default=0.25
            If float, should be between 0.0 and 1.0 and represent the proportion of the dataset to include in the evaluation split.
            If int, represents the absolute number of test samples.

        random_state: int, default=None
            Controls the random seed in train-evaluation split.

        """
        (
            self.X_tr,
            self.X_ev,
            self.y_tr,
            self.y_ev,
            _,
            self.y_full_ev,
        ) = train_test_split(
            self.X, self.y, self.y_full, test_size=eval_size, random_state=random_state
        )
        self.n_rounds_ev = self.X_ev.shape[0]

    def obtain_batch_bandit_feedback(
        self,
        random_state: Optional[int] = None,
    ) -> BanditFeedback:
        """Obtain batch logged bandit feedback, an evaluation policy, and its ground-truth policy value.

        Note
        -------
        Please call `self.split_train_eval()` before calling this method.

        Parameters
        -----------
        random_state: int, default=None
            Controls the random seed in sampling actions.

        Returns
        ---------
        bandit_feedback: BanditFeedback
            bandit_feedback is logged bandit feedback data generated from a multi-class classification dataset.

        """
        random_ = check_random_state(random_state)
        # train a base ML classifier
        base_clf_b = clone(self.base_classifier_b)
        base_clf_b.fit(X=self.X_tr, y=self.y_tr)
        preds = base_clf_b.predict(self.X_ev).astype(int)
        # construct a behavior policy
        pi_b = np.zeros((self.n_rounds_ev, self.n_actions))
        pi_b[:, :] = (1.0 - self.alpha_b) / self.n_actions
        pi_b[np.arange(self.n_rounds_ev), preds] = (
            self.alpha_b + (1.0 - self.alpha_b) / self.n_actions
        )
        # sample action and factual reward based on the behavior policy
        action = np.zeros(self.n_rounds_ev, dtype=int)
        for i, p in enumerate(pi_b):
            action[i] = random_.choice(
                np.arange(self.n_actions, dtype=int), p=p, replace=False
            )
        reward = self.y_full_ev[np.arange(self.n_rounds_ev), action]

        return dict(
            n_actions=self.n_actions,
            n_rounds=self.n_rounds_ev,
            context=self.X_ev,
            action=action,
            reward=reward,
            position=None,  # position effect is not considered in classification data
            pscore=pi_b[np.arange(self.n_rounds_ev), action],
        )

    def obtain_action_dist_by_eval_policy(
        self, base_classifier_e: Optional[ClassifierMixin] = None, alpha_e: float = 1.0
    ) -> np.ndarray:
        """Obtain action choice probabilities by an evaluation policy.

        Parameters
        -----------
        base_classifier_e: ClassifierMixin, default=None
            Machine learning classifier used to construct a behavior policy.

        alpha_e: float, default=1.0
            Ration of a uniform random policy when constructing an **evaluation** policy.
            Must be in the [0, 1] interval (evaluation policy can be deterministic).

        Returns
        ---------
        action_dist_by_eval_policy: array-like, shape (n_rounds_ev, n_actions, 1)
            action_dist_by_eval_policy is an action choice probabilities by an evaluation policy.
            where n_rounds_ev is the number of samples in the evaluation set given the current train-eval split.
            n_actions is the number of actions.
            axis 2 represents the length of list; it is always 1 in the current implementation.

        """
        if not isinstance(alpha_e, float) or not (0.0 <= alpha_e <= 1.0):
            raise ValueError(
                f"alpha_e must be a float in the [0,1] interval, but {alpha_e} is given"
            )
        # train a base ML classifier
        if base_classifier_e is None:
            base_clf_e = clone(self.base_classifier_b)
        else:
            assert is_classifier(
                base_classifier_e
            ), f"base_classifier_e must be a classifier"
            base_clf_e = clone(base_classifier_e)
        base_clf_e.fit(X=self.X_tr, y=self.y_tr)
        preds = base_clf_e.predict(self.X_ev).astype(int)
        # construct an evaluation policy
        pi_e = np.zeros((self.n_rounds_ev, self.n_actions))
        pi_e[:, :] = (1.0 - alpha_e) / self.n_actions
        pi_e[np.arange(self.n_rounds_ev), preds] = (
            alpha_e + (1.0 - alpha_e) / self.n_actions
        )
        return np.expand_dims(pi_e, 2)

    def calc_ground_truth_policy_value(self, action_dist: np.ndarray) -> np.ndarray:
        """Calculate the ground-truth policy value of a given action distribution.

        Parameters
        ----------
        action_dist: array-like, shape (n_rounds_ev, n_actions, 1)
            Action distribution or action choice probabilities of a policy whose ground-truth is to be caliculated here.
            where n_rounds_ev is the number of samples in the evaluation set given the current train-eval split.
            n_actions is the number of actions.
            axis 2 of action_dist represents the length of list; it is always 1 in the current implementation.

        Returns
        ---------
        ground_truth_policy_value: float
            policy value of a given action distribution (mostly evaluation policy).

        """
        if not isinstance(action_dist, np.ndarray) or action_dist.ndim != 3:
            raise ValueError(f"action_dist must be a 3-D np.ndarray")
        if action_dist.shape[0] != self.n_rounds_ev:
            raise ValueError(
                "the size of axis 0 of action_dist must be the same as the number of samples in the evaluation set"
            )
        return action_dist[np.arange(self.n_rounds_ev), self.y_ev].mean()
