"""Replay matrices for the patterns and anti-patterns from the platform-core patching guide.

Each test captures histories recorded by one version of the code and replays them on another.
`replays_cleanly` False means the live code would raise a non-determinism error for that order.
"""

import re
from pathlib import Path

import workflows_v2
from scenarios import (
    anti_inverted,
    anti_inverted_bad_deprecate,
    anti_nested,
    anti_nested_deprecate_inner,
    anti_patch_id_reuse,
    anti_patch_in_loop,
    chain_1_all_versions,
    chain_2_deprecate_oldest,
    chain_3_deprecate_next,
    chain_4_final,
    lifecycle_1_patch,
    lifecycle_2_deprecate,
    lifecycle_3_remove,
    loop_hoisted_ok,
    v1_baseline,
)
from tests.helpers import capture, replays_cleanly


async def test_lifecycle_three_phases(env):
    legacy = await capture(env, v1_baseline)
    phase1 = await capture(env, lifecycle_1_patch)
    phase2 = await capture(env, lifecycle_2_deprecate)

    # Phase 1: patched() keeps old and new executions replayable.
    assert await replays_cleanly(lifecycle_1_patch.OrderWorkflow, legacy)
    assert await replays_cleanly(lifecycle_1_patch.OrderWorkflow, phase1)
    # Phase 2: only safe once no order took the else branch (legacy history breaks).
    assert not await replays_cleanly(lifecycle_2_deprecate.OrderWorkflow, legacy)
    assert await replays_cleanly(lifecycle_2_deprecate.OrderWorkflow, phase1)
    assert await replays_cleanly(lifecycle_2_deprecate.OrderWorkflow, phase2)
    # Phase 3: safe for phase-2 histories (lenient marker)...
    assert await replays_cleanly(lifecycle_3_remove.OrderWorkflow, phase2)
    # ...but skipping phase 2 breaks histories that carry a phase-1 (non-lenient) marker.
    assert not await replays_cleanly(lifecycle_3_remove.OrderWorkflow, phase1)
    assert not await replays_cleanly(lifecycle_3_remove.OrderWorkflow, legacy)


async def test_elif_chain_deprecates_bottom_up(env):
    legacy = await capture(env, v1_baseline)
    era1 = await capture(env, lifecycle_1_patch)  # reserve patch only
    era2 = await capture(env, chain_1_all_versions)  # hold patch active
    after_step2 = await capture(env, chain_2_deprecate_oldest)
    after_step3 = await capture(env, chain_3_deprecate_next)

    # Step 1: every era replays.
    for history in (legacy, era1, era2):
        assert await replays_cleanly(chain_1_all_versions.OrderWorkflow, history)
    # Step 2 (oldest first): needs the legacy population gone; reserve-era orders are bridged.
    step2 = chain_2_deprecate_oldest.OrderWorkflow
    assert not await replays_cleanly(step2, legacy)
    assert await replays_cleanly(step2, era1)
    assert await replays_cleanly(step2, era2)
    # Step 3: needs the reserve-era population gone. Doing it first (top-down) breaks every older era.
    step3 = chain_3_deprecate_next.OrderWorkflow
    assert not await replays_cleanly(step3, legacy)
    assert not await replays_cleanly(step3, era1)
    assert await replays_cleanly(step3, era2)
    assert await replays_cleanly(step3, after_step2)
    # Step 4: only histories that went through the deprecate_patch step (lenient marker) survive.
    step4 = chain_4_final.OrderWorkflow
    assert not await replays_cleanly(step4, era2)
    assert not await replays_cleanly(step4, after_step2)
    assert await replays_cleanly(step4, after_step3)


async def test_inverted_patch_replays_like_the_proper_form_but_misdeprecates(env):
    legacy = await capture(env, v1_baseline)
    proper = await capture(env, lifecycle_1_patch)
    inverted = await capture(env, anti_inverted)

    # Behaviorally equivalent: the cost is readability, not replay.
    for history in (legacy, proper, inverted):
        assert await replays_cleanly(anti_inverted.OrderWorkflow, history)
        assert await replays_cleanly(lifecycle_1_patch.OrderWorkflow, history)
    # The plausible cleanup keeps the body of the `if not` (the OLD code): breaks every new order.
    bad = anti_inverted_bad_deprecate.OrderWorkflow
    assert await replays_cleanly(bad, legacy)
    assert not await replays_cleanly(bad, proper)
    assert not await replays_cleanly(bad, inverted)


async def test_nested_patches_have_three_populations_and_no_safe_inner_deprecation(env):
    legacy = await capture(env, v1_baseline)
    outer_only = await capture(env, lifecycle_1_patch)
    both = await capture(env, anti_nested)

    for history in (legacy, outer_only, both):
        assert await replays_cleanly(anti_nested.OrderWorkflow, history)
    # Deprecating the inner patch while outer-only orders are open sends them down the hold path.
    inner = anti_nested_deprecate_inner.OrderWorkflow
    assert await replays_cleanly(inner, legacy)
    assert not await replays_cleanly(inner, outer_only)
    assert await replays_cleanly(inner, both)


async def test_patch_in_loop_is_equivalent_to_hoisted(env):
    legacy = await capture(env, v1_baseline)
    in_loop = await capture(env, anti_patch_in_loop)
    hoisted = await capture(env, loop_hoisted_ok)

    # patched() is memoized, so it is one decision either way: same markers, same replay.
    for history in (legacy, in_loop, hoisted):
        assert await replays_cleanly(anti_patch_in_loop.OrderWorkflow, history)
        assert await replays_cleanly(loop_hoisted_ok.OrderWorkflow, history)


async def test_reusing_a_patch_id_for_a_different_change_breaks_marker_histories(env):
    # Completed histories cover the point where the reused ID now adds a notification. (An order still
    # parked at the ack wait does not fail: it silently runs the new activity instead.)
    legacy = await capture(env, v1_baseline, completed=True)
    with_marker = await capture(env, lifecycle_1_patch, completed=True)

    reuse = anti_patch_id_reuse.OrderWorkflow
    assert await replays_cleanly(reuse, legacy)  # no marker: False on both calls
    assert not await replays_cleanly(reuse, with_marker)  # marker => notification now expected


async def test_rename_cutover_needs_the_old_type_drained(env):
    old_type = await capture(env, v1_baseline)
    new_type = await capture(env, workflows_v2, workflow_type="OrderWorkflowV2")

    # Step 1: both types registered (worker.py): every order replays.
    both = [v1_baseline.OrderWorkflow, workflows_v2.OrderWorkflowV2]
    assert await replays_cleanly(both, old_type)
    assert await replays_cleanly(both, new_type)
    # Step 2: dropping the old class while old-type orders are open strands them.
    only_new = [workflows_v2.OrderWorkflowV2]
    assert await replays_cleanly(only_new, new_type)
    assert not await replays_cleanly(only_new, old_type)


def test_patch_ids_follow_the_naming_convention():
    """<workflow-slug>-<feature-slug>: lowercase kebab-case, `order-` prefix, no ticket ids."""
    ids: set[str] = set()
    for path in Path("scenarios").glob("*.py"):
        ids |= set(re.findall(r'(?:patched|deprecate_patch)\("([^"]+)"\)', path.read_text()))
    assert ids, "no patch ids found"
    for patch_id in ids:
        assert re.fullmatch(r"order(-[a-z0-9]+)+", patch_id), patch_id
