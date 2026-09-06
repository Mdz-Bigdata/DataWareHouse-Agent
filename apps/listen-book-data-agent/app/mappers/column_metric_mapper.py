from dataclasses import asdict

from app.entities.column_metric import ColumnMetric
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL


class ColumnMetricMapper:
    @staticmethod
    def to_entity(column_metric_mysql: ColumnMetricMySQL):
        return ColumnMetric(
            column_id=column_metric_mysql.column_id,
            metric_id=column_metric_mysql.metric_id,
            build_id=column_metric_mysql.build_id,
        )

    @staticmethod
    def to_model(column_metric: ColumnMetric):
        return ColumnMetricMySQL(**asdict(column_metric))
