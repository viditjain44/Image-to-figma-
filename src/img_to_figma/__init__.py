"""img-to-figma: turn an ad creative or screenshot into editable Figma layers."""
import logging
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Turn an image into editable Figma layers (fully local).")


@app.command()
def convert(
    image: Path = typer.Argument(..., exists=True, dir_okay=False, help="PNG / JPG / WEBP / screenshot"),
    no_cutout: bool = typer.Option(False, help="Keep the main subject in the background image"),
    no_llm: bool = typer.Option(False, help="Label roles with simple rules instead of Qwen3-VL"),
    scale: float = typer.Option(1.0, help="2 for retina screenshots"),
    sans: str = typer.Option(None, help="Figma font family for sans-serif text"),
    serif: str = typer.Option(None, help="Figma font family for serif text"),
):
    """Convert one image; prints the output folder."""
    from . import config, pipeline

    opts = pipeline.Options(cutout_subject=not no_cutout, use_llm=not no_llm, scale=scale)
    if sans:
        opts.sans_font = sans
    if serif:
        opts.serif_font = serif
    doc = pipeline.run(image.resolve(), opts, progress=lambda m: typer.echo(f"  {m}"))
    typer.echo(f"\n{config.OUTPUT_DIR / doc.job_id}")


@app.command()
def serve():
    """Run the API for the web UI and the Figma plugin."""
    import uvicorn

    from . import config

    uvicorn.run("img_to_figma.server:app", host=config.API_HOST, port=config.API_PORT)


@app.command()
def setup():
    """Download the models once (LaMa ~200 MB, rembg ~180 MB)."""
    from .engines import lama
    from .stages import segment

    lama.download()
    segment._session()
    typer.echo("Models ready.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app()
