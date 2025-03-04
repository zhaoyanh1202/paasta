# Copyright 2015-2019 Yelp Inc.
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

import mock
import pytest

import paasta_tools.instance.kubernetes as pik
from paasta_tools import utils
from tests.conftest import Struct


def test_instance_types_integrity():
    for it in pik.INSTANCE_TYPES:
        assert it in utils.INSTANCE_TYPES
    for it in pik.INSTANCE_TYPES_WITH_SET_STATE:
        assert it in utils.INSTANCE_TYPES


def instance_status_kwargs():
    return dict(
        service="",
        instance="",
        instance_type="",
        verbose=0,
        include_smartstack=False,
        include_envoy=False,
        settings=mock.Mock(),
        use_new=False,
    )


@mock.patch("paasta_tools.instance.kubernetes.cr_status", autospec=True)
@mock.patch("paasta_tools.instance.kubernetes.kubernetes_status", autospec=True)
def test_instance_status_invalid_instance_type(mock_kubernetes_status, mock_cr_status):
    kwargs = instance_status_kwargs()
    with pytest.raises(RuntimeError) as excinfo:
        pik.instance_status(**kwargs)
    assert "Unknown instance type" in str(excinfo.value)
    assert len(mock_cr_status.mock_calls) == 0
    assert len(mock_kubernetes_status.mock_calls) == 0


@mock.patch("paasta_tools.instance.kubernetes.cr_status", autospec=True)
@mock.patch("paasta_tools.instance.kubernetes.kubernetes_status", autospec=True)
def test_instance_status_kubernetes_only(mock_kubernetes_status, mock_cr_status):
    kwargs = instance_status_kwargs()
    kwargs.update(instance_type="kubernetes")
    pik.instance_status(**kwargs)
    assert len(mock_cr_status.mock_calls) == 0
    assert len(mock_kubernetes_status.mock_calls) == 1


@mock.patch("paasta_tools.instance.kubernetes.cr_status", autospec=True)
@mock.patch("paasta_tools.instance.kubernetes.kubernetes_status", autospec=True)
def test_instance_status_cr_only(mock_kubernetes_status, mock_cr_status):
    kwargs = instance_status_kwargs()
    kwargs.update(instance_type="flink")
    pik.instance_status(**kwargs)
    assert len(mock_cr_status.mock_calls) == 1
    assert len(mock_kubernetes_status.mock_calls) == 0


@mock.patch("paasta_tools.instance.kubernetes.cr_status", autospec=True)
@mock.patch("paasta_tools.instance.kubernetes.kubernetes_status", autospec=True)
def test_instance_status_cr_and_kubernetes(mock_kubernetes_status, mock_cr_status):
    kwargs = instance_status_kwargs()
    kwargs.update(instance_type="cassandracluster")
    pik.instance_status(**kwargs)
    assert len(mock_cr_status.mock_calls) == 1
    assert len(mock_kubernetes_status.mock_calls) == 1


@mock.patch("paasta_tools.instance.kubernetes.job_status", autospec=True)
@mock.patch(
    "paasta_tools.kubernetes_tools.replicasets_for_service_instance", autospec=True
)
@mock.patch("paasta_tools.kubernetes_tools.pods_for_service_instance", autospec=True)
@mock.patch("paasta_tools.kubernetes_tools.get_kubernetes_app_by_name", autospec=True)
@mock.patch(
    "paasta_tools.instance.kubernetes.LONG_RUNNING_INSTANCE_TYPE_HANDLERS",
    autospec=True,
)
def test_kubernetes_status(
    mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS,
    mock_get_kubernetes_app_by_name,
    mock_pods_for_service_instance,
    mock_replicasets_for_service_instance,
    mock_job_status,
):
    mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS["flink"] = mock.Mock()
    mock_pods_for_service_instance.return_value = []
    mock_replicasets_for_service_instance.return_value = []
    status = pik.kubernetes_status(
        service="",
        instance="",
        verbose=0,
        include_smartstack=False,
        include_envoy=False,
        instance_type="flink",
        settings=mock.Mock(),
    )
    assert "app_count" in status
    assert "evicted_count" in status
    assert "bounce_method" in status
    assert "desired_state" in status


@mock.patch("paasta_tools.instance.kubernetes.mesh_status", autospec=True)
@mock.patch("paasta_tools.kubernetes_tools.pods_for_service_instance", autospec=True)
@mock.patch(
    "paasta_tools.kubernetes_tools.load_service_namespace_config", autospec=True
)
@mock.patch(
    "paasta_tools.instance.kubernetes.LONG_RUNNING_INSTANCE_TYPE_HANDLERS",
    autospec=True,
)
class TestKubernetesStatusV2:
    @pytest.fixture
    def mock_pod(self):
        return Struct(
            metadata=Struct(
                owner_references=[Struct(kind="ReplicaSet", name="replicaset_1")],
                name="pod_1",
                creation_timestamp=datetime.datetime(2021, 3, 6),
                deletion_timestamp=None,
                labels={
                    "paasta.yelp.com/git_sha": "aaa000",
                    "paasta.yelp.com/config_sha": "config000",
                },
            ),
            status=Struct(
                pod_ip="1.2.3.4",
                host_ip="4.3.2.1",
                phase="Running",
                reason=None,
                message=None,
                conditions=[
                    Struct(type="Ready", status="True",),
                    Struct(type="PodScheduled", status="True",),
                ],
                container_statuses=[
                    Struct(
                        name="main_container",
                        restart_count=0,
                        state=Struct(
                            running=Struct(started_at=datetime.datetime(2021, 3, 6)),
                            waiting=None,
                            terminated=None,
                        ),
                        last_state=Struct(running=None, waiting=None, terminated=None,),
                    ),
                ],
            ),
            spec=Struct(
                containers=[
                    Struct(
                        name="main_container",
                        liveness_probe=Struct(
                            initial_delay_seconds=1,
                            failure_threshold=2,
                            period_seconds=3,
                            timeout_seconds=4,
                        ),
                    )
                ]
            ),
        )

    @mock.patch(
        "paasta_tools.kubernetes_tools.replicasets_for_service_instance", autospec=True
    )
    def test_replicaset(
        self,
        mock_replicasets_for_service_instance,
        mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS,
        mock_load_service_namespace_config,
        mock_pods_for_service_instance,
        mock_mesh_status,
        mock_pod,
    ):
        mock_job_config = mock.Mock(get_persistent_volumes=mock.Mock(return_value=[]),)
        mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS[
            "kubernetes"
        ].loader.return_value = mock_job_config
        mock_replicasets_for_service_instance.return_value = [
            Struct(
                spec=Struct(replicas=1),
                metadata=Struct(
                    name="replicaset_1",
                    creation_timestamp=datetime.datetime(2021, 3, 5),
                    deletion_timestamp=None,
                    labels={
                        "paasta.yelp.com/git_sha": "aaa000",
                        "paasta.yelp.com/config_sha": "config000",
                    },
                ),
            ),
        ]
        mock_pods_for_service_instance.return_value = [mock_pod]

        mock_load_service_namespace_config.return_value = {}
        mock_job_config.get_registrations.return_value = ["service.instance"]

        status = pik.kubernetes_status_v2(
            service="service",
            instance="instance",
            verbose=0,
            include_smartstack=False,
            include_envoy=False,
            instance_type="kubernetes",
            settings=mock.Mock(),
        )

        assert status == {
            "app_name": mock_job_config.get_sanitised_deployment_name.return_value,
            "desired_state": mock_job_config.get_desired_state.return_value,
            "desired_instances": mock_job_config.get_instances.return_value,
            "bounce_method": mock_job_config.get_bounce_method.return_value,
            "versions": [
                {
                    "type": "ReplicaSet",
                    "name": "replicaset_1",
                    "replicas": 1,
                    "ready_replicas": 0,
                    "create_timestamp": datetime.datetime(2021, 3, 5).timestamp(),
                    "git_sha": "aaa000",
                    "config_sha": "config000",
                    "pods": [
                        {
                            "name": "pod_1",
                            "ip": "1.2.3.4",
                            "create_timestamp": datetime.datetime(
                                2021, 3, 6
                            ).timestamp(),
                            "delete_timestamp": None,
                            "host": "4.3.2.1",
                            "phase": "Running",
                            "reason": None,
                            "message": None,
                            "scheduled": True,
                            "ready": True,
                            "containers": [
                                {
                                    "healthcheck_grace_period": 1,
                                    "name": "main_container",
                                    "restart_count": 0,
                                    "state": "running",
                                    "reason": None,
                                    "message": None,
                                    "last_state": None,
                                    "last_reason": None,
                                    "last_message": None,
                                    "last_duration": None,
                                    "timestamp": datetime.datetime(
                                        2021, 3, 6
                                    ).timestamp(),
                                },
                            ],
                        },
                    ],
                }
            ],
        }

    @mock.patch(
        "paasta_tools.kubernetes_tools.controller_revisions_for_service_instance",
        autospec=True,
    )
    def test_statefulset(
        self,
        mock_controller_revisions_for_service_instance,
        mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS,
        mock_load_service_namespace_config,
        mock_pods_for_service_instance,
        mock_mesh_status,
        mock_pod,
    ):
        mock_job_config = mock.Mock(
            get_persistent_volumes=mock.Mock(return_value=[mock.Mock]),
        )
        mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS[
            "kubernetes"
        ].loader.return_value = mock_job_config
        mock_controller_revisions_for_service_instance.return_value = [
            Struct(
                metadata=Struct(
                    name="controller_revision_1",
                    creation_timestamp=datetime.datetime(2021, 4, 1),
                    labels={
                        "paasta.yelp.com/git_sha": "aaa000",
                        "paasta.yelp.com/config_sha": "config000",
                    },
                ),
            ),
        ]

        mock_pod.metadata.owner_references = []
        mock_pods_for_service_instance.return_value = [mock_pod]

        status = pik.kubernetes_status_v2(
            service="service",
            instance="instance",
            verbose=0,
            include_smartstack=False,
            include_envoy=False,
            instance_type="kubernetes",
            settings=mock.Mock(),
        )

        assert len(status["versions"]) == 1
        assert status["versions"][0] == {
            "name": "controller_revision_1",
            "type": "ControllerRevision",
            "replicas": 1,
            "ready_replicas": 1,
            "create_timestamp": datetime.datetime(2021, 4, 1).timestamp(),
            "git_sha": "aaa000",
            "config_sha": "config000",
            "pods": [mock.ANY],
        }


@mock.patch("paasta_tools.kubernetes_tools.get_kubernetes_app_by_name", autospec=True)
def test_job_status_include_replicaset_non_verbose(mock_get_kubernetes_app_by_name):
    kstatus = {}
    pik.job_status(
        kstatus=kstatus,
        client=mock.Mock(),
        job_config=mock.Mock(),
        pod_list=[],
        replicaset_list=[mock.Mock(), mock.Mock(), mock.Mock()],
        verbose=0,
        namespace=mock.Mock(),
    )

    assert len(kstatus["replicasets"]) == 3


@mock.patch("paasta_tools.instance.kubernetes.job_status", autospec=True)
@mock.patch(
    "paasta_tools.kubernetes_tools.load_service_namespace_config", autospec=True
)
@mock.patch("paasta_tools.instance.kubernetes.mesh_status", autospec=True)
@mock.patch(
    "paasta_tools.kubernetes_tools.replicasets_for_service_instance", autospec=True
)
@mock.patch("paasta_tools.kubernetes_tools.pods_for_service_instance", autospec=True)
@mock.patch("paasta_tools.kubernetes_tools.get_kubernetes_app_by_name", autospec=True)
@mock.patch(
    "paasta_tools.instance.kubernetes.LONG_RUNNING_INSTANCE_TYPE_HANDLERS",
    autospec=True,
)
def test_kubernetes_status_include_smartstack(
    mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS,
    mock_get_kubernetes_app_by_name,
    mock_pods_for_service_instance,
    mock_replicasets_for_service_instance,
    mock_mesh_status,
    mock_load_service_namespace_config,
    mock_job_status,
):
    mock_load_service_namespace_config.return_value = {"proxy_port": 1234}
    mock_LONG_RUNNING_INSTANCE_TYPE_HANDLERS["flink"] = mock.Mock()
    mock_pods_for_service_instance.return_value = []
    mock_replicasets_for_service_instance.return_value = []
    mock_service = mock.Mock()
    status = pik.kubernetes_status(
        service=mock_service,
        instance="",
        verbose=0,
        include_smartstack=True,
        include_envoy=False,
        instance_type="flink",
        settings=mock.Mock(),
    )
    assert (
        mock_load_service_namespace_config.mock_calls[0][2]["service"] is mock_service
    )
    assert mock_mesh_status.mock_calls[0][2]["service"] is mock_service
    assert "app_count" in status
    assert "evicted_count" in status
    assert "bounce_method" in status
    assert "desired_state" in status


def test_cr_status_bad_instance_type():
    with pytest.raises(RuntimeError) as excinfo:
        pik.cr_status(
            service="",
            instance="",
            verbose=0,
            instance_type="marathon",
            kube_client=mock.Mock(),
        )
    assert "Unknown instance type" in str(excinfo.value)


@mock.patch("paasta_tools.kubernetes_tools.get_cr", autospec=True)
def test_cr_status_happy_path(mock_get_cr):
    mock_status = mock.Mock()
    mock_metadata = mock.Mock()
    mock_return = dict(status=mock_status, metadata=mock_metadata)
    mock_get_cr.return_value = mock_return
    status = pik.cr_status(
        service="",
        instance="",
        verbose=0,
        instance_type="flink",
        kube_client=mock.Mock(),
    )
    assert status == mock_return


def test_set_cr_desired_state_invalid_instance_type():
    with pytest.raises(RuntimeError) as excinfo:
        pik.set_cr_desired_state(
            kube_client=mock.Mock(),
            service=mock.Mock(),
            instance=mock.Mock(),
            instance_type="marathon",
            desired_state=mock.Mock(),
        )
    assert "Unknown instance type" in str(excinfo.value)


@mock.patch("paasta_tools.kubernetes_tools.set_cr_desired_state", autospec=True)
def test_set_cr_desired_state_calls_k8s_tools(mock_set_cr_desired_state):
    pik.set_cr_desired_state(
        kube_client=mock.Mock(),
        service=mock.Mock(),
        instance=mock.Mock(),
        instance_type="flink",
        desired_state=mock.Mock(),
    )
    assert len(mock_set_cr_desired_state.mock_calls) == 1


def test_can_set_state():
    for it in pik.INSTANCE_TYPES_WITH_SET_STATE:
        assert pik.can_set_state(it)

    assert not pik.can_set_state("marathon")


def test_can_handle():
    for it in pik.INSTANCE_TYPES:
        assert pik.can_handle(it)

    assert not pik.can_handle("marathon")


def test_filter_actually_running_replicasets():
    replicaset_list = [
        mock.Mock(),
        mock.Mock(),
        mock.Mock(),
        mock.Mock(),
    ]
    # the `spec` kwarg is special to Mock so we have to set it this way.
    replicaset_list[0].configure_mock(
        **{"spec.replicas": 5, "status.ready_replicas": 5}
    )
    replicaset_list[1].configure_mock(
        **{"spec.replicas": 5, "status.ready_replicas": 0}
    )
    replicaset_list[2].configure_mock(
        **{"spec.replicas": 0, "status.ready_replicas": 0}
    )
    replicaset_list[3].configure_mock(
        **{"spec.replicas": 0, "status.ready_replicas": 5}
    )

    expected = [
        replicaset_list[0],
        replicaset_list[1],
        replicaset_list[3],
    ]
    assert pik.filter_actually_running_replicasets(replicaset_list) == expected


@mock.patch("paasta_tools.instance.kubernetes.get_pod_containers", autospec=True)
@mock.patch("paasta_tools.kubernetes_tools.is_pod_scheduled", autospec=True)
def test_get_pod_status_ready_with_backends(
    mock_is_pod_scheduled, mock_get_pod_containers
):
    mock_get_pod_containers.return_value = []
    mock_is_pod_scheduled.return_value = True
    mock_pod = mock.MagicMock()
    mock_pod.status.pod_ip = "1.2.3.4"
    mock_ready_condition = mock.MagicMock()
    mock_ready_condition.type = "Ready"
    mock_ready_condition.status = "True"
    mock_pod.status.conditions = [mock_ready_condition]
    backends = [{"address": "0.0.0.0"}]
    status = pik.get_pod_status(mock_pod, backends)
    assert not status["ready"]


@mock.patch(
    "paasta_tools.kubernetes_tools.load_service_namespace_config", autospec=True
)
@mock.patch("paasta_tools.instance.kubernetes.mesh_status", autospec=True)
@mock.patch("paasta_tools.kubernetes_tools.pods_for_service_instance", autospec=True)
@mock.patch(
    "paasta_tools.instance.kubernetes.LONG_RUNNING_INSTANCE_TYPE_HANDLERS",
    {"flink": mock.Mock()},
    autospec=False,
)
@pytest.mark.parametrize(
    "include_smartstack,include_envoy,expected",
    [
        (True, True, ("smartstack", "envoy")),
        (True, False, ("smartstack",)),
        (False, True, ("envoy",)),
    ],
)
def test_kubernetes_mesh_status(
    mock_pods_for_service_instance,
    mock_mesh_status,
    mock_load_service_namespace_config,
    include_smartstack,
    include_envoy,
    expected,
):
    mock_load_service_namespace_config.return_value = {"proxy_port": 1234}
    mock_pods_for_service_instance.return_value = ["pod_1"]
    mock_job_config = pik.LONG_RUNNING_INSTANCE_TYPE_HANDLERS[
        "flink"
    ].loader.return_value
    mock_settings = mock.Mock()

    kmesh = pik.kubernetes_mesh_status(
        service="fake_service",
        instance="fake_instance",
        instance_type="flink",
        settings=mock_settings,
        include_smartstack=include_smartstack,
        include_envoy=include_envoy,
    )

    assert len(kmesh) == len(expected)
    for i in range(len(expected)):
        mesh_type = expected[i]
        assert kmesh.get(mesh_type) == mock_mesh_status.return_value
        assert mock_mesh_status.call_args_list[i] == mock.call(
            service="fake_service",
            instance=mock_job_config.get_nerve_namespace.return_value,
            job_config=mock_job_config,
            service_namespace_config={"proxy_port": 1234},
            pods=["pod_1"],
            should_return_individual_backends=True,
            settings=mock_settings,
            service_mesh=getattr(pik.ServiceMesh, mesh_type.upper()),
        )


@mock.patch(
    "paasta_tools.kubernetes_tools.load_service_namespace_config", autospec=True
)
@mock.patch("paasta_tools.instance.kubernetes.mesh_status", autospec=True)
@mock.patch(
    "paasta_tools.kubernetes_tools.pods_for_service_instance",
    mock.Mock(return_value=("pod_1")),
    autospec=False,
)
@mock.patch(
    "paasta_tools.instance.kubernetes.LONG_RUNNING_INSTANCE_TYPE_HANDLERS",
    {"flink": mock.Mock()},
    autospec=False,
)
@pytest.mark.parametrize(
    "include_mesh,inst_type,service_ns_conf,expected_msg",
    [
        (False, "flink", {"proxy_port": 1234}, "No mesh types"),
        (True, "tron", {"proxy_port": 1234}, "not supported"),
        (True, "flink", {}, "not configured"),
    ],
)
def test_kubernetes_mesh_status_error(
    mock_mesh_status,
    mock_load_service_namespace_config,
    include_mesh,
    inst_type,
    service_ns_conf,
    expected_msg,
):
    mock_load_service_namespace_config.return_value = service_ns_conf
    mock_settings = mock.Mock()

    with pytest.raises(RuntimeError) as excinfo:
        pik.kubernetes_mesh_status(
            service="fake_service",
            instance="fake_instance",
            instance_type=inst_type,
            settings=mock_settings,
            include_smartstack=include_mesh,
            include_envoy=include_mesh,
        )

    assert expected_msg in excinfo.value.args[0]
    assert mock_mesh_status.call_args_list == []
