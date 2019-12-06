# import os.path
# import operator
import argparse
import bisect
import collections
import copy
import json
import os
import pickle
import time
from multiprocessing import Pool
from functools import partial

import numpy as np
from tqdm import tqdm
from os.path import join

import utils as utils

DIVIDER = "区"


class FeatureFucntion:
    """Class for feature function.

    Attributes:
        function_keys : list :
            function_keys is list of feature.
            feature like: "id区((||区i"

        weight : np.ndarray :
            weight is weight to be learned.

        candidates_dict : dictionary:
            top S candidate dict.
            key is like "id区((||"

        candidates : LB :
            candidates of variable name.
    """

    NUM_PATH = 20  # the number of iterations of inference
    TOP_CANDIDATES = 16  # the number of candidates to regard

    def __init__(self, function_keys, candidates, label_seq_dict):
        self.function_keys = function_keys
        self.candidates = candidates
        self.label_seq_dict = label_seq_dict
        self.__weight = np.ones(len(function_keys))
        self._update_label_seq_dict()

    @property
    def weight(self):
        return self.__weight

    @weight.setter
    def weight(self, newval):
        self.__weight = newval
        self._update_label_seq_dict()

    def _update_label_seq_dict(self):
        # sort __label_seq_dict with weight value
        for key, value in self.label_seq_dict.items():
            # each value is (index, label)
            value.sort(key=lambda x: self.weight[x[0]], reverse=True)

    def eval(self, key, without_weight=False):
        if key in self.function_keys:
            index = self.function_keys[key]
            if without_weight:
                tmp = np.zeros(len(self.function_keys))
                tmp[index] = 1
                return tmp
            else:
                return self.weight[index]
        return 0

    def write_weight(self, key, value):
        if key in self.function_keys:
            index = self.function_keys[key]
            self.weight[index] = value
            self._update_label_seq_dict()

    def inference(self, x, loss=utils.dummy_loss, NUM_PATH=NUM_PATH, TOP_CANDIDATES=TOP_CANDIDATES):
        """inference program properties.
        x : program
        loss : loss function
        """
        # initialize y:answer
        y = []
        x = copy.deepcopy(x)
        gen = utils.token_generator()

        for st in x["y_names"]:
            index = st.find(DIVIDER)
            y.append(st[: index + 1] + next(gen))
        utils.relabel(y, x)

        for iter_n in range(NUM_PATH):
            # each node with unknown property in the G^x
            for i in range(len(x["y_names"])):
                variable = y[i]
                var_scope_id = int(utils.get_scopeid(variable))
                var_name = utils.get_varname(variable)
                candidates = set()
                edges = []
                connected_edges = []

                for key, edge in x.items():
                    if key == "y_names":
                        continue

                    if edge["type"] == "var-var":
                        if (
                            edge["xName"] == var_name
                            and edge["xScopeId"] == var_scope_id
                        ):
                            edges.append(edge)
                            connected_edges.append(
                                edge["yName"] + DIVIDER + edge["sequence"]
                            )

                        elif (
                            edge["yName"] == var_name
                            and edge["yScopeId"] == var_scope_id
                        ):
                            edges.append(edge)
                            connected_edges.append(
                                edge["xName"] + DIVIDER + edge["sequence"]
                            )

                    else:  # "var-lit"
                        if (
                            edge["xName"] == var_name
                            and edge["xScopeId"] == var_scope_id
                        ):
                            edges.append(edge)
                            connected_edges.append(
                                edge["yName"] + DIVIDER + edge["sequence"]
                            )

                # score = score_edge + loss function(if not provided, loss=0)
                score_v = self.score_edge(edges) + loss(x["y_names"], y)

                for edge in connected_edges:
                    if edge in self.label_seq_dict.keys():
                        for v in self.label_seq_dict[edge][:TOP_CANDIDATES]:
                            candidates.add(v[1])

                if not candidates:
                    continue

                for candidate in candidates:
                    pre_label = y[i]
                    # check duplicate
                    if utils.duplicate_check(y, var_scope_id, candidate):
                        continue

                    # temporaly relabel infered labels
                    y[i] = str(var_scope_id) + DIVIDER + candidate

                    # relabel edges with new label
                    utils.relabel_edges(
                        edges, pre_label, var_scope_id, candidate)

                    # score = score_edge + loss
                    new_score_v = self.score_edge(
                        edges) + loss(x["y_names"], y)

                    if new_score_v < score_v:  # when score is not improved
                        y[i] = pre_label
                        pre_name = utils.get_varname(pre_label)
                        utils.relabel_edges(edges, candidate, var_scope_id, pre_name)

        return y

    def inference_only_correct_number(self, program, **kwrags):
        y = self.inference(program, **kwrags)
        val = 0
        for a, b in zip(program["y_names"], y):
            if a == b:
                val += 1
        return val, len(y)

    def score(self, y, x, without_weight=False):
        assert len(y) == len(
            x["y_names"]
        ), "two length should be equal, but len(y):{0}, len(x):{1}".format(
            len(y), len(x["y_names"])
        )
        x = copy.deepcopy(x)
        utils.relabel(y, x)
        if without_weight:
            val = np.zeros(len(self.function_keys))
        else:
            val = 0
        for key in x:
            if key == "y_names":
                continue
            obj = x[key]
            x_name = obj["xName"]
            y_name = obj["yName"]
            seq = obj["sequence"]
            key_name = x_name + DIVIDER + seq + DIVIDER + y_name
            val += self.eval(key_name, without_weight=without_weight)
        return val

    def score_edge(self, edges):
        res = 0
        for edge in edges:
            x_name = edge["xName"]
            y_name = edge["yName"]
            seq = edge["sequence"]
            key_name = x_name + DIVIDER + seq + DIVIDER + y_name
            res += self.eval(key_name)
        return res

    def subgrad_mmsc(self, program, loss, only_loss=False):
        # this default g value may be wrong
        y_i = program["y_names"]
        y_star = self.inference(program, loss)
        loss = (
            self.score(y_star, program) + loss(y_star, y_i) -
            self.score(y_i, program)
        )
        if only_loss:
            return loss

        g = (self.score(y_star, program, without_weight=True) - self.score(y_i, program, without_weight=True))
        return g, loss

    def subgrad(self, programs, stepsize_sequence, loss_function, *, using_norm=False, iterations=30, save_dir=None, LAMBDA=0.5, BETA=0.5):
        def calc_l2_norm(weight):
            return np.linalg.norm(weight, ord=2) / 2 * LAMBDA

        # initialize
        weight_zero = np.ones(len(self.function_keys)) * (BETA / 2)
        self.weight = weight_zero
        weight_t = weight_zero

        # best loss, weight
        best_loss = 100000
        best_weight = weight_zero

        for i in tqdm(range(iterations)):
            # get newest weight
            sum_loss = 0

            # calculate grad
            subgrad_with_loss = partial(self.subgrad_mmsc, loss=loss_function)

            with Pool() as pool:
                res = pool.map(subgrad_with_loss, programs)

            grad, sum_loss = (sum(x) for x in zip(*res))

            grad /= len(programs)
            sum_loss /= len(programs)

            if using_norm:
                sum_loss += calc_l2_norm(weight_t)

            if sum_loss < best_loss:
                best_loss = sum_loss
                best_weight = weight_t

            new_weight = utils.projection(
                weight_t - next(stepsize_sequence) * grad, 0, BETA
            )

            self.weight = new_weight
            weight_t = new_weight

        sum_loss = 0
        # calculate loss for last weight
        subgrad_with_only_loss = partial(self.subgrad_mmsc, loss=loss_function, only_loss=True)
        with Pool() as pool:
            res = pool.map(subgrad_with_only_loss, programs)

        sum_loss = sum(res)
        sum_loss /= len(programs)
        if using_norm:
            sum_loss += calc_l2_norm(self.weight)

        # return weight for min loss
        if sum_loss < best_loss:
            best_loss = sum_loss
            best_weight = weight_t

        self.weight = best_weight
        if save_dir:
            self._make_pickles(save_dir)
        return best_weight

    def _make_pickles(self, save_dir):
        with open(join(save_dir, "svm.pickle"), mode="wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load_pickles(save_dir):
        with open(join(save_dir, "svm.pickle"), mode="rb") as f:
            svm = pickle.load(f)
        return svm


def main(args):
    function_keys, programs, candidates, label_seq_dict = utils.parse_JSON(args.input_dir)
    func = FeatureFucntion(function_keys, candidates, label_seq_dict)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="train to get weight")
    parser.add_argument("-i", "--input", required=True, dest="input_dir")
    # parser.add_argument("-o", "--output", required=True, dest="output")
    args = parser.parse_args()

    main(args)
