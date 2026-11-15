"""Race condition: two requests try to take the same bed at the same moment."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from api.models import Assignment
from api.placement import ConflictError, assign_bed
from api.schemas import PlacementRequirements

MEDICAL = PlacementRequirements(
    ward_type="MEDICAL", isolation_required=False, monitoring_required=False, requires_private_room=False
)
CONTESTED_BED = "4M-104B"


def test_only_one_of_two_simultaneous_assignments_wins(session_factory: sessionmaker[Session]):
    barrier = Barrier(2)

    def attempt(patient_id: str) -> str:
        with session_factory() as session:
            barrier.wait(timeout=5)
            try:
                assign_bed(session, patient_id, CONTESTED_BED, MEDICAL)
            except ConflictError as error:
                return error.code
            return "ASSIGNED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(attempt, ["P-1001", "P-1004"]))

    assert outcomes == ["ASSIGNED", "BED_NOT_ELIGIBLE"]
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(Assignment).where(Assignment.bed_id == CONTESTED_BED))
    assert count == 1
