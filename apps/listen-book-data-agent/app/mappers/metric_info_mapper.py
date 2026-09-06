from dataclasses import asdict

from app.entities.metric_info import MetricInfo
from app.models.mysql.metric_info_mysql import MetricInfoMySQL


class MetricInfoMapper:
    @staticmethod
    def to_entity(model: MetricInfoMySQL) -> MetricInfo:
        return MetricInfo(
            id=model.id,
            name=model.name,
            description=model.description,
            relevant_columns=model.relevant_columns,
            alias=model.alias,
            formula=model.formula,
            filters=model.filters or [],
            time_column=model.time_column,
            unit=model.unit,
            currency_column=model.currency_column,
            dimensions=model.dimensions or [],
            snapshot=model.snapshot,
            build_id=model.build_id,
        )

    @staticmethod
    def to_model(entity: MetricInfo):
        return MetricInfoMySQL(**asdict(entity))
