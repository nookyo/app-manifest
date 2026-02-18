"""Модели для YAML build-конфига.

Этот файл описывает структуру YAML-файла, который пользователь
передаёт через --config. Pydantic автоматически проверяет что
все обязательные поля на месте и типы правильные.
"""

from enum import Enum

from pydantic import BaseModel, Field


class MimeType(str, Enum):
    """Допустимые типы компонентов.

    str + Enum — значит каждый элемент это строка,
    но только из разрешённого списка. Если в YAML написать
    mimeType: "что-то-левое" — Pydantic выдаст ошибку.
    """

    # nc-вариант (из спецификации)
    STANDALONE_RUNNABLE = "application/vnd.nc.standalone-runnable"
    DOCKER_IMAGE = "application/vnd.docker.image"
    HELM_CHART = "application/vnd.nc.helm.chart"
    SMARTPLUG = "application/vnd.nc.smartplug"
    SAMPLEREPO = "application/vnd.nc.samplerepo"
    CDN = "application/vnd.nc.cdn"
    CRD = "application/vnd.nc.crd"
    JOB = "application/vnd.nc.job"

    # qubership-вариант (из реальных примеров)
    Q_STANDALONE_RUNNABLE = "application/vnd.qubership.standalone-runnable"
    Q_HELM_CHART = "application/vnd.qubership.helm.chart"
    Q_SMARTPLUG = "application/vnd.qubership.smartplug"
    Q_SAMPLEREPO = "application/vnd.qubership.samplerepo"
    Q_CDN = "application/vnd.qubership.cdn"
    Q_CRD = "application/vnd.qubership.crd"
    Q_JOB = "application/vnd.qubership.job"


class DependencyConfig(BaseModel):
    """Одна зависимость компонента.

    Пример в YAML:
        dependsOn:
          - name: jaeger
            mimeType: application/vnd.docker.image
            valuesPathPrefix: images.jaeger
    """

    name: str
    mime_type: MimeType = Field(alias="mimeType")
    values_path_prefix: str | None = Field(default=None, alias="valuesPathPrefix")

    # populate_by_name=True — разрешает использовать и Python-имя (mime_type),
    # и YAML-имя (mimeType). Нужно для удобства в тестах.
    model_config = {"populate_by_name": True}


class ComponentConfig(BaseModel):
    """Один компонент в конфиге.

    Пример в YAML:
        components:
          - name: qubership-jaeger
            mimeType: application/vnd.nc.helm.chart
            reference: oci://registry/repo/chart:1.0
            dependsOn:
              - name: jaeger
                mimeType: application/vnd.docker.image
    """

    name: str
    mime_type: MimeType = Field(alias="mimeType")
    reference: str | None = None
    depends_on: list[DependencyConfig] = Field(default_factory=list, alias="dependsOn")

    model_config = {"populate_by_name": True}


class BuildConfig(BaseModel):
    """Корневая модель YAML build-конфига.

    Пример YAML-файла целиком:
        applicationVersion: "1.2.3"
        applicationName: "my-app"
        components:
          - name: my-service
            mimeType: application/vnd.docker.image
    """

    application_version: str = Field(alias="applicationVersion")
    application_name: str = Field(alias="applicationName")
    components: list[ComponentConfig]

    model_config = {"populate_by_name": True}
