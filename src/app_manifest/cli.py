import json
import sys
from importlib.metadata import version, PackageNotFoundError
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
from app_manifest.services.dd_converter import convert_dd_to_amv2, convert_amv2_to_dd
from app_manifest.models.dd import DeploymentDescriptor
from app_manifest.models.cyclonedx import CycloneDxBom


class AliasedGroup(click.Group):
    _aliases = {"c": "component", "gen": "generate", "f": "fetch", "v": "validate", "cv": "convert", "i": "info"}

    def get_command(self, ctx, cmd_name):
        return super().get_command(ctx, self._aliases.get(cmd_name, cmd_name))


try:
    _version = version("app-manifest-cli")
except PackageNotFoundError:
    _version = "unknown"


@click.group(cls=AliasedGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=_version, prog_name="am")
def cli():
    """Application Manifest v2 Generator."""
    pass


@cli.command("info")
def info():
    """Show version and information about the tool."""
    click.echo(f"app-manifest-cli  {_version}")
    click.echo(f"Python            {sys.version.split()[0]}")
    click.echo()
    click.echo("Commands:")
    click.echo("  component  (c)   Generate a CycloneDX mini-manifest for a single component")
    click.echo("  fetch      (f)   Fetch Helm charts and generate mini-manifests")
    click.echo("  generate   (gen) Generate an Application Manifest v2 JSON from mini-manifests")
    click.echo("  validate   (v)   Validate a manifest against the JSON Schema")
    click.echo("  convert    (cv)  Convert between DD and AMv2")
    click.echo("  info       (i)   Show this info")
    click.echo()
    click.echo("Docs: https://github.com/netcracker/app-manifest")


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
                suffix = mime.split("/")[1].replace(".", "_") if "/" in mime else "unknown"
                out_file = out_dir / f"{name}_{suffix}.json"
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


@cli.command("convert")
@click.option("--input", "-i", "input_file", required=True, type=click.Path(exists=True, path_type=Path), help="Input file: DD JSON or AMv2 JSON")
@click.option("--out", "-o", required=True, type=click.Path(path_type=Path), help="Output file path")
@click.option("--to-dd", "direction", flag_value="to-dd", help="Convert AMv2 → DD")
@click.option("--to-am", "direction", flag_value="to-am", help="Convert DD → AMv2")
@click.option("--registry-def", "-r", "registry_def", required=True, type=click.Path(exists=True, path_type=Path), help="Registry Definition YAML file")
@click.option("--config", "-c", "config_file", default=None, type=click.Path(exists=True, path_type=Path), help="Build Config YAML (required for DD → AMv2)")
@click.option("--zip", "-z", "zip_file", default=None, type=click.Path(path_type=Path), help="Application ZIP (optional, for values.schema.json and resource-profiles)")
@click.option("--name", "-n", "app_name", default=None, help="Override application name")
@click.option("--version", "-v", "app_version", default=None, help="Override application version")
def convert(input_file, out, direction, registry_def, config_file, zip_file, app_name, app_version):
    """Convert between Deployment Descriptor (DD) and Application Manifest v2 (AMv2).

    Use --to-dd to convert AMv2 → DD, or --to-am to convert DD → AMv2.
    """
    if not direction:
        raise click.UsageError("Specify conversion direction: --to-dd or --to-am")

    regdef = _load_regdef(registry_def)

    try:
        if direction == "to-am":
            # DD → AMv2
            if not config_file:
                raise click.UsageError("--config is required for DD → AMv2 conversion")

            build_config = _load_config(config_file)

            try:
                raw = json.loads(input_file.read_text(encoding="utf-8"))
                dd = DeploymentDescriptor.model_validate(raw)
            except json.JSONDecodeError as e:
                raise click.ClickException(f"Invalid JSON in {input_file}: {e}")
            except ValidationError as e:
                errors = "; ".join(
                    f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                raise click.ClickException(f"DD validation error in {input_file}: {errors}")

            name = app_name or build_config.application_name
            version = app_version or build_config.application_version

            bom, warnings = convert_dd_to_amv2(
                dd=dd,
                config=build_config,
                regdef=regdef,
                app_name=name,
                app_version=version,
                zip_path=zip_file,
            )

            for w in warnings:
                click.echo(w, err=True)

            _write_output(bom, out)
            click.echo(f"AMv2 written to {out}")

        else:
            # AMv2 → DD
            try:
                raw = json.loads(input_file.read_text(encoding="utf-8"))
                bom = CycloneDxBom.model_validate(raw)
            except json.JSONDecodeError as e:
                raise click.ClickException(f"Invalid JSON in {input_file}: {e}")
            except ValidationError as e:
                errors = "; ".join(
                    f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                raise click.ClickException(f"AMv2 validation error in {input_file}: {errors}")

            dd, warnings = convert_amv2_to_dd(bom=bom, regdef=regdef)

            for w in warnings:
                click.echo(w, err=True)

            dd_dict = dd.model_dump(by_alias=True, exclude_none=False)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(dd_dict, f, indent=2, ensure_ascii=False)

            click.echo(f"DD written to {out}")

    except click.ClickException:
        raise
    except click.UsageError:
        raise
    except Exception as e:
        raise click.ClickException(str(e))


def _write_output(bom, out_path: Path):
    bom_dict = bom.model_dump(by_alias=True, exclude_none=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bom_dict, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    cli()
