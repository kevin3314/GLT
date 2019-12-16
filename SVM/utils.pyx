# distutils: language = c++
# cython: language_level=3

import bisect
import json
import math
import os
from tqdm import tqdm
import sys
from itertools import chain
from collections import deque, defaultdict

import numpy as np

cimport simdjson.csimdjson as csimdjson

DIVIDER = "区"


def parse_JSON(input_path):
    count_dict = defaultdict(int)
    function_keys = {}
    program_paths = []
    candidates = {}
    label_seq_dict = {}

    i = 0

    if isinstance(input_path, list):
        # when input path is list of json path.
        json_files = input_path
        input_path = ""
    elif os.path.isdir(input_path):
        # when path is directory path
        json_files = [
            x
            for x in os.listdir(input_path)
            if not x.startswith(".") and x[-5:] == ".json"
        ]
    elif os.path.isfile(input_path):
        # when path is file path
        if input_path[-5:] != ".json":
            raise Exception("input file is not json!")
        json_files = [input_path]
        input_path = ""

    # temporaly map for obj type
    tmp_map = {}
    for filename in tqdm(json_files):
        file_path = os.path.join(input_path, filename)
        with open(file_path, "r") as f:
            jsonData = json.load(f)
        program = jsonData
        program_paths.append(file_path)

        for key2 in program:
            if key2 == "y_names":
                for val in program[key2]:
                    varname = get_varname(val)
                    if not(varname in candidates):
                        candidates[varname] = 0
                continue

            obj = program[key2]
            x = obj["xName"]
            y = obj["yName"]
            seq = obj["sequence"]
            key_name = x + DIVIDER + seq + DIVIDER + y

            count_dict[key_name] += 1
            tmp_map[key_name] = obj

    delete = [key for key, value in count_dict.items() if value <= 5]
    for key in delete:
        del count_dict[key]

    for i, key in enumerate(count_dict.keys()):
        function_keys[key] = i

        # update label_seq_dict
        obj = tmp_map[key]
        x = obj["xName"]
        y = obj["yName"]
        seq = obj["sequence"]
        if obj["type"] == "var-var":  # when edge is var-var
            x_seq = x + DIVIDER + seq
            y_seq = y + DIVIDER + seq
            t_list = [(x_seq, y), (y_seq, x)]
        else:  # when edge is var-lit
            y_seq = y + DIVIDER + seq
            t_list = [(y_seq, x)]

        for value in t_list:
            if value[0] in label_seq_dict:
                label_seq_dict[value[0]].append((i, value[1]))
            else:
                label_seq_dict[value[0]] = [(i, value[1])]

    programs = program_gen(program_paths)

    return function_keys, programs, candidates, label_seq_dict


class program_gen:
    def __init__(self, program_paths):
        self.program_paths = program_paths
        self.len = len(program_paths)

    def __len__(self):
        return len(self.program_paths)

    def __iter__(self):
        for path in self.program_paths:
            with open(path, "r") as f:
                jsonData = json.load(f)
            yield jsonData


def remove_number(y):
    tmp = []
    for st in y:
        index = st.find(DIVIDER)
        tmp.append(st[index + 1 :])
    return tmp


def get_varname(label):
    """ label: "1区var" => var
    """
    index = label.find(DIVIDER)
    return label[index+1:]


def get_scopeid(label):
    """ label: "1区var" => index
    """
    index = label.find(DIVIDER)
    return label[:index]


def duplicate_check(y, scope_id, varname):
    """var -> "1区index"
    if duplicate, return True
    """
    for var in y:
        var_scopeid = get_scopeid(var)
        var_name = get_varname(var)
        if var_scopeid == scope_id and var_name == varname:
            return True

    return False


def relabel(y, x, verbose=False):
    """ relabel program with y.
    """
    y_names = x["y_names"]
    # replace in node
    for key in x:
        if key == "y_names":
            continue
        obj = x[key]

        if obj["type"] == "var-var":
            # x, y representing in x["y_names"]
            x_in_ynames = str(obj["xScopeId"]) + DIVIDER + obj["xName"]
            y_in_ynames = str(obj["yScopeId"]) + DIVIDER + obj["yName"]

            # search x, y in x["y_names"] and replace it with
            # correscpondig indexed element in y (infered variable name)
            obj["xName"] = get_varname(y[y_names.index(x_in_ynames)])
            obj["yName"] = get_varname(y[y_names.index(y_in_ynames)])

            if verbose:
                print(obj["xName"])
                print(obj["yName"])

        elif obj["type"] == "var-lit":
            x_in_ynames = str(obj["xScopeId"]) + DIVIDER + obj["xName"]
            obj["xName"] = get_varname(y[y_names.index(x_in_ynames)])
            if verbose:
                print(obj["xName"])

    # replace in y_names
    x["y_names"] = y


def relabel_edges(edges, old_name, old_scope_id, new_name):
    for edge in edges:
        if edge["type"] == "var-var":
            # replace old_name with new_name
            if edge["xName"] == old_name and edge["xScopeId"] == int(old_scope_id):
                edge["xName"] = new_name
            if edge["yName"] == old_name and edge["yScopeId"] == int(old_scope_id):
                edge["yName"] = new_name

        else:  # "var-lit"
            if edge["xName"] == old_name and edge["xScopeId"] == int(old_scope_id):
                edge["xName"] = new_name


def projection(weight, under, upper):
    """projection weight into correct domain
    """
    for i, x in enumerate(weight):
        tmp = max(under, min(upper, x))
        weight[i] = tmp
    return weight

####################################################################
################### loss function for two label ####################
####################################################################

def dummy_loss(y, y_star):
    """dummy loss to return nothing
    """
    return 0

def naive_loss(y, y_star):
    """given two label sequence, calcluate loss by
    simply counting diffrent labes.
    """
    res = 0
    for x, y in zip(y, y_star):
        if x != y:
            res += 1
    return res


####################################################################
###############  generator for stepsize sequence   #################
####################################################################


def simple_sequence(c):
    t = 1.0
    while True:
        yield c / t
        t += 1.0


def sqrt_sequence(c):
    t = 1.0
    while True:
        yield c / math.sqrt(t)
        t += 1.0


####################################################################
###############  generator for initial token       #################
####################################################################


def token_generator():
    ASCII_NUMBER = 33
    i = ASCII_NUMBER
    while True:
        yield chr(i)
        i += 1