# Copyright 2015-2016 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import datetime
from collections import defaultdict
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Set

import mock
import pytest
from mock import ANY
from mock import MagicMock
from mock import Mock
from mock import patch

import paasta_tools.paastaapi.models as paastamodels
from paasta_tools import marathon_tools
from paasta_tools import utils
from paasta_tools.cli.cmds import status
from paasta_tools.cli.cmds.status import apply_args_filters
from paasta_tools.cli.cmds.status import build_smartstack_backends_table
from paasta_tools.cli.cmds.status import create_autoscaling_info_table
from paasta_tools.cli.cmds.status import create_mesos_non_running_tasks_table
from paasta_tools.cli.cmds.status import create_mesos_running_tasks_table
from paasta_tools.cli.cmds.status import desired_state_human
from paasta_tools.cli.cmds.status import format_kubernetes_pod_table
from paasta_tools.cli.cmds.status import format_kubernetes_replicaset_table
from paasta_tools.cli.cmds.status import format_marathon_task_table
from paasta_tools.cli.cmds.status import get_instance_state
from paasta_tools.cli.cmds.status import get_smartstack_status_human
from paasta_tools.cli.cmds.status import get_versions_table
from paasta_tools.cli.cmds.status import haproxy_backend_report
from paasta_tools.cli.cmds.status import marathon_app_status_human
from paasta_tools.cli.cmds.status import marathon_mesos_status_human
from paasta_tools.cli.cmds.status import marathon_mesos_status_summary
from paasta_tools.cli.cmds.status import missing_deployments_message
from paasta_tools.cli.cmds.status import paasta_status
from paasta_tools.cli.cmds.status import paasta_status_on_api_endpoint
from paasta_tools.cli.cmds.status import print_kafka_status
from paasta_tools.cli.cmds.status import print_kubernetes_status
from paasta_tools.cli.cmds.status import print_kubernetes_status_v2
from paasta_tools.cli.cmds.status import print_marathon_status
from paasta_tools.cli.cmds.status import report_invalid_whitelist_values
from paasta_tools.cli.utils import NoSuchService
from paasta_tools.cli.utils import PaastaColors
from paasta_tools.paastaapi import ApiException
from paasta_tools.utils import remove_ansi_escape_sequences
from tests.conftest import Struct


def make_fake_instance_conf(
    cluster, service, instance, deploy_group=None, team=None, registrations=()
):
    conf = MagicMock()
    conf.get_cluster.return_value = cluster
    conf.get_service.return_value = service
    conf.get_instance.return_value = instance
    conf.get_deploy_group.return_value = deploy_group
    conf.get_team.return_value = team
    conf.get_registrations.return_value = registrations if registrations else []
    if registrations is None:
        del (
            conf.get_registrations
        )  # http://www.voidspace.org.uk/python/mock/mock.html#deleting-attributes
    return conf


@patch("paasta_tools.cli.utils.validate_service_name", autospec=True)
def test_figure_out_service_name_not_found(mock_validate_service_name, capfd):
    # paasta_status with invalid -s service_name arg results in error
    mock_validate_service_name.side_effect = NoSuchService(None)
    parsed_args = Mock()
    parsed_args.service = "fake_service"

    expected_output = "%s\n" % NoSuchService.GUESS_ERROR_MSG

    # Fail if exit(1) does not get called
    with pytest.raises(SystemExit) as sys_exit:
        status.figure_out_service_name(parsed_args)

    output, _ = capfd.readouterr()
    assert sys_exit.value.code == 1
    assert output == expected_output


@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
@patch("paasta_tools.cli.cmds.status.load_system_paasta_config", autospec=True)
@patch("paasta_tools.cli.utils.validate_service_name", autospec=True)
@patch("paasta_tools.cli.utils.guess_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
def test_status_arg_service_not_found(
    mock_list_services,
    mock_guess_service_name,
    mock_validate_service_name,
    mock_load_system_paasta_config,
    mock_list_clusters,
    capfd,
    system_paasta_config,
):
    # paasta_status with no args and non-service directory results in error
    mock_list_services.return_value = []
    mock_guess_service_name.return_value = "not_a_service"
    error = NoSuchService("fake_service")
    mock_validate_service_name.side_effect = error
    mock_list_clusters.return_value = ["cluster1"]
    mock_load_system_paasta_config.return_value = system_paasta_config
    expected_output = str(error) + "\n"

    args = MagicMock()
    args.service = None
    args.owner = None
    args.clusters = None
    args.instances = None
    args.deploy_group = None
    args.registration = None
    args.service_instance = None

    # Fail if exit(1) does not get called
    with pytest.raises(SystemExit) as sys_exit:
        paasta_status(args)

    output, _ = capfd.readouterr()
    assert sys_exit.value.code == 1
    assert output == expected_output


@patch("paasta_tools.cli.cmds.status.paasta_status_on_api_endpoint", autospec=True)
@patch("paasta_tools.cli.cmds.status.report_invalid_whitelist_values", autospec=True)
def test_report_status_calls_report_invalid_whitelist_values(
    mock_report_invalid_whitelist_values,
    mock_paasta_status_on_api_endpoint,
    system_paasta_config,
):
    service = "fake_service"
    planned_deployments = ["cluster.instance1", "cluster.instance2"]
    actual_deployments: Dict[str, str] = {}
    instance_whitelist: Dict[str, Any] = {}

    status.report_status_for_cluster(
        service=service,
        cluster="cluster",
        deploy_pipeline=planned_deployments,
        actual_deployments=actual_deployments,
        instance_whitelist=instance_whitelist,
        system_paasta_config=system_paasta_config,
    )
    mock_report_invalid_whitelist_values.assert_called_once_with(
        [], ["instance1", "instance2"], "instance"
    )


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.load_system_paasta_config", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_deploy_info", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_actual_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
def test_status_pending_pipeline_build_message(
    mock_list_clusters,
    mock_validate_service_name,
    mock_get_actual_deployments,
    mock_get_deploy_info,
    mock_figure_out_service_name,
    mock_load_system_paasta_config,
    mock_list_services,
    mock_get_instance_configs_for_service,
    capfd,
    system_paasta_config,
):
    # If deployments.json is missing SERVICE, output the appropriate message
    service = "fake_service"
    mock_list_clusters.return_value = ["cluster"]
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = service
    mock_list_services.return_value = [service]
    pipeline = [{"instancename": "cluster.instance"}]
    mock_get_deploy_info.return_value = {"pipeline": pipeline}
    mock_load_system_paasta_config.return_value = system_paasta_config
    mock_instance_config = make_fake_instance_conf("cluster", service, "instancename")
    mock_get_instance_configs_for_service.return_value = [mock_instance_config]

    actual_deployments: Dict[str, str] = {}
    mock_get_actual_deployments.return_value = actual_deployments
    expected_output = missing_deployments_message(service)

    args = MagicMock()
    args.service = service
    args.deploy_group = None
    args.clusters = None
    args.instances = None
    args.owner = None
    args.soa_dir = utils.DEFAULT_SOA_DIR
    args.registration = None
    args.service_instance = None

    paasta_status(args)
    output, _ = capfd.readouterr()
    assert expected_output in output


@patch("paasta_tools.cli.cmds.status.load_deployments_json", autospec=True)
def test_get_actual_deployments(mock_get_deployments,):
    mock_get_deployments.return_value = utils.DeploymentsJsonV1(
        {
            "fake_service:paasta-b_cluster.b_instance": {
                "docker_image": "this_is_a_sha"
            },
            "fake_service:paasta-a_cluster.a_instance": {
                "docker_image": "this_is_a_sha"
            },
        }
    )
    expected = {
        "a_cluster.a_instance": "this_is_a_sha",
        "b_cluster.b_instance": "this_is_a_sha",
    }

    actual = status.get_actual_deployments("fake_service", "/fake/soa/dir")
    assert expected == actual


@patch("paasta_tools.cli.cmds.status.read_deploy", autospec=True)
def test_get_deploy_info_exists(mock_read_deploy):
    expected = "fake deploy yaml"
    mock_read_deploy.return_value = expected
    actual = status.get_deploy_info("fake_service")
    assert expected == actual


@patch("paasta_tools.cli.cmds.status.read_deploy", autospec=True)
def test_get_deploy_info_does_not_exist(mock_read_deploy, capfd):
    mock_read_deploy.return_value = False
    with pytest.raises(SystemExit) as sys_exit:
        status.get_deploy_info("fake_service")
    output, _ = capfd.readouterr()
    assert sys_exit.value.code == 1
    assert output.startswith("Error encountered with")


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.load_system_paasta_config", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_actual_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_planned_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.report_status_for_cluster", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
def test_status_calls_sergeants(
    mock_list_clusters,
    mock_validate_service_name,
    mock_report_status,
    mock_get_planned_deployments,
    mock_get_actual_deployments,
    mock_figure_out_service_name,
    mock_load_system_paasta_config,
    mock_list_services,
    mock_get_instance_configs_for_service,
    system_paasta_config,
):
    service = "fake_service"
    cluster = "fake_cluster"
    mock_list_clusters.return_value = ["cluster1", "cluster2", "fake_cluster"]
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = service
    mock_list_services.return_value = [service]

    mock_instance_config = make_fake_instance_conf(cluster, service, "fi")
    mock_instance_config.get_service.return_value = service
    mock_instance_config.get_cluster.return_value = cluster
    mock_get_instance_configs_for_service.return_value = [mock_instance_config]

    planned_deployments = [
        "cluster1.instance1",
        "cluster1.instance2",
        "cluster2.instance1",
    ]
    mock_get_planned_deployments.return_value = planned_deployments

    actual_deployments = {"fake_service:paasta-cluster.instance": "this_is_a_sha"}
    mock_get_actual_deployments.return_value = actual_deployments
    mock_load_system_paasta_config.return_value = system_paasta_config
    mock_report_status.return_value = 1776, ["dummy", "output"]

    args = MagicMock()
    args.service = service
    args.clusters = None
    args.instances = None
    args.verbose = False
    args.owner = None
    args.deploy_group = None
    args.soa_dir = "/fake/soa/dir"
    args.registration = None
    args.service_instance = None
    args.new = False
    return_value = paasta_status(args)

    assert return_value == 1776

    mock_get_actual_deployments.assert_called_once_with(service, "/fake/soa/dir")
    mock_report_status.assert_called_once_with(
        service=service,
        deploy_pipeline=planned_deployments,
        actual_deployments=actual_deployments,
        cluster=cluster,
        instance_whitelist={"fi": mock_instance_config.__class__},
        system_paasta_config=system_paasta_config,
        verbose=False,
        new=False,
    )


def test_report_invalid_whitelist_values_no_whitelists():
    whitelist: Set[str] = set()
    items = ["cluster1", "cluster2", "cluster3"]
    item_type = "thingy"
    actual = report_invalid_whitelist_values(whitelist, items, item_type)
    assert actual == ""


def test_report_invalid_whitelist_values_with_whitelists():
    whitelist = {"bogus1", "cluster1"}
    items = ["cluster1", "cluster2", "cluster3"]
    item_type = "thingy"
    actual = report_invalid_whitelist_values(whitelist, items, item_type)
    assert "Warning" in actual
    assert item_type in actual
    assert "bogus1" in actual


class StatusArgs:
    def __init__(
        self,
        service,
        soa_dir,
        clusters,
        instances,
        deploy_group,
        owner,
        registration,
        verbose,
        service_instance=None,
        new=False,
    ):
        self.service = service
        self.soa_dir = soa_dir
        self.clusters = clusters
        self.instances = instances
        self.deploy_group = deploy_group
        self.owner = owner
        self.registration = registration
        self.verbose = verbose
        self.service_instance = service_instance
        self.new = new


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
def test_apply_args_filters_clusters_and_instances_clusters_instances_deploy_group(
    mock_list_clusters,
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group="fake_deploy_group",
        clusters="cluster1",
        instances="instance1,instance3",
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_list_clusters.return_value = ["cluster1", "cluster2"]
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_inst1 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance1", "fake_deploy_group"
    )
    mock_inst2 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance2", "fake_deploy_group"
    )
    mock_inst3 = make_fake_instance_conf(
        "cluster2", "fake_service", "instance3", "fake_deploy_group"
    )
    mock_get_instance_configs_for_service.return_value = [
        mock_inst1,
        mock_inst2,
        mock_inst3,
    ]

    pargs = apply_args_filters(args)
    assert sorted(pargs.keys()) == ["cluster1"]
    assert pargs["cluster1"]["fake_service"] == {"instance1": mock_inst1.__class__}


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
def test_apply_args_filters_clusters_uses_deploy_group_when_no_clusters_and_instances(
    mock_list_clusters,
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group="fake_deploy_group",
        clusters=None,
        instances=None,
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_list_clusters.return_value = ["cluster1", "cluster2"]
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_inst1 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance1", "fake_deploy_group"
    )
    mock_inst2 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance2", "fake_deploy_group"
    )
    mock_inst3 = make_fake_instance_conf(
        "cluster2", "fake_service", "instance3", "fake_deploy_group"
    )
    mock_get_instance_configs_for_service.return_value = [
        mock_inst1,
        mock_inst2,
        mock_inst3,
    ]

    pargs = apply_args_filters(args)
    assert sorted(pargs.keys()) == ["cluster1", "cluster2"]
    assert pargs["cluster1"]["fake_service"] == {
        "instance1": mock_inst1.__class__,
        "instance2": mock_inst2.__class__,
    }
    assert pargs["cluster2"]["fake_service"] == {"instance3": mock_inst3.__class__}


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
def test_apply_args_filters_clusters_return_none_when_cluster_not_in_deploy_group(
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group="fake_deploy_group",
        clusters="cluster4",
        instances=None,
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_get_instance_configs_for_service.return_value = [
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance1", "fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance2", "fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster2", "fake_service", "instance3", "fake_deploy_group"
        ),
    ]

    assert len(apply_args_filters(args)) == 0


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
@patch("paasta_tools.cli.utils.list_all_instances_for_service", autospec=True)
def test_apply_args_filters_clusters_return_none_when_instance_not_in_deploy_group(
    mock_list_clusters,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
    mock_list_all_instances_for_service,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group="fake_deploy_group",
        clusters=None,
        instances="instance5",
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_list_clusters.return_value = ["cluster1", "cluster2"]
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_list_all_instances_for_service.return_value = []
    mock_get_instance_configs_for_service.return_value = [
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance1", "other_fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance2", "other_fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster2", "fake_service", "instance3", "other_fake_deploy_group"
        ),
    ]

    assert len(apply_args_filters(args)) == 0


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
def test_apply_args_filters_clusters_and_instances(
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group=None,
        clusters="cluster1",
        instances="instance1,instance3",
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_inst1 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance1", "fake_deploy_group"
    )
    mock_inst2 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance2", "fake_deploy_group"
    )
    mock_inst3 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance3", "fake_deploy_group"
    )
    mock_get_instance_configs_for_service.return_value = [
        mock_inst1,
        mock_inst2,
        mock_inst3,
    ]

    pargs = apply_args_filters(args)
    assert sorted(pargs.keys()) == ["cluster1"]
    assert pargs["cluster1"]["fake_service"] == {
        "instance1": mock_inst1.__class__,
        "instance3": mock_inst3.__class__,
    }


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
@pytest.mark.parametrize(
    "service_instance_name",
    [
        "fake_service.instance1",
        "fake_service.instance1,instance2",
        "fake_service.instance3",
    ],
)
def test_apply_args_filters_shorthand_notation(
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
    service_instance_name,
):
    args = StatusArgs(
        service=None,
        soa_dir="/fake/soa/dir",
        deploy_group=None,
        clusters="cluster1",
        instances=None,
        owner=None,
        registration=None,
        verbose=False,
        service_instance=service_instance_name,
    )
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_inst1 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance1", "fake_deploy_group"
    )
    mock_inst2 = make_fake_instance_conf(
        "cluster1", "fake_service", "instance2", "fake_deploy_group"
    )
    mock_get_instance_configs_for_service.return_value = [
        mock_inst1,
        mock_inst2,
    ]

    pargs = apply_args_filters(args)
    if service_instance_name == "fake_service.instance1":
        assert sorted(pargs.keys()) == ["cluster1"]
        assert pargs["cluster1"]["fake_service"] == {"instance1": mock_inst1.__class__}
    elif service_instance_name == "fake_service.instance1,instance2":
        assert sorted(pargs.keys()) == ["cluster1"]
        assert pargs["cluster1"]["fake_service"] == {
            "instance1": mock_inst1.__class__,
            "instance2": mock_inst2.__class__,
        }
    elif service_instance_name == "fake_service.instance3":
        assert sorted(pargs.keys()) == []
        assert pargs["cluster1"]["fake_service"] == {}


@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
def test_apply_args_filters_bad_service_name(mock_list_services, capfd):
    args = StatusArgs(
        service="fake-service",
        soa_dir="/fake/soa/dir",
        deploy_group=None,
        clusters="cluster1",
        instances="instance4,instance5",
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_list_services.return_value = ["fake_service"]
    pargs = apply_args_filters(args)
    output, _ = capfd.readouterr()
    assert len(pargs) == 0
    assert 'The service "fake-service" does not exist.' in output
    assert "Did you mean any of these?" in output
    assert "  fake_service" in output


@patch("paasta_tools.cli.utils.list_all_instances_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
def test_apply_args_filters_no_instances_found(
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
    mock_list_all_instances_for_service,
    capfd,
):
    args = StatusArgs(
        service="fake_service",
        soa_dir="/fake/soa/dir",
        deploy_group=None,
        clusters="cluster1",
        instances="instance4,instance5",
        owner=None,
        registration=None,
        verbose=False,
        service_instance=None,
    )
    mock_validate_service_name.return_value = None
    mock_figure_out_service_name.return_value = "fake_service"
    mock_list_services.return_value = ["fake_service"]
    mock_get_instance_configs_for_service.return_value = [
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance1", "fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance2", "fake_deploy_group"
        ),
        make_fake_instance_conf(
            "cluster1", "fake_service", "instance3", "fake_deploy_group"
        ),
    ]
    mock_list_all_instances_for_service.return_value = [
        "instance1",
        "instance2",
        "instance3",
    ]
    pargs = apply_args_filters(args)
    output, _ = capfd.readouterr()
    assert len(pargs.keys()) == 0
    assert (
        "fake_service doesn't have any instances matching instance4, instance5 on cluster1."
        in output
    )

    assert "Did you mean any of these?" in output
    for i in ["instance1", "instance2", "instance3"]:
        assert i in output


@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_actual_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.load_system_paasta_config", autospec=True)
@patch("paasta_tools.cli.cmds.status.report_status_for_cluster", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_planned_deployments", autospec=True)
def test_status_with_owner(
    mock_get_planned_deployments,
    mock_list_clusters,
    mock_report_status,
    mock_load_system_paasta_config,
    mock_get_actual_deployments,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
    system_paasta_config,
):

    mock_load_system_paasta_config.return_value = system_paasta_config
    mock_list_services.return_value = ["fakeservice", "otherservice"]
    cluster = "fake_cluster"
    mock_list_clusters.return_value = [cluster]
    mock_inst_1 = make_fake_instance_conf(
        cluster, "fakeservice", "instance1", team="faketeam"
    )
    mock_inst_2 = make_fake_instance_conf(
        cluster, "otherservice", "instance3", team="faketeam"
    )
    mock_get_instance_configs_for_service.return_value = [mock_inst_1, mock_inst_2]
    mock_get_planned_deployments.return_value = [
        "fakeservice.instance1",
        "otherservice.instance3",
    ]

    mock_get_actual_deployments.return_value = {
        "fakeservice.instance1": "sha1",
        "fakeservice.instance2": "sha2",
        "otherservice.instance3": "sha3",
        "otherservice.instance1": "sha4",
    }
    mock_report_status.return_value = 0, ["dummy", "output"]

    args = MagicMock()
    args.service = None
    args.instances = None
    args.clusters = None
    args.deploy_group = None
    args.owner = "faketeam"
    args.soa_dir = "/fake/soa/dir"
    args.registration = None
    args.service_instance = None
    return_value = paasta_status(args)

    assert return_value == 0
    assert mock_report_status.call_count == 2


@patch("paasta_tools.cli.cmds.status.list_clusters", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_instance_configs_for_service", autospec=True)
@patch("paasta_tools.cli.cmds.status.list_services", autospec=True)
@patch("paasta_tools.cli.cmds.status.figure_out_service_name", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_actual_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.load_system_paasta_config", autospec=True)
@patch("paasta_tools.cli.cmds.status.report_status_for_cluster", autospec=True)
@patch("paasta_tools.cli.cmds.status.get_planned_deployments", autospec=True)
@patch("paasta_tools.cli.cmds.status.validate_service_name", autospec=True)
def test_status_with_registration(
    mock_validate_service_name,
    mock_get_planned_deployments,
    mock_report_status,
    mock_load_system_paasta_config,
    mock_get_actual_deployments,
    mock_figure_out_service_name,
    mock_list_services,
    mock_get_instance_configs_for_service,
    mock_list_clusters,
    system_paasta_config,
):
    mock_validate_service_name.return_value = None
    mock_load_system_paasta_config.return_value = system_paasta_config
    mock_list_services.return_value = ["fakeservice", "otherservice"]
    cluster = "fake_cluster"
    mock_list_clusters.return_value = [cluster]
    mock_get_planned_deployments.return_value = [
        "fakeservice.main",
        "fakeservice.not_main",
    ]
    mock_inst_1 = make_fake_instance_conf(
        cluster, "fakeservice", "instance1", registrations=["fakeservice.main"]
    )
    mock_inst_2 = make_fake_instance_conf(
        cluster, "fakeservice", "instance2", registrations=["fakeservice.not_main"]
    )
    mock_inst_3 = make_fake_instance_conf(
        cluster, "fakeservice", "instance3", registrations=["fakeservice.also_not_main"]
    )
    mock_inst_4 = make_fake_instance_conf(
        cluster, "fakeservice", "instance4", registrations=None
    )
    mock_get_instance_configs_for_service.return_value = [
        mock_inst_1,
        mock_inst_2,
        mock_inst_3,
        mock_inst_4,
    ]

    mock_get_actual_deployments.return_value = {
        "fakeservice.instance1": "sha1",
        "fakeservice.instance2": "sha2",
        "fakeservice.instance3": "sha3",
    }
    mock_report_status.return_value = 0, ["dummy", "output"]

    args = StatusArgs(
        service="fakeservice",
        instances=None,
        clusters=None,
        deploy_group=None,
        owner=None,
        registration="main,not_main",
        soa_dir="/fake/soa/dir",
        verbose=False,
        service_instance=None,
        new=False,
    )
    return_value = paasta_status(args)

    assert return_value == 0
    assert mock_report_status.call_count == 1
    mock_report_status.assert_called_once_with(
        service="fakeservice",
        cluster=cluster,
        deploy_pipeline=ANY,
        actual_deployments=ANY,
        instance_whitelist={
            "instance1": mock_inst_1.__class__,
            "instance2": mock_inst_2.__class__,
        },
        system_paasta_config=system_paasta_config,
        verbose=args.verbose,
        new=False,
    )


@pytest.fixture
def mock_marathon_status(include_envoy=True, include_smartstack=True):
    kwargs = dict(
        desired_state="start",
        desired_app_id="abc.def",
        app_id="fake_app_id",
        app_count=1,
        running_instance_count=2,
        expected_instance_count=2,
        deploy_status="Running",
        bounce_method="crossover",
        app_statuses=[],
        mesos=paastamodels.MarathonMesosStatus(
            running_task_count=2, running_tasks=[], non_running_tasks=[],
        ),
    )
    if include_smartstack:
        kwargs["smartstack"] = paastamodels.SmartstackStatus(
            registration="fake_service.fake_instance",
            expected_backends_per_location=1,
            locations=[],
        )
    if include_envoy:
        kwargs["envoy"] = paastamodels.EnvoyStatus(
            registration="fake_service.fake_instance",
            expected_backends_per_location=1,
            locations=[],
        )
    return paastamodels.InstanceStatusMarathon(**kwargs)


@pytest.fixture
def mock_kubernetes_status():
    return paastamodels.InstanceStatusKubernetes(
        desired_state="start",
        app_id="fake_app_id",
        app_count=1,
        running_instance_count=2,
        expected_instance_count=2,
        deploy_status="Running",
        deploy_status_message="some reason",
        bounce_method="crossover",
        create_timestamp=1562963508.0,
        namespace="paasta",
        pods=[],
        replicasets=[],
        smartstack=paastamodels.SmartstackStatus(
            registration="fake_service.fake_instance",
            expected_backends_per_location=1,
            locations=[],
        ),
        envoy=paastamodels.EnvoyStatus(
            registration="fake_service.fake_instance",
            expected_backends_per_location=1,
            locations=[],
        ),
        evicted_count=1,
    )


@pytest.fixture
def mock_kafka_status() -> Mapping[str, Any]:
    return defaultdict(
        metadata=dict(
            name="kafka--k8s-local-main",
            namespace="paasta-kafkaclusters",
            annotations={"paasta.yelp.com/desired_state": "testing"},
        ),
        status=dict(
            brokers=[
                {
                    "host": "10.93.122.47",
                    "ip": "0.0.0.0",
                    "id": 0,
                    "name": "kafka-0",
                    "phase": "Running",
                    "deployed_timestamp": "2020-03-25T16:24:21Z",
                    "container_state": "Running",
                    "container_state_reason": "",
                },
                {
                    "host": "10.93.115.200",
                    "ip": "0.0.0.1",
                    "id": 1,
                    "name": "kafka-1",
                    "phase": "Pending",
                    "deployed_timestamp": "2020-03-25T16:24:21Z",
                    "container_state": "Waiting",
                    "container_state_reason": "",
                },
            ],
            cluster_ready=True,
            health={
                "healthy": False,
                "restarting": False,
                "message": "message",
                "offline_partitions": 1,
                "under_replicated_partitions": 1,
            },
            kafka_view_url="https://kafkaview.com",
            zookeeper="0.0.0.0:2181/kafka",
        ),
    )


@mock.patch("paasta_tools.cli.cmds.status.get_paasta_oapi_client", autospec=True)
def test_paasta_status_on_api_endpoint_marathon(
    mock_get_paasta_oapi_client, system_paasta_config, mock_marathon_status
):
    fake_status_obj = paastamodels.InstanceStatus(
        git_sha="fake_git_sha",
        instance="fake_instance",
        service="fake_service",
        marathon=mock_marathon_status,
    )

    mock_api = mock_get_paasta_oapi_client.return_value
    mock_api.service.status_instance.return_value = fake_status_obj

    output = []
    paasta_status_on_api_endpoint(
        cluster="fake_cluster",
        service="fake_service",
        instance="fake_instance",
        output=output,
        system_paasta_config=system_paasta_config,
        verbose=0,
    )


def test_paasta_status_exception(system_paasta_config):
    with patch(
        "paasta_tools.cli.cmds.status.get_paasta_oapi_client", autospec=True
    ) as mock_get_paasta_oapi_client:
        mock_swagger_client = Mock()
        mock_swagger_client.api_error = ApiException
        mock_swagger_client.service.status_instance.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        mock_get_paasta_oapi_client.return_value = mock_swagger_client
        paasta_status_on_api_endpoint(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=[],
            system_paasta_config=system_paasta_config,
            verbose=False,
        )


def test_format_kubernetes_replicaset_table_in_non_verbose(mock_kubernetes_status):
    with mock.patch(
        "paasta_tools.cli.cmds.status.format_kubernetes_replicaset_table", autospec=True
    ) as mock_format_kubernetes_replicaset_table, mock.patch(
        "paasta_tools.cli.cmds.status.bouncing_status_human", autospec=True
    ):
        mock_kubernetes_status.replicasets = [
            paastamodels.KubernetesReplicaSet(
                name="replicaset_1",
                replicas=3,
                ready_replicas=2,
                create_timestamp=1562963508.0,
                git_sha="fake_git_sha",
                config_sha="fake_config_sha",
            )
        ]
        mock_kubernetes_status.error_message = ""
        status.print_kubernetes_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=[],
            kubernetes_status=mock_kubernetes_status,
            verbose=0,
        )

        assert mock_format_kubernetes_replicaset_table.called


class TestPrintMarathonStatus:
    def test_error(self, mock_marathon_status):
        mock_marathon_status.error_message = "Things went wrong"
        output = []
        return_value = print_marathon_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            marathon_status=mock_marathon_status,
        )

        assert return_value == 1
        assert output == ["Things went wrong"]

    def test_successful_return_value(self, mock_marathon_status):
        return_value = print_marathon_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=[],
            marathon_status=mock_marathon_status,
        )
        assert return_value == 0

    @pytest.mark.parametrize("include_envoy", [True, False])
    @pytest.mark.parametrize("include_smartstack", [True, False])
    @pytest.mark.parametrize("include_autoscaling_info", [True, False])
    @patch("paasta_tools.cli.cmds.status.create_autoscaling_info_table", autospec=True)
    @patch("paasta_tools.cli.cmds.status.get_smartstack_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.get_envoy_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.marathon_mesos_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.marathon_app_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.status_marathon_job_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.desired_state_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.bouncing_status_human", autospec=True)
    def test_output(
        self,
        mock_bouncing_status,
        mock_desired_state,
        mock_status_marathon_job_human,
        mock_marathon_app_status_human,
        mock_marathon_mesos_status_human,
        mock_get_envoy_status_human,
        mock_get_smartstack_status_human,
        mock_create_autoscaling_info_table,
        include_autoscaling_info,
        include_smartstack,
        include_envoy,
    ):
        mock_marathon_app_status_human.side_effect = lambda desired_app_id, app_status: [
            f"{app_status.deploy_status} status 1",
            f"{app_status.deploy_status} status 2",
        ]
        mock_marathon_mesos_status_human.return_value = [
            "mesos status 1",
            "mesos status 2",
        ]
        mock_get_envoy_status_human.return_value = [
            "envoy status 1",
            "envoy status 2",
        ]
        mock_get_smartstack_status_human.return_value = [
            "smartstack status 1",
            "smartstack status 2",
        ]
        mock_create_autoscaling_info_table.return_value = [
            "autoscaling info 1",
            "autoscaling info 2",
        ]

        mms = mock_marathon_status(
            include_smartstack=include_smartstack, include_envoy=include_envoy
        )
        mms.app_statuses = [
            paastamodels.MarathonAppStatus(deploy_status="app_1"),
            paastamodels.MarathonAppStatus(deploy_status="app_2"),
        ]
        if include_autoscaling_info:
            mms.autoscaling_info = paastamodels.MarathonAutoscalingInfo()

        output = []
        print_marathon_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            marathon_status=mms,
        )

        expected_output = [
            f"    Desired state:      {mock_bouncing_status.return_value} and {mock_desired_state.return_value}",
            f"    {mock_status_marathon_job_human.return_value}",
        ]
        if include_autoscaling_info:
            expected_output += ["      autoscaling info 1", "      autoscaling info 2"]
        expected_output += [
            f"      app_1 status 1",
            f"      app_1 status 2",
            f"      app_2 status 1",
            f"      app_2 status 2",
            f"    mesos status 1",
            f"    mesos status 2",
        ]
        if include_smartstack:
            expected_output += [f"    smartstack status 1", f"    smartstack status 2"]
        if include_envoy:
            expected_output += [f"    envoy status 1", f"    envoy status 2"]

        assert expected_output == output


@pytest.fixture
def mock_kubernetes_status_v2():
    return paastamodels.InstanceStatusKubernetesV2(
        app_name="service--instance",
        desired_state="start",
        desired_instances=1,
        error_message="",
        versions=[
            paastamodels.KubernetesVersion(
                create_timestamp=float(datetime.datetime(2021, 3, 5).timestamp()),
                git_sha="aaa000",
                config_sha="config000",
                name="service--instance--000",
                replicas=1,
                ready_replicas=1,
                pods=[
                    paastamodels.KubernetesPodV2(
                        name="service--instance--000-0000",
                        ip="1.2.3.4",
                        create_timestamp=float(
                            datetime.datetime(2021, 3, 6).timestamp()
                        ),
                    ),
                ],
            ),
        ],
    )


class TestPrintKubernetesStatusV2:
    def test_error(self, mock_kubernetes_status_v2):
        mock_kubernetes_status_v2.error_message = "Something bad happened!"
        output = []
        return_code = print_kubernetes_status_v2(
            cluster="cluster",
            service="service",
            instance="instance",
            output=output,
            status=mock_kubernetes_status_v2,
            verbose=0,
        )
        assert return_code == 1
        assert "Something bad happened!" in output[-1]

    def test_successful_return_value(self, mock_kubernetes_status_v2):
        return_code = print_kubernetes_status_v2(
            cluster="cluster",
            service="service",
            instance="instance",
            output=[],
            status=mock_kubernetes_status_v2,
            verbose=0,
        )
        assert return_code == 0

    @mock.patch(
        "paasta_tools.cli.cmds.status.get_instance_state", autospec=True,
    )
    @mock.patch(
        "paasta_tools.cli.cmds.status.get_versions_table", autospec=True,
    )
    def test_output(
        self,
        mock_get_versions_table,
        mock_get_instance_state,
        mock_kubernetes_status_v2,
    ):
        output = []
        mock_versions_table = ["table_entry_1", "table_entry_2"]
        mock_get_versions_table.return_value = mock_versions_table
        print_kubernetes_status_v2(
            cluster="cluster",
            service="service",
            instance="instance",
            output=output,
            status=mock_kubernetes_status_v2,
            verbose=0,
        )
        joined_output = "\n".join(output)
        assert f"State: {mock_get_instance_state.return_value}" in joined_output
        for table_entry in mock_versions_table:
            assert table_entry in joined_output


class TestGetInstanceState:
    def test_stop(self, mock_kubernetes_status_v2):
        mock_kubernetes_status_v2.desired_state = "stop"
        assert "Stop" in get_instance_state(mock_kubernetes_status_v2)

    def test_running(self, mock_kubernetes_status_v2):
        mock_kubernetes_status_v2.desired_state = "start"
        instance_state = get_instance_state(mock_kubernetes_status_v2)
        assert remove_ansi_escape_sequences(instance_state) == "Running"

    def test_bouncing(self, mock_kubernetes_status_v2):
        new_version = paastamodels.KubernetesVersion(
            create_timestamp=1.0,
            git_sha="bbb111",
            config_sha="config111",
            ready_replicas=0,
        )
        mock_kubernetes_status_v2.versions.append(new_version)

        instance_state = get_instance_state(mock_kubernetes_status_v2)
        instance_state = remove_ansi_escape_sequences(instance_state)
        assert instance_state == "Bouncing to bbb111, config111"

    def test_bouncing_git_sha_change_only(self, mock_kubernetes_status_v2):
        new_version = paastamodels.KubernetesVersion(
            create_timestamp=1.0,
            git_sha="bbb111",
            config_sha=mock_kubernetes_status_v2.versions[0].config_sha,
            ready_replicas=0,
        )
        mock_kubernetes_status_v2.versions.append(new_version)

        instance_state = get_instance_state(mock_kubernetes_status_v2)
        instance_state = remove_ansi_escape_sequences(instance_state)
        assert instance_state == "Bouncing to bbb111"


class TestGetVersionsTable:
    # TODO: Add replica table coverage

    @pytest.fixture
    def mock_replicasets(self):
        replicaset_1 = paastamodels.KubernetesVersion(
            git_sha="aabbccddee",
            config_sha="config000",
            create_timestamp=float(datetime.datetime(2021, 3, 3).timestamp()),
            pods=[
                paastamodels.KubernetesPodV2(
                    name="pod1",
                    ip="1.2.3.4",
                    host="w.x.y.z",
                    create_timestamp=float(datetime.datetime(2021, 3, 5).timestamp()),
                    phase="Running",
                    ready=True,
                    scheduled=True,
                    containers=[],
                ),
                paastamodels.KubernetesPodV2(
                    name="pod2",
                    ip="1.2.3.5",
                    host="a.b.c.d",
                    create_timestamp=float(datetime.datetime(2021, 3, 3).timestamp()),
                    phase="Failed",
                    reason="Evicted",
                    message="Not enough memory!",
                    ready=True,
                    scheduled=True,
                    containers=[],
                ),
            ],
        )
        replicaset_2 = paastamodels.KubernetesVersion(
            git_sha="ff11223344",
            config_sha="config000",
            create_timestamp=float(datetime.datetime(2021, 3, 1).timestamp()),
            pods=[
                paastamodels.KubernetesPodV2(
                    name="pod1",
                    ip="1.2.3.6",
                    host="a.b.c.d",
                    create_timestamp=float(datetime.datetime(2021, 3, 1).timestamp()),
                    phase="Running",
                    ready=True,
                    scheduled=True,
                    containers=[],
                ),
            ],
        )
        # in reverse order to ensure we are sorting
        return [replicaset_2, replicaset_1]

    def test_two_replicasets(self, mock_replicasets):
        versions_table = get_versions_table(mock_replicasets, verbose=0)

        assert "aabbccdd (new)" in versions_table[0]
        assert "2021-03-03" in versions_table[0]
        assert PaastaColors.green("1 Healthy") in versions_table[1]
        assert PaastaColors.red("1 Unhealthy") in versions_table[1]

        assert "ff112233 (old)" in versions_table[7]
        assert "2021-03-01" in versions_table[7]
        assert PaastaColors.green("1 Healthy") in versions_table[8]
        assert "Unhealhty" not in versions_table[8]

    def test_different_config_shas(self, mock_replicasets):
        mock_replicasets[0].config_sha = "config111"
        versions_table = get_versions_table(mock_replicasets, verbose=0)
        assert "aabbccdd, config000" in versions_table[0]
        assert "ff112233, config111" in versions_table[7]

    def test_full_replica_table(self, mock_replicasets):
        versions_table = get_versions_table(mock_replicasets, verbose=2)
        versions_table_tip = remove_ansi_escape_sequences(versions_table[4])
        assert "1.2.3.5" in versions_table[3]
        assert "Evicted: Not enough memory!" in versions_table_tip
        assert "1.2.3.6" in versions_table[10]


class TestPrintKubernetesStatus:
    def test_error(self, mock_kubernetes_status):
        mock_kubernetes_status.error_message = "Things went wrong"
        output = []
        return_value = print_kubernetes_status(
            cluster="fake_Cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            kubernetes_status=mock_kubernetes_status,
        )

        assert return_value == 1
        assert PaastaColors.red("Things went wrong") in output[-1]

    def test_successful_return_value(self, mock_kubernetes_status):
        return_value = print_kubernetes_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=[],
            kubernetes_status=mock_kubernetes_status,
        )
        assert return_value == 0

    @patch(
        "paasta_tools.cli.cmds.status.format_tail_lines_for_mesos_task", autospec=True
    )
    @patch("paasta_tools.cli.cmds.status.get_smartstack_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.get_envoy_status_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
    @patch(
        "paasta_tools.cli.cmds.status.kubernetes_app_deploy_status_human", autospec=True
    )
    @patch("paasta_tools.cli.cmds.status.desired_state_human", autospec=True)
    @patch("paasta_tools.cli.cmds.status.bouncing_status_human", autospec=True)
    def test_output(
        self,
        mock_bouncing_status,
        mock_desired_state,
        mock_kubernetes_app_deploy_status_human,
        mock_naturaltime,
        mock_get_envoy_status_human,
        mock_get_smartstack_status_human,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_status,
    ):
        mock_bouncing_status.return_value = "Bouncing (crossover)"
        mock_desired_state.return_value = "Started"
        mock_kubernetes_app_deploy_status_human.return_value = "Running"
        mock_naturaltime.return_value = "a month ago"
        mock_kubernetes_status.pods = [
            paastamodels.KubernetesPod(
                name="app_1",
                host="fake_host1",
                deployed_timestamp=1562963508.0,
                phase="Running",
                ready=True,
                containers=[],
                message=None,
            ),
            paastamodels.KubernetesPod(
                name="app_2",
                host="fake_host2",
                deployed_timestamp=1562963510.0,
                phase="Running",
                ready=True,
                containers=[],
                message=None,
            ),
            paastamodels.KubernetesPod(
                name="app_3",
                host="fake_host3",
                deployed_timestamp=1562963511.0,
                phase="Failed",
                ready=False,
                containers=[],
                message="Disk quota exceeded",
                reason="Evicted",
            ),
        ]
        mock_kubernetes_status.replicasets = [
            paastamodels.KubernetesReplicaSet(
                name="replicaset_1",
                replicas=3,
                ready_replicas=2,
                create_timestamp=1562963508.0,
                git_sha=None,
                config_sha=None,
            )
        ]

        output = []
        print_kubernetes_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            kubernetes_status=mock_kubernetes_status,
        )

        expected_output = [
            f"    State:      {mock_bouncing_status.return_value} - Desired state: {mock_desired_state.return_value}",
            f"    Kubernetes:   {PaastaColors.green('Healthy')} - up with {PaastaColors.green('(2/2)')} instances ({PaastaColors.red('1')} evicted). Status: {mock_kubernetes_app_deploy_status_human.return_value}",
        ]
        expected_output += [
            f"      Pods:",
            f"        Pod ID  Host deployed to  Deployed at what localtime      Health",
            f"        app_1   fake_host1        2019-07-12T20:31 ({mock_naturaltime.return_value})  {PaastaColors.green('Healthy')}",
            f"        app_2   fake_host2        2019-07-12T20:31 ({mock_naturaltime.return_value})  {PaastaColors.green('Healthy')}",
            f"        app_3   fake_host3        2019-07-12T20:31 ({mock_naturaltime.return_value})  {PaastaColors.red('Evicted')}",
            f"        {PaastaColors.grey('  Disk quota exceeded')}",
            f"      ReplicaSets:",
            f"        ReplicaSet Name  Ready / Desired  Created at what localtime       Service git SHA  Config hash",
            f"        replicaset_1     {PaastaColors.red('2/3')}              2019-07-12T20:31 ({mock_naturaltime.return_value})  Unknown          Unknown",
        ]

        assert expected_output == output


class TestPrintKafkaStatus:
    def test_error(self, mock_kafka_status):
        mock_kafka_status["status"] = None
        output = []
        return_value = print_kafka_status(
            cluster="fake_Cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            kafka_status=mock_kafka_status,
            verbose=1,
        )

        assert return_value == 1
        assert output == [PaastaColors.red("    Kafka cluster is not available yet")]

    def test_successful_return_value(self, mock_kafka_status):
        return_value = print_kafka_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=[],
            kafka_status=mock_kafka_status,
            verbose=1,
        )
        assert return_value == 0

    @patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
    def test_output(
        self, mock_naturaltime, mock_kafka_status,
    ):
        mock_naturaltime.return_value = "one day ago"
        output = []
        print_kafka_status(
            cluster="fake_cluster",
            service="fake_service",
            instance="fake_instance",
            output=output,
            kafka_status=mock_kafka_status,
            verbose=0,
        )

        status = mock_kafka_status["status"]
        expected_output = [
            f"    Kafka View Url: {status['kafka_view_url']}",
            f"    Zookeeper: {status['zookeeper']}",
            f"    State: testing",
            f"    Ready: {str(status['cluster_ready']).lower()}",
            f"    Health: {PaastaColors.red('unhealthy')}",
            f"     Reason: {status['health']['message']}",
            f"     Offline Partitions: {status['health']['offline_partitions']}",
            f"     Under Replicated Partitions: {status['health']['under_replicated_partitions']}",
            f"    Brokers:",
            f"     Id  Phase    Started",
            f"     0   {PaastaColors.green('Running')}  2020-03-25 16:24:21 ({mock_naturaltime.return_value})",
            f"     1   {PaastaColors.red('Pending')}  2020-03-25 16:24:21 ({mock_naturaltime.return_value})",
        ]
        assert expected_output == output


def _formatted_table_to_dict(formatted_table):
    """Convert a single-row table with header to a dictionary"""
    headers = [
        header.strip() for header in formatted_table[0].split("  ") if len(header) > 0
    ]
    fields = [
        field.strip() for field in formatted_table[1].split("  ") if len(field) > 0
    ]
    return dict(zip(headers, fields))


def test_create_autoscaling_info_table():
    mock_autoscaling_info = paastamodels.MarathonAutoscalingInfo(
        current_instances=2,
        max_instances=5,
        min_instances=1,
        current_utilization=0.6,
        target_instances=3,
    )
    output = create_autoscaling_info_table(mock_autoscaling_info)
    assert output[0] == "Autoscaling Info:"

    table_headings_to_values = _formatted_table_to_dict(output[1:])
    assert table_headings_to_values == {
        "Current instances": "2",
        "Max instances": "5",
        "Min instances": "1",
        "Current utilization": "60.0%",
        "Target instances": "3",
    }


def test_create_autoscaling_info_table_errors():
    mock_autoscaling_info = paastamodels.MarathonAutoscalingInfo(
        current_instances=2,
        max_instances=5,
        min_instances=1,
        current_utilization=None,
        target_instances=None,
    )
    output = create_autoscaling_info_table(mock_autoscaling_info)
    table_headings_to_values = _formatted_table_to_dict(output[1:])

    assert table_headings_to_values["Current utilization"] == "Exception"
    assert table_headings_to_values["Target instances"] == "Exception"


@patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
class TestMarathonAppStatusHuman:
    @pytest.fixture
    def mock_app_status(self):
        return Struct(
            tasks_running=5,
            tasks_healthy=4,
            tasks_staged=3,
            tasks_total=12,
            create_timestamp=1565731681,
            deploy_status="Deploying",
            dashboard_url="http://paasta.party",
            backoff_seconds=2,
            unused_offer_reason_counts=None,
            tasks=[],
        )

    def test_marathon_app_status_human(self, mock_naturaltime, mock_app_status):
        output = marathon_app_status_human("app_id", mock_app_status)
        uncolored_output = [remove_ansi_escape_sequences(line) for line in output]

        assert uncolored_output == [
            f"Dashboard: {mock_app_status.dashboard_url}",
            f"  5 running, 4 healthy, 3 staged out of 12",
            f"  App created: 2019-08-13 21:28:01 ({mock_naturaltime.return_value})",
            f"  Status: Deploying",
        ]

    def test_no_dashboard_url(self, mock_naturaltime, mock_app_status):
        mock_app_status.dashboard_url = None
        output = marathon_app_status_human("app_id", mock_app_status)
        assert remove_ansi_escape_sequences(output[0]) == "App ID: app_id"

    @patch("paasta_tools.cli.cmds.status.format_marathon_task_table", autospec=True)
    def test_tasks_list(
        self, mock_format_marathon_task_table, mock_naturaltime, mock_app_status
    ):
        mock_app_status.tasks = [Struct()]
        mock_format_marathon_task_table.return_value = ["task table 1", "task table 2"]
        output = marathon_app_status_human("app_id", mock_app_status)

        expected_task_table_lines = ["  Tasks:", "    task table 1", "    task table 2"]
        assert output[-3:] == expected_task_table_lines

    def test_unused_offers(self, mock_naturaltime, mock_app_status):
        mock_app_status.unused_offer_reason_counts = {"reason1": 5, "reason2": 3}
        output = marathon_app_status_human("app_id", mock_app_status)
        expected_lines = ["  Possibly stalled for:", "    reason1: 5", "    reason2: 3"]
        assert output[-3:] == expected_lines


@patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
class TestFormatMarathonTaskTable:
    @pytest.fixture
    def mock_marathon_task(self):
        return paastamodels.MarathonTask(
            id="abc123",
            host="paasta.cloud",
            port=4321,
            deployed_timestamp=1565648600.0,
            is_healthy=True,
        )

    def test_format_marathon_task_table(self, mock_naturaltime, mock_marathon_task):
        output = format_marathon_task_table([mock_marathon_task])
        task_table_dict = _formatted_table_to_dict(output)
        assert task_table_dict == {
            "Mesos Task ID": "abc123",
            "Host deployed to": "paasta.cloud:4321",
            "Deployed at what localtime": f"2019-08-12T22:23 ({mock_naturaltime.return_value})",
            "Health": PaastaColors.green("Healthy"),
        }

    def test_no_host(self, mock_naturaltime, mock_marathon_task):
        mock_marathon_task.host = None
        output = format_marathon_task_table([mock_marathon_task])
        task_table_dict = _formatted_table_to_dict(output)
        assert task_table_dict["Host deployed to"] == "Unknown"

    def test_unhealthy(self, mock_naturaltime, mock_marathon_task):
        mock_marathon_task.is_healthy = False
        output = format_marathon_task_table([mock_marathon_task])
        task_table_dict = _formatted_table_to_dict(output)
        assert task_table_dict["Health"] == PaastaColors.red("Unhealthy")

    def test_no_health(self, mock_naturaltime, mock_marathon_task):
        mock_marathon_task.is_healthy = None
        output = format_marathon_task_table([mock_marathon_task])
        task_table_dict = _formatted_table_to_dict(output)
        assert task_table_dict["Health"] == PaastaColors.grey("N/A")


@patch("paasta_tools.cli.cmds.status.format_tail_lines_for_mesos_task", autospec=True)
@patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
class TestFormatKubernetesPodTable:
    @pytest.fixture
    def mock_kubernetes_pod(self):
        return paastamodels.KubernetesPod(
            name="abc123",
            host="paasta.cloud",
            deployed_timestamp=1565648600.0,
            phase="Running",
            ready=True,
            containers=[],
            message=None,
            reason=None,
        )

    @pytest.fixture
    def mock_kubernetes_replicaset(self):
        return paastamodels.KubernetesReplicaSet(
            name="abc123",
            replicas=3,
            ready_replicas=3,
            create_timestamp=1565648600.0,
            git_sha="def456",
            config_sha=None,
        )

    def test_format_kubernetes_pod_table(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_pod,
    ):
        output = format_kubernetes_pod_table([mock_kubernetes_pod], verbose=0)
        pod_table_dict = _formatted_table_to_dict(output)
        assert pod_table_dict == {
            "Pod ID": "abc123",
            "Host deployed to": "paasta.cloud",
            "Deployed at what localtime": f"2019-08-12T22:23 ({mock_naturaltime.return_value})",
            "Health": PaastaColors.green("Healthy"),
        }

    def test_format_kubernetes_replicaset_table(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_replicaset,
    ):
        output = format_kubernetes_replicaset_table([mock_kubernetes_replicaset])
        replicaset_table_dict = _formatted_table_to_dict(output)
        assert replicaset_table_dict == {
            "ReplicaSet Name": "abc123",
            "Ready / Desired": PaastaColors.green("3/3"),
            "Created at what localtime": f"2019-08-12T22:23 ({mock_naturaltime.return_value})",
            "Service git SHA": "def456",
            "Config hash": "Unknown",
        }

    def test_no_host(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_pod,
    ):
        mock_kubernetes_pod.host = None
        mock_kubernetes_pod.events = []
        output = format_kubernetes_pod_table([mock_kubernetes_pod], verbose=0)
        pod_table_dict = _formatted_table_to_dict(output)
        assert pod_table_dict["Host deployed to"] == PaastaColors.grey("N/A")

    @pytest.mark.parametrize("phase,ready", [("Failed", False), ("Running", False)])
    def test_unhealthy(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_pod,
        phase,
        ready,
    ):
        mock_kubernetes_pod.phase = phase
        mock_kubernetes_pod.ready = ready
        mock_kubernetes_pod.events = []
        output = format_kubernetes_pod_table([mock_kubernetes_pod], verbose=0)
        pod_table_dict = _formatted_table_to_dict(output)
        assert pod_table_dict["Health"] == PaastaColors.red("Unhealthy")

    def test_evicted(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_pod,
    ):
        mock_kubernetes_pod.phase = "Failed"
        mock_kubernetes_pod.reason = "Evicted"
        mock_kubernetes_pod.events = []
        output = format_kubernetes_pod_table([mock_kubernetes_pod], verbose=0)
        pod_table_dict = _formatted_table_to_dict(output)
        assert pod_table_dict["Health"] == PaastaColors.red("Evicted")

    def test_no_health(
        self,
        mock_naturaltime,
        mock_format_tail_lines_for_mesos_task,
        mock_kubernetes_pod,
    ):
        mock_kubernetes_pod.phase = None
        mock_kubernetes_pod.events = []
        output = format_kubernetes_pod_table([mock_kubernetes_pod], verbose=0)
        pod_table_dict = _formatted_table_to_dict(output)
        assert pod_table_dict["Health"] == PaastaColors.grey("N/A")


@patch("paasta_tools.cli.cmds.status.create_mesos_running_tasks_table", autospec=True)
@patch(
    "paasta_tools.cli.cmds.status.create_mesos_non_running_tasks_table", autospec=True
)
@patch("paasta_tools.cli.cmds.status.marathon_mesos_status_summary", autospec=True)
def test_marathon_mesos_status_human(
    mock_marathon_mesos_status_summary,
    mock_create_mesos_non_running_tasks_table,
    mock_create_mesos_running_tasks_table,
):
    mock_create_mesos_running_tasks_table.return_value = [
        "running task 1",
        "running task 2",
    ]
    mock_create_mesos_non_running_tasks_table.return_value = ["non-running task 1"]

    running_tasks = [
        paastamodels.MarathonMesosRunningTask(),
        paastamodels.MarathonMesosRunningTask(),
    ]
    non_running_tasks = [paastamodels.MarathonMesosNonrunningTask()]
    mesos_status = paastamodels.MarathonMesosStatus(
        running_task_count=2,
        running_tasks=running_tasks,
        non_running_tasks=non_running_tasks,
    )
    output = marathon_mesos_status_human(mesos_status, expected_instance_count=2,)

    assert output == [
        mock_marathon_mesos_status_summary.return_value,
        "  Running Tasks:",
        "    running task 1",
        "    running task 2",
        PaastaColors.grey("  Non-running Tasks:"),
        "    non-running task 1",
    ]
    mock_marathon_mesos_status_summary.assert_called_once_with(2, 2)
    mock_create_mesos_running_tasks_table.assert_called_once_with(running_tasks)
    mock_create_mesos_non_running_tasks_table.assert_called_once_with(non_running_tasks)


def test_marathon_mesos_status_summary():
    status_summary = marathon_mesos_status_summary(
        mesos_task_count=3, expected_instance_count=2
    )
    expected_status = PaastaColors.green("Healthy")
    expected_count = PaastaColors.green(f"(3/2)")
    assert f"{expected_status} - {expected_count}" in status_summary


@patch("paasta_tools.cli.cmds.status.format_tail_lines_for_mesos_task", autospec=True)
@patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
class TestCreateMesosRunningTasksTable:
    @pytest.fixture
    def mock_running_task(self):
        return Struct(
            id="task_id",
            hostname="paasta.yelp.com",
            mem_limit=Struct(value=2 * 1024 * 1024),
            rss=Struct(value=1024 * 1024),
            cpu_shares=Struct(value=0.5),
            cpu_used_seconds=Struct(value=1.2),
            duration_seconds=300,
            deployed_timestamp=1565567511.0,
            tail_lines=Struct(),
        )

    def test_create_mesos_running_tasks_table(
        self, mock_naturaltime, mock_format_tail_lines_for_mesos_task, mock_running_task
    ):
        mock_format_tail_lines_for_mesos_task.return_value = [
            "tail line 1",
            "tail line 2",
        ]
        output = create_mesos_running_tasks_table([mock_running_task])
        running_tasks_dict = _formatted_table_to_dict(output[:2])
        assert running_tasks_dict == {
            "Mesos Task ID": mock_running_task.id,
            "Host deployed to": mock_running_task.hostname,
            "Ram": "1/2MB",
            "CPU": "0.8%",
            "Deployed at what localtime": f"2019-08-11T23:51 ({mock_naturaltime.return_value})",
        }
        assert output[2:] == ["tail line 1", "tail line 2"]
        mock_format_tail_lines_for_mesos_task.assert_called_once_with(
            mock_running_task.tail_lines, mock_running_task.id
        )

    def test_error_messages(
        self, mock_naturaltime, mock_format_tail_lines_for_mesos_task, mock_running_task
    ):
        mock_running_task.mem_limit = paastamodels.FloatAndError(
            error_message="Couldn't get memory"
        )
        mock_running_task.rss = paastamodels.IntegerAndError(value=1)
        mock_running_task.cpu_shares = paastamodels.FloatAndError(
            error_message="Couldn't get CPU"
        )

        output = create_mesos_running_tasks_table([mock_running_task])
        running_tasks_dict = _formatted_table_to_dict(output)
        assert running_tasks_dict["Ram"] == "Couldn't get memory"
        assert running_tasks_dict["CPU"] == "Couldn't get CPU"

    def test_undefined_cpu(
        self, mock_naturaltime, mock_format_tail_lines_for_mesos_task, mock_running_task
    ):
        mock_running_task.cpu_shares.value = 0
        output = create_mesos_running_tasks_table([mock_running_task])
        running_tasks_dict = _formatted_table_to_dict(output)
        assert running_tasks_dict["CPU"] == "Undef"

    def test_high_cpu(
        self, mock_naturaltime, mock_format_tail_lines_for_mesos_task, mock_running_task
    ):
        mock_running_task.cpu_shares.value = 0.1
        mock_running_task.cpu_used_seconds.value = 28
        output = create_mesos_running_tasks_table([mock_running_task])
        running_tasks_dict = _formatted_table_to_dict(output)
        assert running_tasks_dict["CPU"] == PaastaColors.red("93.3%")

    def test_tasks_are_none(
        self, mock_naturaltime, mock_format_tail_lines_for_mesos_task, mock_running_task
    ):
        assert len(create_mesos_running_tasks_table(None)) == 1  # just the header


@patch("paasta_tools.cli.cmds.status.format_tail_lines_for_mesos_task", autospec=True)
@patch("paasta_tools.cli.cmds.status.humanize.naturaltime", autospec=True)
def test_create_mesos_non_running_tasks_table(
    mock_naturaltime, mock_format_tail_lines_for_mesos_task
):
    mock_format_tail_lines_for_mesos_task.return_value = ["tail line 1", "tail line 2"]
    mock_non_running_task = Struct(
        id="task_id",
        hostname="paasta.restaurant",
        deployed_timestamp=1564642800.0,
        state="Not running",
        tail_lines=Struct(),
    )
    output = create_mesos_non_running_tasks_table([mock_non_running_task])
    uncolored_output = [remove_ansi_escape_sequences(line) for line in output]
    task_dict = _formatted_table_to_dict(uncolored_output)
    assert task_dict == {
        "Mesos Task ID": mock_non_running_task.id,
        "Host deployed to": mock_non_running_task.hostname,
        "Deployed at what localtime": f"2019-08-01T07:00 ({mock_naturaltime.return_value})",
        "Status": mock_non_running_task.state,
    }
    assert uncolored_output[2:] == ["tail line 1", "tail line 2"]
    mock_format_tail_lines_for_mesos_task.assert_called_once_with(
        mock_non_running_task.tail_lines, mock_non_running_task.id
    )


@patch("paasta_tools.cli.cmds.status.format_tail_lines_for_mesos_task", autospec=True)
def test_create_mesos_non_running_tasks_table_handles_none_deployed_timestamp(
    mock_format_tail_lines_for_mesos_task,
):
    mock_non_running_task = Struct(
        id="task_id",
        hostname="paasta.restaurant",
        deployed_timestamp=None,
        state="Not running",
        tail_lines=Struct(),
    )
    output = create_mesos_non_running_tasks_table([mock_non_running_task])
    uncolored_output = [remove_ansi_escape_sequences(line) for line in output]
    task_dict = _formatted_table_to_dict(uncolored_output)
    assert task_dict["Deployed at what localtime"] == "Unknown"


def test_create_mesos_non_running_tasks_table_handles_nones():
    assert len(create_mesos_non_running_tasks_table(None)) == 1  # just the header


@patch("paasta_tools.cli.cmds.status.haproxy_backend_report", autospec=True)
@patch("paasta_tools.cli.cmds.status.build_smartstack_backends_table", autospec=True)
def test_get_smartstack_status_human(
    mock_build_smartstack_backends_table, mock_haproxy_backend_report
):
    mock_locations = [
        Struct(
            name="location_1",
            running_backends_count=2,
            backends=[Struct(hostname="location_1_host")],
        ),
        Struct(
            name="location_2",
            running_backends_count=5,
            backends=[
                Struct(hostname="location_2_host1"),
                Struct(hostname="location_2_host2"),
            ],
        ),
    ]
    mock_haproxy_backend_report.side_effect = (
        lambda expected, running: f"haproxy report: {running}/{expected}"
    )
    mock_build_smartstack_backends_table.side_effect = lambda backends: [
        f"{backend.hostname}" for backend in backends
    ]

    output = get_smartstack_status_human(
        registration="fake_service.fake_instance",
        expected_backends_per_location=5,
        locations=mock_locations,
    )
    assert output == [
        "Smartstack:",
        "  Haproxy Service Name: fake_service.fake_instance",
        "  Backends:",
        "    location_1 - haproxy report: 2/5",
        "      location_1_host",
        "    location_2 - haproxy report: 5/5",
        "      location_2_host1",
        "      location_2_host2",
    ]


def test_get_smartstack_status_human_no_locations():
    output = get_smartstack_status_human(
        registration="fake_service.fake_instance",
        expected_backends_per_location=1,
        locations=[],
    )
    assert len(output) == 1
    assert "ERROR" in output[0]


class TestBuildSmartstackBackendsTable:
    @pytest.fixture
    def mock_backend(self):
        return Struct(
            hostname="mock_host",
            port=1138,
            status="UP",
            check_status="L7OK",
            check_code="0",
            check_duration=10,
            last_change=300,
            has_associated_task=True,
        )

    def test_build_smartstack_backends_table(self, mock_backend):
        output = build_smartstack_backends_table([mock_backend])
        backend_dict = _formatted_table_to_dict(output)
        assert backend_dict == {
            "Name": "mock_host:1138",
            "LastCheck": "L7OK/0 in 10ms",
            "LastChange": "5 minutes ago",
            "Status": PaastaColors.default("UP"),
        }

    @pytest.mark.parametrize(
        "backend_status,expected_color",
        [
            ("DOWN", PaastaColors.red),
            ("MAINT", PaastaColors.grey),
            ("OTHER", PaastaColors.yellow),
        ],
    )
    def test_backend_status(self, mock_backend, backend_status, expected_color):
        mock_backend.status = backend_status
        output = build_smartstack_backends_table([mock_backend])
        backend_dict = _formatted_table_to_dict(output)
        assert backend_dict["Status"] == expected_color(backend_status)

    def test_no_associated_task(self, mock_backend):
        mock_backend.has_associated_task = False
        output = build_smartstack_backends_table([mock_backend])
        backend_dict = _formatted_table_to_dict(output)
        assert all(
            field == PaastaColors.grey(remove_ansi_escape_sequences(field))
            for field in backend_dict.values()
        )

    def test_multiple_backends(self, mock_backend):
        assert len(build_smartstack_backends_table([mock_backend, mock_backend])) == 3


def test_get_desired_state_human():
    fake_conf = marathon_tools.MarathonServiceConfig(
        service="service",
        cluster="cluster",
        instance="instance",
        config_dict={},
        branch_dict={"desired_state": "stop"},
    )
    assert "Stopped" in desired_state_human(
        fake_conf.get_desired_state(), fake_conf.get_instances()
    )


def test_get_desired_state_human_started_with_instances():
    fake_conf = marathon_tools.MarathonServiceConfig(
        service="service",
        cluster="cluster",
        instance="instance",
        config_dict={"instances": 42},
        branch_dict={"desired_state": "start"},
    )
    assert "Started" in desired_state_human(
        fake_conf.get_desired_state(), fake_conf.get_instances()
    )


def test_get_desired_state_human_with_0_instances():
    fake_conf = marathon_tools.MarathonServiceConfig(
        service="service",
        cluster="cluster",
        instance="instance",
        config_dict={"instances": 0},
        branch_dict={"desired_state": "start"},
    )
    assert "Stopped" in desired_state_human(
        fake_conf.get_desired_state(), fake_conf.get_instances()
    )


def test_haproxy_backend_report_healthy():
    normal_count = 10
    actual_count = 11
    status = haproxy_backend_report(normal_count, actual_count)
    assert "Healthy" in status


def test_haproxy_backend_report_critical():
    normal_count = 10
    actual_count = 1
    status = haproxy_backend_report(normal_count, actual_count)
    assert "Critical" in status
