# Copyright 2024 The Aibrix Team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# 	http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import tos
from tos import DataTransferType
from tqdm import tqdm

from aibrix import envs
from aibrix.common.errors import ArgNotCongiuredError, ModelNotFoundError
from aibrix.downloader.base import (
    DEFAULT_DOWNLOADER_EXTRA_CONFIG,
    BaseDownloader,
    DownloadExtraConfig,
)
from aibrix.downloader.entity import RemoteSource, get_local_download_paths
from aibrix.downloader.s3 import S3BaseDownloader
from aibrix.downloader.utils import (
    infer_model_name,
    meta_file,
    need_to_download,
    save_meta_data,
)
from aibrix.logger import init_logger

tos_logger = logging.getLogger("tos")
tos_logger.setLevel(logging.WARNING)
logger = init_logger(__name__)


def _parse_bucket_info_from_uri(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri, scheme="tos")
    bucket_name = parsed.netloc
    bucket_path = parsed.path.lstrip("/")
    return bucket_name, bucket_path


class TOSDownloaderV1(BaseDownloader):
    _source: ClassVar[RemoteSource] = RemoteSource.TOS

    def __init__(
        self,
        model_uri,
        model_name: Optional[str] = None,
        download_extra_config: DownloadExtraConfig = DEFAULT_DOWNLOADER_EXTRA_CONFIG,
        enable_progress_bar: bool = False,
    ):
        if model_name is None:
            model_name = infer_model_name(model_uri)
            logger.info(f"model_name is not set, using `{model_name}` as model_name")

        self.download_extra_config = download_extra_config
        ak = self.download_extra_config.ak or envs.DOWNLOADER_TOS_ACCESS_KEY or ""
        sk = self.download_extra_config.sk or envs.DOWNLOADER_TOS_SECRET_KEY or ""
        endpoint = (
            self.download_extra_config.endpoint or envs.DOWNLOADER_TOS_ENDPOINT or ""
        )
        region = self.download_extra_config.region or envs.DOWNLOADER_TOS_REGION or ""
        enable_crc = envs.DOWNLOADER_TOS_ENABLE_CRC
        bucket_name, bucket_path = _parse_bucket_info_from_uri(model_uri)

        self.client = tos.TosClientV2(
            ak=ak, sk=sk, endpoint=endpoint, region=region, enable_crc=enable_crc
        )

        super().__init__(
            model_uri=model_uri,
            model_name=model_name,
            bucket_path=bucket_path,
            bucket_name=bucket_name,
            download_extra_config=download_extra_config,
            enable_progress_bar=enable_progress_bar,
        )  # type: ignore

    def _valid_config(self):
        if self.model_name is None or self.model_name == "":
            raise ArgNotCongiuredError(arg_name="model_name", arg_source="--model-name")

        if self.bucket_name is None or self.bucket_name == "":
            raise ArgNotCongiuredError(arg_name="bucket_name", arg_source="--model-uri")

        if self.bucket_path is None or self.bucket_path == "":
            raise ArgNotCongiuredError(arg_name="bucket_path", arg_source="--model-uri")

        try:
            self.client.head_bucket(self.bucket_name)
        except Exception as e:
            logger.error(f"TOS bucket {self.bucket_name} not exist for {e}")
            raise ModelNotFoundError(model_uri=self.model_uri, detail_msg=str(e))

    @lru_cache()
    def _is_directory(self) -> bool:
        """Check if model_uri is a directory."""
        if self.bucket_path.endswith("/"):
            return True
        objects_out = self.client.list_objects_type2(
            self.bucket_name, prefix=self.bucket_path, delimiter="/"
        )
        if (
            len(objects_out.contents) == 1
            and objects_out.contents[0].key == self.bucket_path
        ):
            return False
        return True

    def _directory_list(self, path: str) -> List[str]:
        # TODO cache list_objects_type2 result to avoid too many requests
        objects_out = self.client.list_objects_type2(
            self.bucket_name, prefix=path, delimiter="/"
        )
        return [obj.key for obj in objects_out.contents]

    def _support_range_download(self) -> bool:
        return True

    def download(
        self,
        local_path: Path,
        bucket_path: str,
        bucket_name: Optional[str] = None,
        enable_range: bool = True,
    ):
        try:
            meta_data = self.client.head_object(bucket=bucket_name, key=bucket_path)
        except Exception as e:
            raise ValueError(f"TOS bucket path {bucket_path} not exist for {e}.")

        _file_name = bucket_path.split("/")[-1]
        local_file = local_path.joinpath(_file_name).absolute()

        # check if file exist
        etag = meta_data.etag
        file_size = meta_data.content_length
        meta_data_file = meta_file(
            local_path=local_path, file_name=_file_name, source=self._source.value
        )

        if not need_to_download(local_file, meta_data_file, file_size, etag):
            return
        num_threads = (
            self.download_extra_config.num_threads or envs.DOWNLOADER_NUM_THREADS
        )
        task_num = num_threads if enable_range else 1

        download_kwargs = {"part_size": self.download_extra_config.part_chunksize}

        # download file
        total_length = meta_data.content_length

        with tqdm(
            desc=_file_name, total=total_length, unit="b", unit_scale=True
        ) if self.enable_progress_bar else nullcontext() as pbar:

            def download_progress(
                consumed_bytes, total_bytes, rw_once_bytes, type: DataTransferType
            ):
                pbar.update(rw_once_bytes)

            download_file = get_local_download_paths(
                local_path, _file_name, self._source
            )
            with download_file.download_lock():
                self.client.download_file(
                    bucket=bucket_name,
                    key=bucket_path,
                    file_path=str(
                        local_file
                    ),  # TOS client does not support Path, convert it to str
                    task_num=task_num,
                    data_transfer_listener=download_progress
                    if self.enable_progress_bar
                    else None,
                    **download_kwargs,
                )
                save_meta_data(meta_data_file, etag)


class TOSDownloaderV2(S3BaseDownloader):
    _source: ClassVar[RemoteSource] = RemoteSource.TOS

    def __init__(
        self,
        model_uri,
        model_name: Optional[str] = None,
        download_extra_config: DownloadExtraConfig = DEFAULT_DOWNLOADER_EXTRA_CONFIG,
        enable_progress_bar: bool = False,
    ):
        super().__init__(
            scheme="tos",
            model_uri=model_uri,
            model_name=model_name,
            download_extra_config=download_extra_config,
            enable_progress_bar=enable_progress_bar,
        )  # type: ignore

    def _get_auth_config(self) -> Dict[str, Optional[str]]:
        return {
            "region_name": self.download_extra_config.region
            or envs.DOWNLOADER_TOS_REGION
            or "",
            "endpoint_url": self.download_extra_config.endpoint
            or envs.DOWNLOADER_TOS_ENDPOINT
            or "",
            "aws_access_key_id": self.download_extra_config.ak
            or envs.DOWNLOADER_TOS_ACCESS_KEY
            or "",
            "aws_secret_access_key": self.download_extra_config.sk
            or envs.DOWNLOADER_TOS_SECRET_KEY
            or "",
        }
