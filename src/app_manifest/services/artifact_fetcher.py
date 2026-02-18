"""Скачивание артефактов и создание мини-манифестов.

Helm-чарты: скачивает через helm CLI, извлекает Chart.yaml,
values.schema.json, resource-profiles, считает SHA-256.

Docker-образы: если у компонента есть reference в конфиге,
создаёт минимальный мини-манифест из reference без хеша
(hash неизвестен без скачивания образа).
"""

import base64
import hashlib
import subprocess
import tarfile
import tempfile
from pathlib import Path

import yaml

from app_manifest.models.cyclonedx import (
    CdxAttachment,
    CdxComponent,
    CdxDataContents,
    CdxDataEntry,
    CdxHash,
    CycloneDxBom,
    CdxMetadata,
    CdxMetadataComponent,
    CdxTool,
    CdxToolsWrapper,
    _make_bom_ref,
)
from app_manifest.models.config import BuildConfig, ComponentConfig, MimeType
from app_manifest.models.regdef import RegistryDefinition
from app_manifest.services.purl import make_docker_purl, make_helm_purl, parse_docker_reference

_HELM_TYPES = {MimeType.HELM_CHART, MimeType.Q_HELM_CHART}
_DOCKER_TYPES = {MimeType.DOCKER_IMAGE}


def fetch_components_from_config(
    config: BuildConfig,
    regdef: RegistryDefinition | None = None,
) -> list[tuple[str, CycloneDxBom]]:
    """Обработать все компоненты с reference из конфига.

    - Helm-чарты: скачивает через helm pull, создаёт полный мини-манифест.
    - Docker-образы: создаёт минимальный мини-манифест из reference (без хеша).

    Возвращает список (config_name, bom) для каждого обработанного компонента.
    """
    results = []
    for comp in config.components:
        if not comp.reference:
            continue
        if comp.mime_type in _HELM_TYPES:
            bom = fetch_helm_component(comp.reference, regdef, mime_type=comp.mime_type.value)
            results.append((comp.name, bom))
        elif comp.mime_type in _DOCKER_TYPES:
            bom = fetch_docker_component_from_reference(comp, regdef)
            results.append((comp.name, bom))
    return results


# Обратная совместимость: старое имя функции
fetch_helm_components_from_config = fetch_components_from_config


def fetch_docker_component_from_reference(
    comp_config: ComponentConfig,
    regdef: RegistryDefinition | None = None,
) -> CycloneDxBom:
    """Создать мини-манифест для Docker-образа из reference.

    Хеш не вычисляется (образ не скачивается).
    name, version, group берутся из reference;
    для name используется имя из конфига, чтобы generate мог сопоставить.
    """
    from datetime import datetime, timezone

    _, version, group = parse_docker_reference(comp_config.reference)
    if not group:
        import sys
        print(
            f"WARNING: no group for component '{comp_config.name}' "
            f"(reference '{comp_config.reference}' has no namespace/org)",
            file=sys.stderr,
        )
    purl = make_docker_purl(comp_config.reference, regdef)

    component = CdxComponent(
        bom_ref=_make_bom_ref(comp_config.name),
        type="container",
        mime_type=comp_config.mime_type.value,
        name=comp_config.name,
        version=version,
        group=group or None,
        purl=purl,
    )

    meta = CdxMetadata(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        component=CdxMetadataComponent(
            bom_ref=_make_bom_ref("am-build-cli"),
            name="am-build-cli",
            version="0.1.0",
        ),
        tools=CdxToolsWrapper(
            components=[CdxTool(name="am-build-cli", version="0.1.0")]
        ),
    )

    return CycloneDxBom(
        metadata=meta,
        components=[component],
        dependencies=[],
    )


def fetch_helm_component(
    reference: str,
    regdef: RegistryDefinition | None = None,
    mime_type: str = "application/vnd.nc.helm.chart",
) -> CycloneDxBom:
    """Скачать Helm-чарт и создать CycloneDX мини-манифест.

    reference — OCI URL (например oci://registry.example.com/charts/my-chart:1.0)
    regdef — Registry Definition для PURL (опционально)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Скачиваем чарт
        tgz_path = _helm_pull(reference, tmp_path)

        # 2. Считаем хеш архива
        chart_hash = _compute_sha256(tgz_path)

        # 3. Извлекаем архив
        extract_dir = tmp_path / "extracted"
        _extract_chart(tgz_path, extract_dir)

        # 4. Находим корневую директорию чарта
        chart_dir = _find_chart_dir(extract_dir)

        # 5. Читаем Chart.yaml
        chart_yaml = _read_chart_yaml(chart_dir)

        # 6. Собираем данные
        name = chart_yaml.get("name", "unknown")
        version = chart_yaml.get("version", "")
        app_version = chart_yaml.get("appVersion", version)

        # 7. PURL
        purl = make_helm_purl(reference, regdef) if reference else None

        # 8. Вложенные компоненты (values.schema.json, resource-profiles)
        nested = _extract_nested_components(chart_dir)

        # 9. Собираем компонент
        component = CdxComponent(
            bom_ref=_make_bom_ref(name),
            type="application",
            mime_type=mime_type,
            name=name,
            version=app_version or version,
            purl=purl,
            hashes=[CdxHash(alg="SHA-256", content=chart_hash)],
            components=nested,
        )

        # 10. Собираем мини-манифест
        from datetime import datetime, timezone

        meta = CdxMetadata(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            component=CdxMetadataComponent(
                bom_ref=_make_bom_ref("am-build-cli"),
                name="am-build-cli",
                version="0.1.0",
            ),
            tools=CdxToolsWrapper(
                components=[CdxTool(name="am-build-cli", version="0.1.0")]
            ),
        )

        return CycloneDxBom(
            metadata=meta,
            components=[component],
            dependencies=[],
        )


def _helm_pull(reference: str, dest: Path) -> Path:
    """Скачать Helm-чарт через helm CLI."""
    try:
        result = subprocess.run(
            ["helm", "pull", reference, "--destination", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "helm CLI not found. Install helm: https://helm.sh/docs/intro/install/"
        )

    if result.returncode != 0:
        raise RuntimeError(f"helm pull failed: {result.stderr.strip()}")

    # Находим скачанный .tgz файл
    tgz_files = sorted(dest.glob("*.tgz"))
    if not tgz_files:
        raise RuntimeError(f"No .tgz file found after helm pull in {dest}")
    if len(tgz_files) > 1:
        import warnings
        warnings.warn(
            f"Multiple .tgz files found after helm pull, using first: "
            + ", ".join(f.name for f in tgz_files),
            stacklevel=2,
        )

    return tgz_files[0]


def _compute_sha256(path: Path) -> str:
    """Вычислить SHA-256 хеш файла."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _extract_chart(tgz_path: Path, dest: Path) -> None:
    """Извлечь .tgz архив."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz_path, "r:gz") as tar:
        tar.extractall(dest, filter="data")


def _find_chart_dir(extract_dir: Path) -> Path:
    """Найти корневую директорию чарта в извлечённом архиве."""
    # Helm-чарты обычно имеют структуру: chart-name/Chart.yaml
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "Chart.yaml").exists():
            return child

    # Может быть что Chart.yaml прямо в extract_dir
    if (extract_dir / "Chart.yaml").exists():
        return extract_dir

    raise RuntimeError(f"Chart.yaml not found in extracted chart at {extract_dir}")


def _read_chart_yaml(chart_dir: Path) -> dict:
    """Прочитать Chart.yaml."""
    chart_file = chart_dir / "Chart.yaml"
    with open(chart_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_nested_components(chart_dir: Path) -> list[CdxComponent]:
    """Извлечь вложенные компоненты: values.schema.json и resource-profiles."""
    result: list[CdxComponent] = []

    # values.schema.json
    schema_file = chart_dir / "values.schema.json"
    if schema_file.exists():
        content_b64 = base64.b64encode(
            schema_file.read_bytes()
        ).decode("ascii")

        result.append(CdxComponent(
            bom_ref=_make_bom_ref("values.schema.json"),
            type="data",
            mime_type="application/vnd.nc.helm.values.schema",
            name="values.schema.json",
            data=[CdxDataEntry(
                type="configuration",
                name="values.schema.json",
                contents=CdxDataContents(
                    attachment=CdxAttachment(
                        content_type="application/json",
                        encoding="base64",
                        content=content_b64,
                    )
                ),
            )],
        ))

    # resource-profiles
    profiles_dir = chart_dir / "resource-profiles"
    if profiles_dir.exists() and profiles_dir.is_dir():
        data_entries: list[CdxDataEntry] = []
        for profile_file in sorted(profiles_dir.glob("*.yaml")):
            content_b64 = base64.b64encode(
                profile_file.read_bytes()
            ).decode("ascii")
            data_entries.append(CdxDataEntry(
                type="configuration",
                name=profile_file.name,
                contents=CdxDataContents(
                    attachment=CdxAttachment(
                        content_type="application/yaml",
                        encoding="base64",
                        content=content_b64,
                    )
                ),
            ))

        if data_entries:
            result.append(CdxComponent(
                bom_ref=_make_bom_ref("resource-profile-baselines"),
                type="data",
                mime_type="application/vnd.nc.resource-profile-baseline",
                name="resource-profile-baselines",
                data=data_entries,
            ))

    return result
