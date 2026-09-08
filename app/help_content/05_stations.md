# Stations

A station is one cross-section plane through the motor, perpendicular to the detected axis. The
solid is built from these sections, so the Stations page is where you check that the
reconstruction saw the grain you think it saw.

The page has two halves, and both are views of the same data as the yellow rings in the 3D
viewport. Selecting a table row highlights that station in all three places at once.

## The radius profile chart

R_outer and R_bore against z, in report-frame millimetres. This is the grain-geometry picture a
propulsion engineer actually reads: the outer wall along the top, the bore or slot radius below
it, and the gap between them as the remaining web.

Red vertical lines mark **topology events**. A dashed line marks the currently selected station.

The chart's z and the table's Z are the same number in the same frame, so a position on the chart
and a row in the table need no conversion between them.

## The station table

One row per station, measured by slicing the **rebuilt solid** at exactly that plane — not by
sampling a band around it, and not by re-deriving what the engine intended to build. What the
table shows is what was actually built.

| Column | Meaning |
|---|---|
| # | station index, from the fore end |
| Z | axial position in millimetres, report frame |
| Loops | how many closed loops the cross-section has |
| R_outer | the largest loop's max radius from the axis |
| R_bore | the next-largest loop's max radius, if there is one |
| Class | `barrel`, `dome`, or `transition` |

**Class** is derived, not read from the engine: a station at a topology event is `transition`, a
station whose outer radius is under 98 % of the largest outer radius anywhere is `dome`, and the
rest are `barrel`. Rows at a topology event are tinted amber.

## Topology events

A topology event is the bisected z where a section's loop topology changes — a circular bore
becoming a star, six satellite perforations dying against a flat wall, a slot breaking through
into a dome. The engine finds them by bisection and builds the solid in separate windows either
side of each one, which is what lets a single solid carry a genuine change of cross-section shape.

These are the numbers most likely to expose a **wrong-direction axis**: compare them against where
you know the grain's features start and stop. The axis sign is canonicalised so the
largest-magnitude component is positive, so if that convention disagrees with your part's fore-aft
direction, every z in the report is measured from the other end. Recount before concluding that
anything is misplaced.

The count is also on the Output page under Report; the full list is in the report JSON as
`topology_events_z_mm`.

## Where the stations went

With **adaptive stations** on, the stations should visibly cluster in the domes and around feature
edges rather than sitting uniformly. If they are uniform anyway, adaptive placement found nothing
to steer on — which is information about the geometry, not a failure.

No stations are placed right at either tip. That band is rebuilt from an analytic dome fit
instead, and the mint **dome-cap fit** layer in the viewport draws what was built there.
