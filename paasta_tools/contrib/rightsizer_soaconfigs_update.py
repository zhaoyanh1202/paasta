import argparse
import logging
from collections import defaultdict

from paasta_tools.config_utils import AutoConfigUpdater
from paasta_tools.contrib.paasta_update_soa_memcpu import get_report_from_splunk
from paasta_tools.utils import DEFAULT_SOA_CONFIGS_GIT_URL
from paasta_tools.utils import format_git_url
from paasta_tools.utils import load_system_paasta_config

NULL = "null"
SUPPORTED_CSV_KEYS = (
    "cpus",
    "mem",
    "disk",
    "hacheck_cpus",
    "cpu_burst_add",
    "min_instances",
    "max_instances",
)


def parse_args():
    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "-s",
        "--splunk-creds",
        help="Service credentials for Splunk API, user:pass",
        dest="splunk_creds",
        required=True,
    )
    parser.add_argument(
        "-f",
        "--criteria-filter",
        help="Filter Splunk search results criteria field. Default: *",
        dest="criteria_filter",
        required=False,
        default="*",
    )
    parser.add_argument(
        "-c",
        "--csv-report",
        help="Splunk csv file from which to pull data.",
        required=True,
        dest="csv_report",
    )
    parser.add_argument(
        "--csv-key",
        help="Key(s) to apply to config from the csv. If not specified, applies all supported keys.",
        choices=SUPPORTED_CSV_KEYS,
        required=False,
        nargs="*",
        default=None,
        dest="csv_keys",
    )
    parser.add_argument(
        "--app",
        help="Splunk app of the CSV file",
        default="yelp_computeinfra",
        required=False,
        dest="splunk_app",
    )
    parser.add_argument(
        "--git-remote",
        help="Master git repo for soaconfigs",
        default=None,
        dest="git_remote",
    )
    parser.add_argument(
        "--branch",
        help="Branch name to push to. Defaults to master",
        default="master",
        required=False,
        dest="branch",
    )
    parser.add_argument(
        "--push-to-remote",
        help="Actually push to remote. Otherwise files will only be modified and validated.",
        action="store_true",
        dest="push_to_remote",
    )
    parser.add_argument(
        "--local-dir",
        help="Act on configs in the local directory rather than cloning the git_remote",
        required=False,
        default=None,
        dest="local_dir",
    )
    parser.add_argument(
        "--source-id",
        help="String to attribute the changes in the commit message. Defaults to csv report name",
        required=False,
        default=None,
        dest="source_id",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Logging verbosity",
        action="store_true",
        dest="verbose",
    )
    return parser.parse_args()


def get_default_git_remote():
    system_paasta_config = load_system_paasta_config()
    repo_config = system_paasta_config.get_git_repo_config("yelpsoa-configs")
    default_git_remote = format_git_url(
        system_paasta_config.get_git_config()["git_user"],
        repo_config.get("git_server", DEFAULT_SOA_CONFIGS_GIT_URL),
        repo_config["repo_name"],
    )
    return default_git_remote


def get_recommendation_from_result(result, keys_to_apply):
    rec = {}
    for key in keys_to_apply:
        val = result.get(key)
        if not val or val == NULL:
            continue
        if key == "cpus":
            rec["cpus"] = float(val)
        elif key == "cpu_burst_add":
            rec["cpu_burst_add"] = min(1, float(val))
        elif key == "mem":
            rec["mem"] = max(128, round(float(val)))
        elif key == "disk":
            rec["disk"] = max(128, round(float(val)))
        elif key == "min_instances":
            rec["min_instances"] = int(val)
        elif key == "max_instances":
            rec["max_instances"] = int(val)
        elif key == "hacheck_cpus":
            hacheck_cpus_value = max(0.1, min(float(val), 1))
            rec["sidecar_resource_requirements"] = {
                "hacheck": {
                    "requests": {"cpu": hacheck_cpus_value,},
                    "limits": {"cpu": hacheck_cpus_value,},
                },
            }
    return rec


def get_recommendations_by_service_file(results, keys_to_apply):
    results_by_service_file = defaultdict(dict)
    for result in results.values():
        key = (
            result["service"],
            result["cluster"],
        )  # e.g. (foo, marathon-norcal-stagef)
        rec = get_recommendation_from_result(result, keys_to_apply)
        if not rec:
            continue
        results_by_service_file[key][result["instance"]] = rec
    return results_by_service_file


def get_extra_message(splunk_search_string):
    return f"""This review is based on results from the following Splunk search:\n
    {splunk_search_string}
    """


def main(args):
    report = get_report_from_splunk(
        args.splunk_creds, args.splunk_app, args.csv_report, args.criteria_filter
    )
    extra_message = get_extra_message(report["search"])
    config_source = args.source_id or args.csv_report

    keys_to_apply = args.csv_keys or SUPPORTED_CSV_KEYS
    results = get_recommendations_by_service_file(report["results"], keys_to_apply)
    updater = AutoConfigUpdater(
        config_source=config_source,
        git_remote=args.git_remote or get_default_git_remote(),
        branch=args.branch,
        working_dir=args.local_dir or "/nail/tmp",
        do_clone=args.local_dir is None,
    )
    with updater:
        for (service, extra_info), instance_recommendations in results.items():
            existing_recommendations = updater.get_existing_configs(service, extra_info)
            for instance_name, recommendation in instance_recommendations.items():
                existing_recommendations.setdefault(instance_name, {})
                existing_recommendations[instance_name].update(recommendation)
            updater.write_configs(service, extra_info, existing_recommendations)

        if args.push_to_remote:
            updater.commit_to_remote(extra_message=extra_message)
        else:
            updater.validate()


if __name__ == "__main__":
    args = parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    main(args)
