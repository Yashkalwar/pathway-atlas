import pandas as pd

from app import config
from app.pipeline.build import build_analytics
from app.pipeline.simulate import simulate


def test_same_seed_is_reproducible_and_seed_changes_output():
    a, b, c = simulate(7, 300), simulate(7, 300), simulate(8, 300)
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert not a["drug_exposure"].equals(c["drug_exposure"])


def test_small_build_is_consistent():
    raw = simulate(3, 600)
    an, attr = build_analytics(raw, config.rules(), config.concepts())
    j = an["index_journey"]
    assert attr["n_index"] == len(j) == j.person_id.nunique()
    assert j.pdc_12m.between(0, 1).all()
    assert set(j.first_leave) <= {"persistent", "switch", "discontinue"}
    # hospitalization events = inpatient visits of index patients inside their follow-up window
    assert an["patient_events"].event_type.str.startswith("hospitalization").sum() == j.n_hosp.sum()
