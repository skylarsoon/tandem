from tandem_worker.contracts import Candidate
from tandem_worker.explain import suggestions


def idea(id_, title, rating):
    return Candidate(id=id_, category="ACTIVITY", total_cents=5000, title=title, rating=rating, observed_at=0, valid_until=1,
                     source_url=f"https://fixtures.invalid/{id_}", run_ref="r", mode="FIXTURE", cost_basis="ESTIMATE_PER_PERSON")


def test_the_same_place_found_twice_is_one_idea():
    found = [idea("a1", "Diamond Head hike", 4.8), idea("a2", "diamond head hike", 4.8), idea("b", "Snorkel tour", 4.6)]
    assert [s.title for s in suggestions(found)] == ["Diamond Head hike", "Snorkel tour"]
