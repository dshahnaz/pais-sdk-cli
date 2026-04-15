"""Data sources resource."""

from __future__ import annotations

from pais.models.data_source import DataSource, DataSourceCreate
from pais.resources._base import Resource


class DataSourcesResource(Resource[DataSource]):
    path = "/control/data-sources"
    model = DataSource

    def create(self, payload: DataSourceCreate) -> DataSource:
        return self._create(payload)
