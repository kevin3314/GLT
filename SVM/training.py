import argparse
import copy
import os
import sys

import numpy as np
import pytest

import SVM.utils as utils
from SVM.SVM import FeatureFucntion
from SVM.utils import DIVIDER, parse_JSON


def main(args):
    # declaration magic numbers.
    CANDIDATES_NUMBER = 8

    # parse json files
    function_keys, programs, candidates = parse_JSON(args.input_dir)
    svm = FeatureFucntion(function_keys, candidates)
    svm.update_all_top_candidates(CANDIDATES_NUMBER)
    svm.subgrad(
        programs,
        utils.simple_sequence(0.03),
        utils.naive_loss,
        iterations=30,
        save_weight=args.output,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="train to get weight")
    parser.add_argument("-i", "--input", required=True, dest="input_dir")
    parser.add_argument("-o", "--output", required=True, dest="output")
    parser.add_argument("-w", "--weight", required=False, dest="pre_weight")
    args = parser.parse_args()

    main(args)
