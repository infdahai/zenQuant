import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.app.forward_data_collect import main


class ForwardDataCollectCliTests(unittest.TestCase):
    def test_main_runs_collection_mode(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("zenquant.app.forward_data_collect.run_collection", return_value={"ok": True}),
            patch("builtins.print") as mock_print,
        ):
            code = main(["--output-dir", str(Path(tmp))])

        self.assertEqual(code, 0)
        mock_print.assert_called_once()

    def test_main_runs_health_mode(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("zenquant.app.forward_data_collect.evaluate_health", return_value={"ok": False}),
            patch("builtins.print") as mock_print,
        ):
            code = main(["--health-only", "--output-dir", str(Path(tmp))])

        self.assertEqual(code, 1)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
