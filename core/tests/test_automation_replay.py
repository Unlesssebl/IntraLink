from pathlib import Path

from core.automation.replay import run_replay


async def test_versioned_offline_acceptance_dataset_is_exact() -> None:
    root = Path(__file__).parents[2] / "datasets" / "automation" / "v1"
    scores = await run_replay(root)
    assert {score.dataset for score in scores} == {"intake", "compatibility", "redirect_target", "action_selection", "execution_feedback"}
    assert all(score.total > 0 for score in scores)
    assert all(score.accuracy == 1.0 for score in scores), [score.failures for score in scores]
