import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zenquant.app.paper_live_scorecard import main


class PaperLiveScorecardCliTests(unittest.TestCase):
    def test_main_prints_scorecard_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = {
                "healthy": True,
                "status_label": "paper trading in progress",
            }
            with (
                patch("zenquant.app.paper_live_scorecard.collect_scorecard", return_value=fake),
                patch("builtins.print") as mock_print,
            ):
                code = main(["--output-root", str(Path(tmp))])

        self.assertEqual(code, 0)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
