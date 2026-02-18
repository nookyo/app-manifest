"""Модели для JSON-метаданных компонентов.

Это файлы, которые генерируются в CI при сборке Docker-образов
и Helm-чартов. Они передаются в CLI как позиционные аргументы.

Пример для Docker-образа:
{
    "name": "jaeger",
    "type": "container",
    "mime-type": "application/vnd.docker.image",
    "group": "core",
    "version": "build3",
    "hashes": [{"alg": "SHA-256", "content": "abc123..."}],
    "reference": "sandbox.example.com/core/jaeger:build3"
}

Пример для Helm-чарта:
{
    "name": "kafka",
    "type": "application",
    "mime-type": "application/vnd.qubership.helm.chart",
    "appVersion": "2.1.0-dev-SNAPSHOT",
    "hashes": [...],
    "reference": "oci://registry.qubership.org/.../kafka:2.1.0",
    "components": [
        {
            "type": "data",
            "mime-type": "application/vnd.nc.resource-profile-baseline",
            "name": "resource-profile-baselines",
            "data": [{"type": "configuration", "name": "small.yaml", "contents": {...}}]
        }
    ]
}
"""

from typing import Any

from pydantic import BaseModel, Field


class HashEntry(BaseModel):
    """Хеш артефакта (для проверки целостности)."""

    alg: str
    content: str


class MetadataAttachment(BaseModel):
    """Вложение с base64-содержимым из CI метаданных."""

    content_type: str = Field(alias="contentType")
    encoding: str = "base64"
    content: str

    model_config = {"populate_by_name": True}


class MetadataDataContents(BaseModel):
    """Обёртка для attachment."""

    attachment: MetadataAttachment


class MetadataDataEntry(BaseModel):
    """Одна запись данных (например small.yaml)."""

    type: str = "configuration"
    name: str
    contents: MetadataDataContents


class MetadataNestedComponent(BaseModel):
    """Вложенный компонент из CI метаданных Helm-чарта.

    Например: values.schema.json или resource-profile-baselines.
    Эти данные уже подготовлены CI в нужном формате и
    передаются в выходной манифест почти без изменений.
    """

    type: str
    mime_type: str = Field(alias="mime-type")
    name: str
    data: list[MetadataDataEntry] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ComponentMetadata(BaseModel):
    """Метаданные одного компонента из CI.

    name — имя компонента (должно совпадать с именем в YAML-конфиге)
    type — "container" для Docker, "application" для Helm
    mime_type — тип компонента
    group — группа/namespace (например "core") — необязательно
    version — версия/тег — необязательно
    app_version — версия приложения (для Helm-чартов) — необязательно
    hashes — список хешей артефакта
    reference — полная ссылка на артефакт
    components — вложенные компоненты (values.schema.json, resource-profiles)
    """

    name: str
    type: str
    mime_type: str = Field(alias="mime-type")
    group: str | None = None
    version: str | None = None
    app_version: str | None = Field(default=None, alias="appVersion")
    hashes: list[HashEntry] = Field(default_factory=list)
    reference: str | None = None
    components: list[MetadataNestedComponent] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
