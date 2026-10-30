# Timeline selection and V0.2 state replay

The original Agent Timeline, cost/label aggregates, transcript, artifacts, comms,
file diffs, file tree and spawn tree remain in the V0.2 fork. Summary + Labels
contains the new state explorer; the version selector retains V0.1 hourly bullets.
The private root opens the Gamejam timeline. `/review` remains the correction UI.

## Membership

Selection is a union of `(session ID, [start,end))` windows. A completed turn
belongs when its finish timestamp falls inside any window for its source session.
Overlapping windows never duplicate turns. Complete source prose can begin before
its selected finish-time window. Turn order is the dataset's global completion order.

| Selection | Included turns |
|---|---|
| Cell | That session and exact cell interval |
| Row | Union of the selected cells for that session |
| Column | Union of selected cells across included sessions |
| Connected lasso | Actual picked cells |
| Disjoint selection | Actual picked session/time pairs; gaps excluded |
| Fleet/org filter | Included sessions over its active time scope |

The selection bus now carries optional exact `windows` as well as legacy bounding
`timeRange`. V0.2 consumes the windows. They survive A2A pin/unpin; a session pin
intersects them with that session. The timeline restores only cells intersecting
those windows. A pin with no intersecting sessions preserves an empty array,
clears timeline highlights and selects no turns; it never widens to the bounding
range. With no pin, an empty A2A click remains a no-op.

## Three views

1. **State at cursor** (default, explicitly chosen by CKL): the saved accumulated
   graph after the latest selected processed turn. Scope to selected session actors,
   their related tasks and tasks changed in selected turns through the cursor.
   Include upstream parent/dependency tasks and connected actors as context.
   A Whole project checkbox reveals the full snapshot at the same cursor.
2. **Changes in selection**: only tasks, actor updates, decisions and relations
   recorded in selected turns through the cursor. Task cards retain earlier fields
   required to interpret sparse patches. A gap's changes do not count here.
3. **Recorded LLM input**: the exact saved context supplied before the cursor turn.
   Its graph uses only the entities supplied in that input. Missing referenced
   entities are explicit stubs; they are never filled from later state.

State at cursor includes real earlier history, including activity in gaps or by
other agents that affected the scoped entities. It is not a hypothetical history
with unselected turns removed. The Changes view answers the separate question of
what happened inside the selection. Neither view imports the final dataset graph
into an earlier cursor. Relations are recorded claims, not inferred dependencies.

## Replay

Replay keeps Agent Timeline selection and other widgets fixed. First/previous/play/
next/last and the scrubber move the state cursor within selected processed turns.
Turn mode visits each selected turn. Hour mode visits the last selected processed
turn in each occupied UTC hour, labelled with its actual timestamp. It skips empty
hours; it does not manufacture hour-end snapshots. Changing selection/version
stops playback. Hidden tabs pause on the next tick. Playback at the end restarts
from the first frame and stops at the last. Graph keyboard controls do not clear
or navigate Agent Timeline selection.

Coverage and replay endpoints come from the active dataset and accepted prefix.
Pending source turns have no generated state. Selecting a period with no processed
turns clears the graph and displays an explicit empty state.

These are original machine-history snapshots. Human corrections are review records
until a reviewed replay is built; the UI labels this distinction. No inference or
paid API calls occur when selecting, inspecting, or replaying.

## Legacy pane boundaries

V0.1 bullets retain hourly overlapping-bucket semantics. Summary transcript and
file diffs apply exact per-session windows; artifacts already filter picked cells.
The A2A transcript tests the sender or receiver session against the ambient exact
windows, including gaps; a human pin requires a selected receiving session.
These message-time projections are distinct from V0.2 turn-finish membership.
The A2A graph nodes/edges still use the bounding interval and may include gap
traffic; this remains a known limitation. A human A2A pin retains sender/receiver
meaning, not task ownership or human-help blockers.
