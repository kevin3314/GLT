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

import numpy as np
from tqdm import tqdm

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
    TOP_CANDIDATES = 8  # the number of candidates to regard

    def __init__(self, function_keys, candidates, label_seq_dict, weight_path=None):
        self.function_keys = function_keys
        self.candidates = candidates
        self.label_seq_dict = label_seq_dict
        if weight_path:
            self.__weight = np.load(weight_path)
        else:
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

        g = np.zeros(len(self.function_keys))
        g = (
            g
            + self.score(y_star, program, without_weight=True)
            - self.score(y_i, program, without_weight=True)
        )
        return g, loss

    def subgrad(self, programs, stepsize_sequence, loss_function, iterations=100, save_weight=None, LAMBDA=0.5):
        def calc_l2_norm(weight):
            return np.linalg.norm(weight, ord=2) / 2 * LAMBDA

        # initialize
        weight_zero = np.ones(len(self.function_keys)) * 0.15
        self.weight = weight_zero
        weights = [weight_zero]
        losses = []

        for i in range(iterations):
            # get newest weight
            weight_t = weights[-1]
            sum_loss = 0

            # calculate grad
            grad = np.zeros(len(self.function_keys))
            for program in tqdm(programs):
                g_t, loss = self.subgrad_mmsc(program, loss_function)
                grad += g_t
                sum_loss += loss

            sum_loss /= len(programs)
            sum_loss += calc_l2_norm(weight_t)
            losses.append(sum_loss)

            new_weight = utils.projection(
                weight_t - next(stepsize_sequence) * grad, 0, 0.5
            )
            weights.append(new_weight)
            self.weight = new_weight

        sum_loss = 0
        # calculate loss for last weight
        for program in programs:
            loss = self.subgrad_mmsc(program, loss_function, only_loss=True)
            sum_loss += loss
        sum_loss /= len(programs)
        sum_loss += calc_l2_norm(self.weight)

        # return weight for min loss
        losses.append(sum_loss)
        min_index = np.argmin(losses)
        res_weight = weights[min_index]
        if save_weight:
            np.save(save_weight, res_weight)
        return res_weight


def main(args):
    function_keys, programs, candidates, label_seq_dict = utils.parse_JSON(args.input_dir)
    func = FeatureFucntion(function_keys, candidates, label_seq_dict)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="train to get weight")
    parser.add_argument("-i", "--input", required=True, dest="input_dir")
    # parser.add_argument("-o", "--output", required=True, dest="output")
    args = parser.parse_args()

    main(args)