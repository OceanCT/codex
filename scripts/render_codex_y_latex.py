#!/usr/bin/env python3
"""Render a bounded math fragment with local LaTeX; never download packages."""

import argparse
import os
from pathlib import Path
import resource
import re
import shutil
import subprocess
import tempfile


def child_limits():
    os.nice(10)
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--color", default="D8DEE9")
    parser.add_argument("--background", default="121A1D")
    args = parser.parse_args()
    source = args.source.read_text()
    if len(source.encode()) > 32768:
        raise SystemExit("Formula exceeds 32 KiB")
    if any(
        len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value)
        for value in (args.color, args.background)
    ):
        raise SystemExit("Invalid foreground color")
    latex = shutil.which("pdflatex")
    if latex is None and Path("/Library/TeX/texbin/pdflatex").is_file():
        latex = "/Library/TeX/texbin/pdflatex"
    raster = shutil.which("pdftocairo")
    pdfinfo = shutil.which("pdfinfo")
    if latex is None or raster is None or pdfinfo is None:
        raise SystemExit("LaTeX rendering needs pdflatex, pdftocairo and pdfinfo")
    with tempfile.TemporaryDirectory(prefix="codex-y-tex-") as tmp:
        work = Path(tmp)
        document = (
            r"\documentclass[border=3pt]{standalone}"
            "\n"
            r"\usepackage{amsmath,amssymb,mathtools,bm,xcolor}"
            "\n"
            r"\definecolor{ink}{HTML}{" + args.color + "}\n"
            r"\begin{document}\fontsize{14}{18}\selectfont\color{ink}$\displaystyle "
            + source
            + r"$\end{document}"
            "\n"
        )
        (work / "formula.tex").write_text(document)
        env = dict(os.environ, openin_any="p", openout_any="p", shell_escape="f")
        # No shell escape or arbitrary absolute/parent file access from generated TeX.
        subprocess.run(
            [
                latex,
                "-no-shell-escape",
                "-halt-on-error",
                "-interaction=batchmode",
                "formula.tex",
            ],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
            preexec_fn=child_limits,
        )
        info = subprocess.check_output(
            [pdfinfo, "formula.pdf"], cwd=work, timeout=3, text=True
        )
        size = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
        if size is None:
            raise SystemExit("Cannot determine formula page dimensions")
        pixels = max(1, min(1600, round(max(map(float, size.groups())) * 160 / 72)))
        subprocess.run(
            [
                raster,
                "-f",
                "1",
                "-singlefile",
                "-scale-to",
                str(pixels),
                "-png",
                "-transp",
                "formula.pdf",
                "formula",
            ],
            cwd=work,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
            preexec_fn=child_limits,
        )
        # Keep PNG conversion in the Rust image pipeline; it also applies size limits.
        shutil.copyfile(work / "formula.png", args.output)


if __name__ == "__main__":
    main()
