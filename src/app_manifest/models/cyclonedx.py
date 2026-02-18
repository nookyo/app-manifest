"""Модели выходного CycloneDX 1.6 JSON.

Эти модели описывают структуру итогового Application Manifest.
При вызове .model_dump(by_alias=True) они превращаются в JSON
с правильными именами полей (bom-ref, $schema, bomFormat и т.д.).

Пример итогового JSON (верхний уровень):
{
    "$schema": "...",
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "serialNumber": "urn:uuid:...",
    "version": 1,
    "metadata": { ... },
    "components": [ ... ],
    "dependencies": [ ... ]
}
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field


def _make_bom_ref(name: str) -> str:
    """Сгенерировать bom-ref в формате name:uuid.

    Из примеров видно что bom-ref всегда в формате:
    "qubership-jaeger:61439aff-c00d-43f5-9bae-fe6db05db2d5"
    """
    return f"{name}:{uuid.uuid4()}"


# ─── Хеши ────────────────────────────────────────────────

class CdxHash(BaseModel):
    """Хеш артефакта. Пример: {"alg": "SHA-256", "content": "a1b2c3..."}"""

    alg: str
    content: str


# ─── Свойства ─────────────────────────────────────────────

class CdxProperty(BaseModel):
    """Свойство компонента. Пример: {"name": "isLibrary", "value": false}

    value — может быть строкой, числом, boolean или объектом,
    поэтому тип Any.
    """

    name: str
    value: Any


# ─── Вложенные данные (values.schema.json, resource-profiles) ──

class CdxAttachment(BaseModel):
    """Вложение с закодированным содержимым."""

    content_type: str = Field(
        validation_alias="contentType",
        serialization_alias="contentType",
    )
    encoding: str = "base64"
    content: str

    model_config = {"populate_by_name": True}


class CdxDataContents(BaseModel):
    """Обёртка для вложения."""

    attachment: CdxAttachment


class CdxDataEntry(BaseModel):
    """Запись данных (например values.schema.json).

    Пример:
    {
        "type": "configuration",
        "name": "values.schema.json",
        "contents": { "attachment": { ... } }
    }
    """

    type: str = "configuration"
    name: str
    contents: CdxDataContents


# ─── Компонент ────────────────────────────────────────────

class CdxComponent(BaseModel):
    """Один компонент в манифесте.

    Может быть: standalone-runnable, docker image, helm chart,
    values.schema.json, resource-profile и т.д.

    bom-ref — уникальный идентификатор в формате "name:uuid"
    type — "application", "container" или "data"
    mime-type — конкретный тип (например "application/vnd.docker.image")
    """

    bom_ref: str = Field(
        validation_alias="bom-ref",
        serialization_alias="bom-ref",
    )
    type: str
    mime_type: str = Field(
        validation_alias="mime-type",
        serialization_alias="mime-type",
    )
    name: str
    version: str | None = None
    group: str | None = None
    purl: str | None = None
    properties: list[CdxProperty] | None = None
    hashes: list[CdxHash] | None = None
    components: list["CdxComponent"] | None = None
    data: list[CdxDataEntry] | None = None

    model_config = {"populate_by_name": True}


# ─── Зависимости ─────────────────────────────────────────

class CdxDependency(BaseModel):
    """Связь зависимости между компонентами.

    ref — bom-ref компонента, который зависит
    dependsOn — список bom-ref компонентов, от которых зависит

    Пример:
    {
        "ref": "qubership-jaeger:aaa-bbb",
        "dependsOn": ["docker-jaeger:ccc-ddd", "chart-jaeger:eee-fff"]
    }
    """

    ref: str
    depends_on: list[str] = Field(
        default_factory=list,
        validation_alias="dependsOn",
        serialization_alias="dependsOn",
    )

    model_config = {"populate_by_name": True}


# ─── Metadata ─────────────────────────────────────────────

class CdxTool(BaseModel):
    """Инструмент, сгенерировавший манифест."""

    type: str = "application"
    name: str
    version: str


class CdxToolsWrapper(BaseModel):
    """Обёртка для списка инструментов.

    В JSON: "tools": {"components": [{"name": "am-build-cli", ...}]}
    """

    components: list[CdxTool]


class CdxMetadataComponent(BaseModel):
    """Компонент в секции metadata — описание самого приложения."""

    bom_ref: str = Field(
        validation_alias="bom-ref",
        serialization_alias="bom-ref",
    )
    type: str = "application"
    mime_type: str = Field(
        default="application/vnd.nc.application",
        validation_alias="mime-type",
        serialization_alias="mime-type",
    )
    name: str
    version: str

    model_config = {"populate_by_name": True}


class CdxMetadata(BaseModel):
    """Секция metadata манифеста.

    timestamp — когда сгенерирован (ISO 8601)
    component — описание приложения
    tools — чем сгенерирован
    """

    timestamp: str
    component: CdxMetadataComponent
    tools: CdxToolsWrapper


# ─── Корневой BOM ─────────────────────────────────────────

class CycloneDxBom(BaseModel):
    """Корневая модель Application Manifest (CycloneDX 1.6 BOM).

    Это то, что в итоге сериализуется в JSON-файл.
    """

    serial_number: str = Field(
        default_factory=lambda: f"urn:uuid:{uuid.uuid4()}",
        validation_alias="serialNumber",
        serialization_alias="serialNumber",
    )
    schema_url: str = Field(
        default="../schemas/application-manifest.schema.json",
        validation_alias="$schema",
        serialization_alias="$schema",
    )
    bom_format: str = Field(
        default="CycloneDX",
        validation_alias="bomFormat",
        serialization_alias="bomFormat",
    )
    spec_version: str = Field(
        default="1.6",
        validation_alias="specVersion",
        serialization_alias="specVersion",
    )
    version: int = 1
    metadata: CdxMetadata
    components: list[CdxComponent] = Field(default_factory=list)
    dependencies: list[CdxDependency] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
