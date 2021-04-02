#!/usr/bin/env python
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
import concurrent.futures
from typing import List
from typing import Mapping
from typing import Sequence
from typing import Tuple
from typing import Type

from paasta_tools.api.client import get_paasta_oapi_client
from paasta_tools.cli.cmds.status import add_instance_filter_arguments
from paasta_tools.cli.cmds.status import apply_args_filters
from paasta_tools.cli.cmds.status import get_envoy_status_human
from paasta_tools.cli.cmds.status import get_smartstack_status_human
from paasta_tools.utils import DEFAULT_SOA_DIR
from paasta_tools.utils import InstanceConfig
from paasta_tools.utils import load_system_paasta_config
from paasta_tools.utils import PaastaColors
from paasta_tools.utils import SystemPaastaConfig


def add_subparser(subparsers) -> None:
    mesh_status_parser = subparsers.add_parser(
        "mesh-status",
        help="Display the mesh status of a PaaSTA service.",
        description=(
            "'paasta mesh-status' queries the PaaSTA API in order to report "
            "on the health of a PaaSTA service in the mesh."
        ),
    )
    add_instance_filter_arguments(mesh_status_parser)
    mesh_status_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        dest="verbose",
        default=0,
        help=(
            "Prints the health of individual backends in the mesh, instead of "
            "the overall health"
        ),
    )
    mesh_status_parser.add_argument(
        "-d",
        "--soa-dir",
        dest="soa_dir",
        metavar="SOA_DIR",
        default=DEFAULT_SOA_DIR,
        help="define a different soa config directory",
    )
    mesh_status_parser.set_defaults(command=paasta_mesh_status)


def paasta_mesh_status_on_api_endpoint(
    cluster: str,
    service: str,
    instance: str,
    instance_type: str,
    verbose: int,
    system_paasta_config: SystemPaastaConfig,
) -> Tuple[int, List[str]]:
    client = get_paasta_oapi_client(cluster, system_paasta_config)
    if not client:
        print("Cannot get a paasta-api client")
        exit(1)

    try:
        mesh_status = client.service.mesh_instance(
            service=service,
            instance=instance,
            verbose=verbose,
            include_smartstack=False,
        )
    except client.api_error as exc:
        # 405 (method not allowed) is returned for instances that are not configured
        # for the mesh, or for which getting mesh status is not supported
        return (
            exc.status,
            [PaastaColors.red(exc.body if exc.status == 405 else exc.reason)],
        )
    except (client.connection_error, client.timeout_error) as exc:
        return (
            1,
            [PaastaColors.red(f"Could not connect to API: {exc.__class__.__name__}")],
        )
    except Exception as e:
        output = [PaastaColors.red(f"Exception when talking to the API:")]
        output.extend(str(e).split("\n"))
        return 1, output

    output = []
    if mesh_status.smartstack is not None:
        smartstack_status_human = get_smartstack_status_human(
            mesh_status.smartstack.registration,
            mesh_status.smartstack.expected_backends_per_location,
            mesh_status.smartstack.locations,
        )
        output.extend(smartstack_status_human)
    if mesh_status.envoy is not None:
        envoy_status_human = get_envoy_status_human(
            mesh_status.envoy.registration,
            mesh_status.envoy.expected_backends_per_location,
            mesh_status.envoy.locations,
        )
        output.extend(envoy_status_human)

    return 0, output


def report_mesh_status_for_cluster(
    cluster: str,
    service: str,
    instance_whitelist: Mapping[str, Type[InstanceConfig]],
    system_paasta_config: SystemPaastaConfig,
    verbose: int = 0,
) -> Tuple[int, Sequence[str]]:
    output = [f"service: {service}", f"cluster: {cluster}"]
    return_codes = []

    for instance, instance_config_class in instance_whitelist.items():
        return_code, instance_output = paasta_mesh_status_on_api_endpoint(
            cluster=cluster,
            service=service,
            instance=instance,
            instance_type=instance_config_class.config_filename_prefix,
            verbose=verbose,
            system_paasta_config=system_paasta_config,
        )

        return_codes.append(return_code)
        output.append(f"  instance: {PaastaColors.cyan(instance)}")
        output.extend(["    " + line for line in instance_output])

    return 1 if any(return_codes) else 0, output


def paasta_mesh_status(args) -> int:
    system_paasta_config = load_system_paasta_config()

    return_codes = [0]
    tasks = []
    clusters_services_instances = apply_args_filters(args)
    for cluster, service_instances in clusters_services_instances.items():
        for service, instances in service_instances.items():
            tasks.append(
                (
                    report_mesh_status_for_cluster,
                    dict(
                        cluster=cluster,
                        service=service,
                        instance_whitelist=instances,
                        system_paasta_config=system_paasta_config,
                        verbose=args.verbose,
                    ),
                )
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        tasks = [executor.submit(t[0], **t[1]) for t in tasks]  # type: ignore
        for future in concurrent.futures.as_completed(tasks):  # type: ignore
            return_code, output = future.result()
            print("\n".join(output))
            return_codes.append(return_code)

    return max(return_codes)
