# import os.path
# import operator
import argparse
import bisect
import collections
import copy
import json
import os
import pickle

import numpy as np

import context2name.utils as utils

DIVIDER = "区"


class FeatureFucntion:
    """Class for feature function.

    Attributes:
        function_keys(list):
            function_keys is list of feature.
            feature like: (x, y, ["ArrayExpression", "CallExpression"])

        weight(np.ndarray):
            weight is weight to be learned.
    """

    TOP_CANDIDATES = 8

    def __init__(self, function_keys, candidates, weight_path=None):
        self.function_keys = function_keys
        self.weight = np.ones(len(function_keys))
        self.candidates = candidates
        if weight_path:
            self.weight = np.load(weight_path)
            self.update_all_top_candidates

    def eval(self, key, without_weight=False):
        if self.function_keys.contain(key):
            index = self.function_keys.index(key)
            if without_weight:
                tmp = np.zeros(len(self.function_keys))
                tmp[index] = 1
                return tmp
            else:
                return self.weight[index]
        return 0

    def write_weight(self, key, value):
        if self.function_keys.contain(key):
            index = self.function_keys.index(key)
            self.weight[index] = value

    def inference(self, x, loss=utils.dummy_loss):
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
        y_tmp = utils.remove_number(y)
        utils.relabel(y_tmp, x)
        num_path = 30  # the number of iterations.

        for i in range(num_path):
            # each node with unknown property in the G^x
            for i in range(len(x["y_names"])):
                variable = y[i]
                index = variable.find(DIVIDER)
                var_scope_id = int(variable[:index])
                var_name = variable[index + 1 :]
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
                            edges.append(copy.deepcopy(edge))
                            connected_edges.append(
                                edge["yName"] + DIVIDER + edge["sequence"]
                            )

                        elif (
                            edge["yName"] == var_name
                            and edge["yScopeId"] == var_scope_id
                        ):
                            edges.append(copy.deepcopy(edge))
                            connected_edges.append(
                                edge["xName"] + DIVIDER + edge["sequence"]
                            )

                    else:  # "var-lit"
                        if (
                            edge["xName"] == var_name
                            and edge["xScopeId"] == var_scope_id
                        ):
                            edges.append(copy.deepcopy(edge))
                            connected_edges.append(
                                edge["yName"] + DIVIDER + edge["sequence"]
                            )

                # score = score_edge + loss function(if not provided, loss=0)
                score_v = self.score_edge(edges) + loss(x["y_names"], y)

                for edge in connected_edges:
                    if edge in self.candidates_dict.keys():
                        candidates = candidates.union(self.candidates_dict[edge])

                if not candidates:
                    continue

                for candidate in candidates:
                    saved_edges = copy.deepcopy(edges)

                    # temporaly relabel infered labels
                    tmp_y = copy.copy(y)
                    tmp_y[i] = str(var_scope_id) + DIVIDER + candidate

                    # relabel edges with new label
                    utils.relabel_edges(edges, var_name, var_scope_id, candidate)

                    # score = score_edge + loss
                    new_score_v = self.score_edge(edges) + loss(x["y_names"], tmp_y)
                    if new_score_v > score_v:
                        # check duplicate
                        if utils.duplicate_check(y, var_scope_id, candidate):
                            continue
                        y[i] = str(var_scope_id) + DIVIDER + candidate
                        utils.relabel(y, x)

                    else:
                        edges = saved_edges
        return y

    def score(self, y, x, without_weight=False):
        assert len(y) == len(
            x["y_names"]
        ), "two length should be equal, but len(y):{0}, len(x):{1}".format(
            len(y), len(x["y_names"])
        )
        y = utils.remove_number(y)
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

    def top_candidates(self, label, rel, s):
        candidate_keys = utils.ListForBitsect()
        for key in self.function_keys:
            x_index = key.find(DIVIDER)
            y_index = key.rfind(DIVIDER)
            x = key[:x_index]
            y = key[y_index + 1 :]
            seq = key[x_index + 1 : y_index]
            if (
                (label == x and self.candidates.contain(y))
                or (label == y and self.candidates.contain(x))
                and rel == seq
            ):
                candidate_keys.append(key)

        candidate_keys.sort(key=lambda x: self.eval(x), reverse=True)
        tmp_candidates = candidate_keys[:s]

        tmp = []
        for v in tmp_candidates:
            x_index = v.find(DIVIDER)
            y_index = v.rfind(DIVIDER)
            x = v[:x_index]
            y = v[y_index + 1 :]

            # v[0] is set of keys
            if x == label:
                tmp.append(y)
            else:
                tmp.append(x)
        return tmp

    def update_all_top_candidates(self, s=TOP_CANDIDATES):
        candidates_dict = {}
        already_added = utils.ListForBitsect()
        for key in self.function_keys:
            x_index = key.find(DIVIDER)
            y_index = key.rfind(DIVIDER)
            x = key[:x_index]
            y = key[y_index + 1 :]
            seq = key[x_index + 1 : y_index]
            for v in (x, y):
                node_seq = v + DIVIDER + seq
                if already_added.contain(node_seq):
                    continue
                already_added.append(v)
                candidates = self.top_candidates(v, seq, s)
                candidates_dict[node_seq] = candidates

        self.candidates_dict = candidates_dict

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
            self.score(y_star, program) + loss(y_star, y_i) - self.score(y_i, program)
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
        self.update_all_top_candidates()
        weights = [weight_zero]
        losses = []

        for i in range(iterations):
            # get newest weight
            weight_t = weights[-1]
            sum_loss = 0

            # calculate grad
            grad = np.zeros(len(self.function_keys))
            for program in programs:
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
            self.update_all_top_candidates()

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
    function_keys, programs, candidates = utils.parse_JSON(args.input_dir)
    func = FeatureFucntion(function_keys, candidates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="train to get weight")
    parser.add_argument("-i", "--input", required=True, dest="input_dir")
    # parser.add_argument("-o", "--output", required=True, dest="output")
    args = parser.parse_args()

    main(args)