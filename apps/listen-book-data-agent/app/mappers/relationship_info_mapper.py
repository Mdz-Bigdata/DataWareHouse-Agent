from dataclasses import asdict

from app.entities.relationship_info import RelationshipInfo
from app.models.mysql.relationship_info_mysql import RelationshipInfoMySQL


class RelationshipInfoMapper:
    @staticmethod
    def to_entity(model: RelationshipInfoMySQL) -> RelationshipInfo:
        return RelationshipInfo(
            id=model.id,
            source_table=model.source_table,
            source_column=model.source_column,
            target_table=model.target_table,
            target_column=model.target_column,
            relationship_type=model.relationship_type,
            condition=model.condition,
            physical=model.physical,
            build_id=model.build_id,
        )

    @staticmethod
    def to_model(entity: RelationshipInfo) -> RelationshipInfoMySQL:
        return RelationshipInfoMySQL(**asdict(entity))
