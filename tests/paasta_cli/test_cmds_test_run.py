import docker
import mock
from pytest import raises

from paasta_tools.marathon_tools import MarathonServiceConfig
from paasta_tools.paasta_cli.cmds.test_run import build_docker_container
from paasta_tools.paasta_cli.cmds.test_run import get_cmd
from paasta_tools.paasta_cli.cmds.test_run import get_cmd_string
from paasta_tools.paasta_cli.cmds.test_run import get_docker_run_cmd
from paasta_tools.paasta_cli.cmds.test_run import paasta_test_run
from paasta_tools.paasta_cli.cmds.test_run import run_docker_container
from paasta_tools.paasta_cli.cmds.test_run import validate_environment


def test_build_docker_container():
    docker_client = mock.MagicMock()
    args = mock.MagicMock()

    docker_client.build.return_value = [
        '{"stream":null}',
        '{"stream":"foo\\n"}',
        '{"stream":"foo\\n"}',
        '{"stream":"Successfully built 1234\\n"}'
    ]
    assert build_docker_container(docker_client, args) == '1234'


@mock.patch('os.path.expanduser', autospec=True)
@mock.patch('os.getcwd', autospec=True)
def test_validate_environment_fail_in_homedir(
    mock_getcwd,
    mock_expanduser,
):
    fake_home = '/fake_home'
    mock_getcwd.return_value = fake_home
    mock_expanduser.return_value = fake_home

    with raises(SystemExit) as sys_exit:
        validate_environment()

    assert sys_exit.value.code == 1


@mock.patch('os.path.join', autospec=True)
@mock.patch('os.path.isfile', autospec=True)
@mock.patch('os.path.expanduser', autospec=True)
@mock.patch('os.getcwd', autospec=True)
def test_validate_environment_fail_no_dockerfile(
    mock_getcwd,
    mock_expanduser,
    mock_isfile,
    mock_pathjoin,
):
    mock_getcwd.return_value = 'doesntmatter'
    mock_expanduser.return_value = 'nothomedir'
    mock_pathjoin.return_value = 'something'
    mock_isfile.return_value = False

    with raises(SystemExit) as sys_exit:
        validate_environment()

    assert sys_exit.value.code == 1


@mock.patch('paasta_tools.paasta_cli.cmds.test_run.validate_environment', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.figure_out_service_name', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.validate_service_name', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.configure_and_run_docker_container', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.build_docker_container', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.Client', autospec=True)
def test_run_success(
    mock_Client,
    mock_build_docker_container,
    mock_run_docker_container,
    mock_validate_service_name,
    mock_figure_out_service_name,
    mock_validate_environment,
):
    mock_Client.return_value = None
    mock_build_docker_container.return_value = None
    mock_run_docker_container.return_value = None
    mock_validate_service_name.return_value = True
    mock_figure_out_service_name.return_value = 'fake_service'
    mock_validate_environment.return_value = None

    args = mock.MagicMock()
    args.service = 'fake_service'

    assert paasta_test_run(args) is None


def test_get_docker_run_cmd_interactive_false():
    memory = 555
    random_port = 666
    volumes = ['7_Brides_for_7_Brothers', '7-Up', '7-11']
    interactive = False
    docker_hash = '8' * 40
    command = ['IE9.exe', '/VERBOSE', '/ON_ERROR_RESUME_NEXT']
    actual = get_docker_run_cmd(memory, random_port, volumes, interactive, docker_hash, command)

    assert any(['--env=PORT=' in arg for arg in actual])
    assert '--memory=%dm' % memory in actual
    assert any(['--publish=%s' % random_port in arg for arg in actual])
    assert all(['--volume=%s' % volume in actual for volume in volumes])
    assert '--tty=true' not in actual
    assert '--interactive=true' not in actual
    assert docker_hash in actual
    assert all([arg in actual for arg in command])


def test_get_docker_run_cmd_interactive_true():
    memory = 555
    random_port = 666
    volumes = ['7_Brides_for_7_Brothers', '7-Up', '7-11']
    interactive = True
    docker_hash = '8' * 40
    command = ['IE9.exe', '/VERBOSE', '/ON_ERROR_RESUME_NEXT']
    actual = get_docker_run_cmd(memory, random_port, volumes, interactive, docker_hash, command)

    assert '--tty=true' in actual
    assert '--interactive=true' in actual


def test_get_container_id():
    pass


@mock.patch('paasta_tools.paasta_cli.cmds.test_run.pick_random_port', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.get_docker_run_cmd', autospec=True)
@mock.patch('paasta_tools.paasta_cli.cmds.test_run.execlp', autospec=True)
def test_run_docker_container(
    mock_execlp,
    mock_get_docker_run_cmd,
    mock_pick_random_port,
):
    mock_pick_random_port.return_value = 666
    mock_docker_client = mock.MagicMock(spec_set=docker.Client)
    mock_docker_client.stop = mock.MagicMock(spec_set=docker.Client.stop)
    mock_docker_client.remove_container = mock.MagicMock(spec_set=docker.Client.remove_container)
    mock_service_manifest = mock.MagicMock(spec_set=MarathonServiceConfig)
    run_docker_container(
        mock_docker_client,
        'fake_service',
        'fake_instance',
        'fake_hash',
        [],
        False,  # interactive
        'fake_command',
        mock_service_manifest,
    )
    mock_service_manifest.get_mem.assert_called_once_with()
    mock_pick_random_port.assert_called_once_with()
    assert mock_get_docker_run_cmd.call_count == 1
    assert mock_execlp.call_count == 1


@mock.patch('paasta_tools.paasta_cli.cmds.test_run.get_cmd', autospec=True)
def test_get_cmd_string(
    mock_get_cmd,
):
    mock_get_cmd.return_value = 'fake_cmd'
    actual = get_cmd_string()
    assert 'fake_cmd' in actual


@mock.patch('paasta_tools.paasta_cli.cmds.test_run.read_local_dockerfile_lines', autospec=True)
def test_get_cmd_when_working(
    mock_read_local_dockerfile_lines,
):
    mock_read_local_dockerfile_lines.return_value = ['CMD BLA']
    actual = get_cmd()
    assert 'BLA' == actual


@mock.patch('paasta_tools.paasta_cli.cmds.test_run.read_local_dockerfile_lines', autospec=True)
def test_get_cmd_when_unknown(
    mock_read_local_dockerfile_lines,
):
    mock_read_local_dockerfile_lines.return_value = []
    actual = get_cmd()
    assert 'Unknown' in actual
