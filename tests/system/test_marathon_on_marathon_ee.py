""" Test using enterprise marathon on marathon (MoM-EE). The individual steps
    to install MoM-EE are well documented here:
    https://wiki.mesosphere.com/display/DCOS/MoM+1.4
"""

import apps
import common
import fixtures
import os
import pytest
import requests
import shakedown
import json
import logging

import shakedown.dcos.service
from shakedown.clients import dcos_url, marathon
from shakedown.clients.authentication import dcos_acs_token, DCOSAcsAuth
from shakedown.clients.rpcclient import verify_ssl
from shakedown.dcos.cluster import ee_version # NOQA F401
from shakedown.dcos.marathon import delete_all_apps, deployment_wait, marathon_on_marathon
from shakedown.dcos.service import service_available_predicate, get_service_task
from urllib.parse import urljoin
from utils import get_resource
from fixtures import install_enterprise_cli # NOQA F401

logger = logging.getLogger(__name__)

MOM_EE_NAME = 'marathon-user-ee'
MOM_EE_SERVICE_ACCOUNT = 'marathon_user_ee'
MOM_EE_SERVICE_ACCOUNT_SECRET_NAME = 'service-credentials'
MOM_EE_DOCKER_CONFIG_SECRET_NAME = 'docker-credentials'

PRIVATE_KEY_FILE = 'private-key.pem'
PUBLIC_KEY_FILE = 'public-key.pem'

DEFAULT_MOM_IMAGES = {
    'MOM_EE_1.7': 'v1.7.181_1.12.1',
    'MOM_EE_1.6': 'v1.6.335_1.11.0',
    'MOM_EE_1.5': 'v1.5.5_1.10.2'
}


def is_mom_ee_deployed():
    mom_ee_id = f'/{MOM_EE_NAME}'
    client = marathon.create_client()
    apps = client.get_apps()
    return any(app['id'] == mom_ee_id for app in apps)


def remove_mom_ee():
    mom_ee_versions = [
        ('1.7', 'strict'),
        ('1.7', 'permissive'),
        ('1.6', 'strict'),
        ('1.6', 'permissive'),
        ('1.5', 'strict'),
        ('1.5', 'permissive')
    ]
    for mom_ee in mom_ee_versions:
        endpoint = mom_ee_endpoint(mom_ee[0], mom_ee[1])
        logger.info(f'Checking endpoint: {endpoint}')
        if service_available_predicate(endpoint):
            logger.info(f'Removing {endpoint}...')
            with marathon_on_marathon(name=endpoint) as client:
                delete_all_apps(client=client)

    client = marathon.create_client()
    client.remove_app(MOM_EE_NAME)
    deployment_wait(MOM_EE_NAME)
    logger.info(f'Successfully removed {MOM_EE_NAME}')


def mom_ee_image(version):
    image_name = f'MOM_EE_{version}'
    try:
        os.environ[image_name]
    except Exception:
        default_image = DEFAULT_MOM_IMAGES[image_name]
        logger.info(
            f'No environment override found for MoM-EE  v{version}. Using default image {default_image}'
        )

        return default_image


def mom_ee_endpoint(version, security_mode):
    # '1.3', 'permissive' -> marathon-user-ee-permissive-1-3
    return f"{MOM_EE_NAME}-{security_mode}-{version.replace('.', '-')}"


def assert_mom_ee(version, security_mode='permissive'):
    ensure_service_account()
    ensure_permissions()
    ensure_sa_secret(strict=security_mode == 'strict')
    ensure_docker_config_secret()

    # In strict mode all tasks are started as user `nobody` by default. However we start
    # MoM-EE as 'root' and for that we need to give root marathon ACLs to start
    # tasks as 'root'.
    if security_mode == 'strict':
        common.add_dcos_marathon_user_acls()

    # Deploy MoM-EE in permissive mode
    app_def_file = (
        f'{fixtures.fixtures_dir()}/mom-ee-{security_mode}-{version}.json'
    )

    assert os.path.isfile(
        app_def_file
    ), f"Couldn't find appropriate MoM-EE definition: {app_def_file}"


    image = mom_ee_image(version)
    logger.info(f'Deploying {app_def_file} definition with {image} image')

    app_def = get_resource(app_def_file)
    app_def['container']['docker'][
        'image'
    ] = f'mesosphere/marathon-dcos-ee:{image}'

    app_id = app_def["id"]

    client = marathon.create_client()
    client.add_app(app_def)
    deployment_wait(service_id=app_id)
    shakedown.dcos.service.wait_for_service_endpoint(mom_ee_endpoint(version, security_mode), path="ping")


# strict security mode
@pytest.mark.skipif('shakedown.dcos.agent.required_private_agents(1)')
@shakedown.dcos.cluster.strict
@pytest.mark.parametrize("version,security_mode", [
    ('1.7', 'strict'),
    ('1.6', 'strict'),
    ('1.5', 'strict')
])
def test_strict_mom_ee(version, security_mode):
    assert_mom_ee(version, security_mode)
    assert simple_sleep_app(mom_ee_endpoint(version, security_mode))


# permissive security mode
@pytest.mark.skipif('shakedown.dcos.agent.required_private_agents(1)')
@shakedown.dcos.cluster.permissive
@pytest.mark.parametrize("version,security_mode", [
    ('1.7', 'permissive'),
    ('1.6', 'permissive'),
    ('1.5', 'permissive'),
])
def test_permissive_mom_ee(version, security_mode):
    assert_mom_ee(version, security_mode)
    assert simple_sleep_app(mom_ee_endpoint(version, security_mode))


def simple_sleep_app(mom_endpoint):
    # Deploy a simple sleep app in the MoM-EE
    with marathon_on_marathon(name=mom_endpoint) as client:
        app_def = apps.sleep_app()
        app_id = app_def["id"]

        client.add_app(app_def)
        deployment_wait(service_id=app_id, client=client)

        tasks = get_service_task(mom_endpoint, app_id.lstrip("/"))
        logger.info(f'MoM-EE tasks: {tasks}')
        return tasks is not None


def ensure_service_account():
    """Method creates a MoM-EE service account. It relies on the global `install_enterprise_cli`
       fixture to install the enterprise-cli-package.
    """
    if common.has_service_account(MOM_EE_SERVICE_ACCOUNT):
        common.delete_service_account(MOM_EE_SERVICE_ACCOUNT)
    common.create_service_account(MOM_EE_SERVICE_ACCOUNT, PRIVATE_KEY_FILE, PUBLIC_KEY_FILE)
    assert common.has_service_account(MOM_EE_SERVICE_ACCOUNT)


def ensure_permissions():
    common.set_service_account_permissions(MOM_EE_SERVICE_ACCOUNT)

    url = urljoin(
        dcos_url(),
        f'acs/api/v1/acls/dcos:superuser/users/{MOM_EE_SERVICE_ACCOUNT}',
    )

    auth = DCOSAcsAuth(dcos_acs_token())
    req = requests.get(url, auth=auth, verify=verify_ssl())
    expected = (
        f'/acs/api/v1/acls/dcos:superuser/users/{MOM_EE_SERVICE_ACCOUNT}/full'
    )

    assert req.json()['array'][0]['url'] == expected, "Service account permissions couldn't be set"


def ensure_sa_secret(strict=False):
    """Method creates a secret with MoM-EE service account private key. It relies on the global
       `install_enterprise_cli` fixture to install the enterprise-cli-package.
    """
    if common.has_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME):
        common.delete_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME)
    common.create_sa_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME, MOM_EE_SERVICE_ACCOUNT, strict)
    assert common.has_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME)


def ensure_docker_config_secret():
    """Method creates a secret with the docker credentials that is later used to pull
       the image from our private docker repository. It relies on the global
       `install_enterprise_cli` fixture to install the enterprise-cli-package.
    """
    # Docker username and password should be passed  as environment variables `DOCKER_HUB_USERNAME`
    # and `DOCKER_HUB_PASSWORD` (usually by jenkins)
    assert 'DOCKER_HUB_USERNAME' in os.environ, "Couldn't find docker hub username. $DOCKER_HUB_USERNAME is not set"
    assert 'DOCKER_HUB_PASSWORD' in os.environ, "Couldn't find docker hub password. $DOCKER_HUB_PASSWORD is not set"

    if common.has_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME):
        common.delete_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME)

    username = os.environ['DOCKER_HUB_USERNAME']
    password = os.environ['DOCKER_HUB_PASSWORD']
    config_json = common.create_docker_pull_config_json(username, password)
    common.create_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME, value=json.dumps(config_json))
    assert common.has_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME)


def cleanup():
    if is_mom_ee_deployed():
        remove_mom_ee()
    if common.has_service_account(MOM_EE_SERVICE_ACCOUNT):
        common.delete_service_account(MOM_EE_SERVICE_ACCOUNT)
    if common.has_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME):
        common.delete_secret(MOM_EE_SERVICE_ACCOUNT_SECRET_NAME)
    if common.has_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME):
        common.delete_secret(MOM_EE_DOCKER_CONFIG_SECRET_NAME)


def setup_function(function):
    if is_mom_ee_deployed():
        remove_mom_ee()


# An additional cleanup in case one/more tests failed and left mom-ee running
def teardown_module(module):
    cleanup()
