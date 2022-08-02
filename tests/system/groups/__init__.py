import os.path

from utils import get_resource


def groups_dir():
    return os.path.dirname(os.path.abspath(__file__))


def load_group(group_name):
    group_path = os.path.join(groups_dir(), f"{group_name}.json")
    return get_resource(group_path)


def sleep_group():
    return load_group('sleep-group')
