"""Local integration checks for the optional TeX renderer; no network access."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(
    (shutil.which("pdflatex") or Path("/Library/TeX/texbin/pdflatex").exists())
    and shutil.which("pdftocairo")
    and shutil.which("pdfinfo"),
    "local TeX and Poppler are required",
)
class LatexRendererTests(unittest.TestCase):
    def render(self, source, work):
        path = Path(work)
        (path / "input.txt").write_text(source)
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("render_codex_y_latex.py")),
                str(path / "input.txt"),
                str(path / "output.png"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
        )

    def test_real_tex_matrix_alignment_and_integral(self):
        for source in (
            r"\begin{bmatrix}a&b\\c&d\end{bmatrix}",
            r"\begin{aligned}f(x)&=x^2+1\\f'(x)&=2x\end{aligned}",
            r"\int_0^1 x^2\,dx=\frac13",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as work:
                self.assertEqual(self.render(source, work).returncode, 0)
                png = (Path(work) / "output.png").read_bytes()
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertLess(len(png), 8 * 1024 * 1024)

    def test_shell_escape_cannot_create_a_file(self):
        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "unexpected"
            self.render(r"\immediate\write18{touch " + str(target) + "}x", work)
            self.assertFalse(target.exists())

    def test_invalid_tex_and_external_input_fail_without_an_image(self):
        for source in (r"\unknowncodexcommand", r"\input{/etc/passwd}"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as work:
                self.assertNotEqual(self.render(source, work).returncode, 0)
                self.assertFalse((Path(work) / "output.png").exists())

    def test_runaway_tex_is_stopped(self):
        with tempfile.TemporaryDirectory() as work:
            start = time.monotonic()
            self.assertNotEqual(self.render(r"\loop\iftrue\repeat", work).returncode, 0)
            self.assertLess(time.monotonic() - start, 8)


if __name__ == "__main__":
    unittest.main()
