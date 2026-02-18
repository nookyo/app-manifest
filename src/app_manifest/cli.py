import json
from pathlib import Path

import click
import yaml
from pydantic import ValidationError

from app_manifest.services.component_builder import build_component_manifest
from app_manifest.services.config_loader import load_build_config
from app_manifest.services.artifact_fetcher import fetch_components_from_config
from app_manifest.services.manifest_builder import build_manifest
from app_manifest.services.metadata_loader import load_all_mini_manifests, load_component_metadata
from app_manifest.services.regdef_loader import load_registry_definition
from app_manifest.services.validator import validate_manifest


class AliasedGroup(click.Group):
    _aliases = {"c": "component", "gen": "generate", "f": "fetch", "v": "validate"}

    def get_command(self, ctx, cmd_name):
        return super().get_command(ctx, self._aliases.get(cmd_name, cmd_name))


@click.group(cls=AliasedGroup)
def cli():
    """Application Manifest v2 Generator."""
    pass


@cli.command()
@click.option("--config", "-c", required=True, type=click.Path(exists=True, path_type=Path), help="Path to YAML build config file")
@click.option("--out", "-o", required=True, type=click.Path(path_type=Path), help="Output JSON file path")
@click.option("--version", "-v", "app_version", default=None, help="Override application version")
@click.option("--name", "-n", "app_name", default=None, help="Override application name")
@click.option("--validate", is_flag=True, default=False, help="Validate output against JSON Schema")
@click.argument("component_files", nargs=-1, type=click.Path(exists=True, path_type=Path, file_okay=True, dir_okay=True))
def generate(config, out, app_version, app_name, validate, component_files):
    """Generate an Application Manifest v2 JSON file from mini-manifests."""
    try:
        build_config = _load_config(config)
        mini_manifests = _load_mini_manifests(component_files)

        bom, warnings = build_manifest(
            config=build_config,
            mini_manifests=mini_manifests,
            version_override=app_version,
            name_override=app_name,
        )

        for w in warnings:
            click.echo(w, err=True)

        _write_output(bom, out)
        click.echo(f"Manifest written to {out}")

        if validate:
            bom_dict = json.loads(Path(out).read_text(encoding="utf-8"))
            errors = validate_manifest(bom_dict)
            if errors:
                click.echo("Validation FAILED:", err=True)
                for error in errors:
                    click.echo(f"  - {error}", err=True)
                raise click.ClickException("Manifest does not conform to JSON Schema")
            click.echo("Manifest is valid.")

    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command("component")
@click.option("--input", "-i", "input_file", required=True, type=click.Path(exists=True, path_type=Path), help="CI metadata JSON file")
@click.option("--out", "-o", required=True, type=click.Path(path_type=Path), help="Output CycloneDX mini-manifest JSON file")
@click.option("--registry-def", "-r", "registry_def", default=None, type=click.Path(exists=True, path_type=Path), help="Registry Definition YAML file")
def component(input_file, out, registry_def):
    """Generate a CycloneDX mini-manifest for a single component."""
    try:
        meta = _load_single_metadata(input_file)
        regdef = _load_regdef(registry_def)

        bom = build_component_manifest(meta, regdef)

        _write_output(bom, out)
        click.echo(f"Component manifest written to {out}")

    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command("validate")
@click.option("--input", "-i", "input_file", required=True, type=click.Path(exists=True, path_type=Path), help="Application Manifest JSON file to validate")
def validate(input_file):
    """Validate an Application Manifest JSON file against the JSON Schema."""
    try:
        data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in {input_file}: {e}")

    errors = validate_manifest(data)
    if errors:
        click.echo(f"Validation FAILED: {input_file}", err=True)
        for error in errors:
            click.echo(f"  - {error}", err=True)
        raise click.ClickException("Manifest does not conform to JSON Schema")

    click.echo(f"Manifest is valid: {input_file}")


@cli.command("fetch")
@click.option("--config", "-c", required=True, type=click.Path(exists=True, path_type=Path), help="Path to YAML build config file")
@click.option("--out", "-o", required=True, type=click.Path(path_type=Path), help="Output directory for mini-manifest JSON files")
@click.option("--registry-def", "-r", "registry_def", default=None, type=click.Path(exists=True, path_type=Path), help="Registry Definition YAML file")
def fetch(config, out, registry_def):
    """Fetch Helm charts from config and generate CycloneDX mini-manifests."""
    try:
        build_config = _load_config(config)
        regdef = _load_regdef(registry_def)

        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)

        results = fetch_components_from_config(build_config, regdef)

        if not results:
            click.echo("No components with reference found in config.")
            return

        name_counts: dict[str, int] = {}
        for name, _ in results:
            name_counts[name] = name_counts.get(name, 0) + 1

        for name, bom in results:
            if name_counts[name] > 1:
                mime = bom.components[0].mime_type if bom.components else ""
                vendor = mime.split("/")[1].split(".")[1] if "/" in mime else "unknown"
                out_file = out_dir / f"{name}_{vendor}.json"
                click.echo(
                    f"WARNING: duplicate component name '{name}' — "
                    f"using filename '{out_file.name}' to avoid collision",
                    err=True,
                )
            else:
                out_file = out_dir / f"{name}.json"
            _write_output(bom, out_file)
            click.echo(f"Component manifest written to {out_file}")

    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))


def _load_single_metadata(path: Path):
    try:
        return load_component_metadata(path)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in metadata file {path}: {e}")
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise click.ClickException(f"Metadata validation error in {path}: {errors}")


def _load_config(config_path: Path):
    try:
        return load_build_config(config_path)
    except yaml.YAMLError as e:
        raise click.ClickException(f"Invalid YAML in config file {config_path}: {e}")
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise click.ClickException(f"Config validation error in {config_path}: {errors}")


def _load_mini_manifests(component_files: tuple[Path, ...]) -> dict:
    if not component_files:
        return {}
    try:
        return load_all_mini_manifests(list(component_files))
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in component file: {e}")
    except (ValidationError, ValueError) as e:
        raise click.ClickException(f"Component manifest error: {e}")


def _load_regdef(registry_def_path: Path | None):
    if not registry_def_path:
        return None
    try:
        return load_registry_definition(registry_def_path)
    except yaml.YAMLError as e:
        raise click.ClickException(
            f"Invalid YAML in registry definition {registry_def_path}: {e}"
        )
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise click.ClickException(
            f"Registry definition validation error in {registry_def_path}: {errors}"
        )


def _write_output(bom, out_path: Path):
    bom_dict = bom.model_dump(by_alias=True, exclude_none=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bom_dict, f, indent=2, ensure_ascii=False)
