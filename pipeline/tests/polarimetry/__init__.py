"""Independent test oracles for the polarimetry tests (PRD 6.2, 7).

Nothing in this package imports ``terraluna_pipeline.polarimetry``: PRD 7 requires the expanded
formulas to be tested against a separately implemented basis transformation, never against a
copy of the code under test.

pytest puts ``pipeline/tests`` on ``sys.path`` (its default prepend import mode), so tests
import these modules as ``polarimetry.<module>``. mypy runs from the repository root with
explicit package bases and names the same files ``pipeline.tests.polarimetry.<module>``, so the
tests import them under that name inside ``if TYPE_CHECKING``. Both names refer to these files.
"""
