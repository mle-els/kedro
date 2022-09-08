# pylint: disable=global-statement,invalid-name
"""
This script creates an IPython extension to load Kedro-related variables in
local scope.
"""
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from IPython import get_ipython
from IPython.core.magic import needs_local_scope, register_line_magic
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring

from kedro.framework.cli import load_entry_points
from kedro.framework.cli.project import PARAMS_ARG_HELP
from kedro.framework.cli.utils import ENV_HELP, _split_params
from kedro.framework.project import LOGGING  # noqa
from kedro.framework.project import configure_project, pipelines
from kedro.framework.session import KedroSession
from kedro.framework.startup import _is_project, bootstrap_project

logger = logging.getLogger(__name__)
default_project_path = Path.cwd()


def _remove_cached_modules(package_name):
    to_remove = [mod for mod in sys.modules if mod.startswith(package_name)]
    # `del` is used instead of `reload()` because: If the new version of a module does not
    # define a name that was defined by the old version, the old definition remains.
    for module in to_remove:
        del sys.modules[module]  # pragma: no cover


def _find_kedro_project(current_dir: Path):  # pragma: no cover
    while current_dir != current_dir.parent:
        if _is_project(current_dir):
            return current_dir
        current_dir = current_dir.parent

    return None


def reload_kedro(
    path: str = None, env: str = None, extra_params: Dict[str, Any] = None
):
    """Function that underlies the %reload_kedro Line magic. This should not be imported
    or run directly but instead invoked through %reload_kedro."""

    # If a path is provided, set it as default for subsequent calls
    global default_project_path
    if path:
        default_project_path = Path(path).expanduser().resolve()
        logger.info("Updated path to Kedro project: %s", default_project_path)
    else:
        logger.info("No path argument was provided. Using: %s", default_project_path)

    metadata = bootstrap_project(default_project_path)
    _remove_cached_modules(metadata.package_name)
    configure_project(metadata.package_name)

    session = KedroSession.create(
        metadata.package_name, default_project_path, env=env, extra_params=extra_params
    )
    context = session.load_context()
    catalog = context.catalog

    get_ipython().push(
        variables={
            "context": context,
            "catalog": catalog,
            "session": session,
            "pipelines": pipelines,
        }
    )

    logger.info("Kedro project %s", str(metadata.project_name))
    logger.info(
        "Defined global variable 'context', 'session', 'catalog' and 'pipelines'"
    )

    for line_magic in load_entry_points("line_magic"):
        register_line_magic(needs_local_scope(line_magic))
        logger.info("Registered line magic '%s'", line_magic.__name__)  # type: ignore


@magic_arguments()
@argument(
    "path",
    type=str,
    help=(
        "Path to the project root directory. If not given, use the previously set"
        "project root."
    ),
    nargs="?",
    default=None,
)
@argument("-e", "--env", type=str, default=None, help=ENV_HELP)
@argument(
    "--params",
    type=lambda value: _split_params(None, None, value),
    default=None,
    help=PARAMS_ARG_HELP,
)
def magic_reload_kedro(line: str):
    """
    The `%reload_kedro` IPython line magic. See
     https://kedro.readthedocs.io/en/stable/tools_integration/ipython.html for more.
    """
    args = parse_argstring(magic_reload_kedro, line)
    reload_kedro(args.path, args.env, args.params)


def load_ipython_extension(ipython):
    """
    Main entry point when %load_ext kedro.ipython is executed, either manually or
    automatically through `kedro ipython` or `kedro jupyter lab/notebook`.
    IPython will look for this function specifically.
    See https://ipython.readthedocs.io/en/stable/config/extensions/index.html
    """

    global default_project_path

    ipython.register_magic_function(magic_reload_kedro, magic_name="reload_kedro")
    default_project_path = _find_kedro_project(Path.cwd())

    if default_project_path is None:
        logger.warning(
            "Kedro extension was registered but couldn't find a Kedro project. "
            "Make sure you run '%reload_kedro <project_root>'."
        )
        return

    reload_kedro(default_project_path)