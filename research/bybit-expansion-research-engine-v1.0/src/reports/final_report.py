from __future__ import annotations

from src.reports.generator import write_reports


class FinalReport:
    """Compatibility facade for the five-report candidate-first output."""

    def generate(self, output_dir, payload):
        return write_reports(output_dir, payload)
