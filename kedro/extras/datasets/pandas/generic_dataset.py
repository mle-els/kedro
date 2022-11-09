"""``GenericDataSet`` loads/saves data from/to a data file using an underlying
filesystem (e.g.: local, S3, GCS). It uses pandas to handle the
type of read/write target.
"""
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Dict

import fsspec
import pandas as pd

from kedro.io.core import (
    AbstractVersionedDataSet,
    DataSetError,
    Version,
    get_filepath_str,
    get_protocol_and_path,
)

# NOTE: kedro.extras.datasets will be removed in Kedro 0.19.0.
# Any contribution to datasets should be made in kedro-datasets
# in kedro-plugins (https://github.com/kedro-org/kedro-plugins)


NON_FILE_SYSTEM_TARGETS = [
    "clipboard",
    "numpy",
    "sql",
    "period",
    "records",
    "timestamp",
    "xarray",
    "sql_table",
]


class GenericDataSet(AbstractVersionedDataSet[pd.DataFrame, pd.DataFrame]):
    """`pandas.GenericDataSet` loads/saves data from/to a data file using an underlying
    filesystem (e.g.: local, S3, GCS). It uses pandas to dynamically select the
    appropriate type of read/write target on a best effort basis.

    Example using `YAML API
    <https://kedro.readthedocs.io/en/stable/data/\
        data_catalog.html#use-the-data-catalog-with-the-yaml-api>`_:

    .. code-block:: yaml

        >>> cars:
        >>>   type: pandas.GenericDataSet
        >>>   file_format: csv
        >>>   filepath: s3://data/01_raw/company/cars.csv
        >>>   load_args:
        >>>     sep: ","
        >>>     na_values: ["#NA", NA]
        >>>   save_args:
        >>>     index: False
        >>>     date_format: "%Y-%m-%d"

    This second example is able to load a SAS7BDAT file via the :code:`pd.read_sas` method.
    Trying to save this dataset will raise a `DataSetError` since pandas does not provide an
    equivalent :code:`pd.DataFrame.to_sas` write method.

    .. code-block:: yaml

        >>> flights:
        >>>    type: pandas.GenericDataSet
        >>>    file_format: sas
        >>>    filepath: data/01_raw/airplanes.sas7bdat
        >>>    load_args:
        >>>       format: sas7bdat

    Example using Python API:
    ::

        >>> from kedro.extras.datasets.pandas import GenericDataSet
        >>> import pandas as pd
        >>>
        >>> data = pd.DataFrame({'col1': [1, 2], 'col2': [4, 5],
        >>>                      'col3': [5, 6]})
        >>>
        >>> # data_set = GenericDataSet(filepath="s3://test.csv", file_format='csv')
        >>> data_set = GenericDataSet(filepath="test.csv", file_format='csv')
        >>> data_set.save(data)
        >>> reloaded = data_set.load()
        >>> assert data.equals(reloaded)

    """

    DEFAULT_LOAD_ARGS = {}  # type: Dict[str, Any]
    DEFAULT_SAVE_ARGS = {}  # type: Dict[str, Any]

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        filepath: str,
        file_format: str,
        load_args: Dict[str, Any] = None,
        save_args: Dict[str, Any] = None,
        version: Version = None,
        credentials: Dict[str, Any] = None,
        fs_args: Dict[str, Any] = None,
    ):
        """Creates a new instance of ``GenericDataSet`` pointing to a concrete data file
        on a specific filesystem. The appropriate pandas load/save methods are
        dynamically identified by string matching on a best effort basis.

        Args:
            filepath: Filepath in POSIX format to a file prefixed with a protocol like `s3://`.
                If prefix is not provided, `file` protocol (local filesystem) will be used.
                The prefix should be any protocol supported by ``fsspec``.
                Key assumption: The first argument of either load/save method points to a
                filepath/buffer/io type location. There are some read/write targets such
                as 'clipboard' or 'records' that will fail since they do not take a
                filepath like argument.
            file_format: String which is used to match the appropriate load/save method on a best
                effort basis. For example if 'csv' is passed in the `pandas.read_csv` and
                `pandas.DataFrame.to_csv` will be identified. An error will be raised unless
                at least one matching `read_{file_format}` or `to_{file_format}` method is
                identified.
            load_args: Pandas options for loading files.
                Here you can find all available arguments:
                https://pandas.pydata.org/pandas-docs/stable/reference/io.html
                All defaults are preserved.
            save_args: Pandas options for saving files.
                Here you can find all available arguments:
                https://pandas.pydata.org/pandas-docs/stable/reference/io.html
                All defaults are preserved, but "index", which is set to False.
            version: If specified, should be an instance of
                ``kedro.io.core.Version``. If its ``load`` attribute is
                None, the latest version will be loaded. If its ``save``
                attribute is None, save version will be autogenerated.
            credentials: Credentials required to get access to the underlying filesystem.
                E.g. for ``GCSFileSystem`` it should look like `{"token": None}`.
            fs_args: Extra arguments to pass into underlying filesystem class constructor
                (e.g. `{"project": "my-project"}` for ``GCSFileSystem``), as well as
                to pass to the filesystem's `open` method through nested keys
                `open_args_load` and `open_args_save`.
                Here you can find all available arguments for `open`:
                https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem.open
                All defaults are preserved, except `mode`, which is set to `r` when loading
                and to `w` when saving.

        Raises:
            DataSetError: Will be raised if at least less than one appropriate
                read or write methods are identified.
        """

        self._file_format = file_format.lower()

        _fs_args = deepcopy(fs_args) or {}
        _fs_open_args_load = _fs_args.pop("open_args_load", {})
        _fs_open_args_save = _fs_args.pop("open_args_save", {})
        _credentials = deepcopy(credentials) or {}

        protocol, path = get_protocol_and_path(filepath)
        if protocol == "file":
            _fs_args.setdefault("auto_mkdir", True)

        self._protocol = protocol
        self._fs = fsspec.filesystem(self._protocol, **_credentials, **_fs_args)

        super().__init__(
            filepath=PurePosixPath(path),
            version=version,
            exists_function=self._fs.exists,
            glob_function=self._fs.glob,
        )

        self._load_args = deepcopy(self.DEFAULT_LOAD_ARGS)
        if load_args is not None:
            self._load_args.update(load_args)
        self._save_args = deepcopy(self.DEFAULT_SAVE_ARGS)
        if save_args is not None:
            self._save_args.update(save_args)

        _fs_open_args_save.setdefault("mode", "w")
        self._fs_open_args_load = _fs_open_args_load
        self._fs_open_args_save = _fs_open_args_save

    def _ensure_file_system_target(self) -> None:
        # Fail fast if provided a known non-filesystem target
        if self._file_format in NON_FILE_SYSTEM_TARGETS:
            raise DataSetError(
                f"Cannot create a dataset of file_format '{self._file_format}' as it "
                f"does not support a filepath target/source."
            )

    def _load(self) -> pd.DataFrame:

        self._ensure_file_system_target()

        load_path = get_filepath_str(self._get_load_path(), self._protocol)
        load_method = getattr(pd, f"read_{self._file_format}", None)
        if load_method:
            with self._fs.open(load_path, **self._fs_open_args_load) as fs_file:
                return load_method(fs_file, **self._load_args)
        raise DataSetError(
            f"Unable to retrieve 'pandas.read_{self._file_format}' method, please ensure that your "
            "'file_format' parameter has been defined correctly as per the Pandas API "
            "https://pandas.pydata.org/docs/reference/io.html"
        )

    def _save(self, data: pd.DataFrame) -> None:

        self._ensure_file_system_target()

        save_path = get_filepath_str(self._get_save_path(), self._protocol)
        save_method = getattr(data, f"to_{self._file_format}", None)
        if save_method:
            with self._fs.open(save_path, **self._fs_open_args_save) as fs_file:
                # KEY ASSUMPTION - first argument is path/buffer/io
                save_method(fs_file, **self._save_args)
                self._invalidate_cache()
        else:
            raise DataSetError(
                f"Unable to retrieve 'pandas.DataFrame.to_{self._file_format}' method, please "
                "ensure that your 'file_format' parameter has been defined correctly as "
                "per the Pandas API "
                "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html"
            )

    def _exists(self) -> bool:
        try:
            load_path = get_filepath_str(self._get_load_path(), self._protocol)
        except DataSetError:
            return False

        return self._fs.exists(load_path)

    def _describe(self) -> Dict[str, Any]:
        return dict(
            file_format=self._file_format,
            filepath=self._filepath,
            protocol=self._protocol,
            load_args=self._load_args,
            save_args=self._save_args,
            version=self._version,
        )

    def _release(self) -> None:
        super()._release()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Invalidate underlying filesystem caches."""
        filepath = get_filepath_str(self._filepath, self._protocol)
        self._fs.invalidate_cache(filepath)
