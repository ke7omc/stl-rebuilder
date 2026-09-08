# The 3D viewport

The central 3D view shows the input mesh and the rebuilt solid in the same space, plus the
overlays that explain how the reconstruction was made. Everything here is driven from the **View**
menu, from the overlays floating in the canvas itself, or from a single-key shortcut.

## Layers and the legend

Six layers, each with a checkable entry in **View** and — for the five data layers — a clickable
row in the legend at the top right of the canvas. The two stay in sync in both directions: click
a legend row and the menu check follows, and the other way round.

| Layer | Color | What it is |
|---|---|---|
| Input mesh | pale grey-blue | the STL you loaded, drawn as a translucent ghost |
| Rebuilt solid | blue | the geometry that was actually built and written to the STEP |
| Station rings | yellow | the cross-sections the solid was built from |
| Dome-cap fit | mint | the station-free end bands, rebuilt from a dome fit |
| Topology events | red | the z where a section's loop topology changes |
| Axis line | grey | the detected motor axis (no legend row — it is a reference, not data) |

Station rings carry short tick combs at the silhouette so they stay visible edge-on. The
**dome-cap fit** layer deserves a note: the engine deliberately places no stations right at either
tip, because a per-station circle fit is systematically biased low where curvature peaks. Those
end bands are rebuilt from an analytic dome fit instead, and this layer draws them — in a
deliberately different style, with longitudinal meridians. Mint geometry is **built and verified
geometry**, not a gap and not a guess.

Selecting a station row on the Stations page lights that ring in bright yellow.

## Comparison modes

Three independent View entries, all about seeing the input and the output against each other.

- **Swap input to rebuilt (B)** — shows the input mesh near-opaque and hides the rebuilt solid.
  Press and release to A/B the two.
- **Rebuilt solid: transparent** — ghosts the solid so you can compare its fins and walls against
  the input mesh behind it.
- **Input mesh: solid color** — draws the input at full opacity, to compare its outer boundary
  against the solid. This one wins over the opacity the other two modes would otherwise apply.
- **Input mesh: show triangulation** — draws the input STL's real facet edges. For the input
  these triangles ARE the data — mesh density, faceting quality, where the generator packed more
  or fewer facets. Best combined with "Input mesh: solid color"; on the translucent ghost the
  edges are faint by nature. On a very dense mesh the edges read as a texture until you zoom in.

The transparent/solid-color toggles are also one click away in the legend: the half-filled-circle
button on the Input-mesh and Rebuilt-solid rows flips that layer's opacity mode, and lights up
cyan while the mode is active. Clicking the rest of the row still shows/hides the layer.

## Camera

- **Fit view (F)** — re-frame the camera on the whole scene.
- **Orthographic projection (O)** — parallel projection. Parallel edges stay parallel regardless
  of depth, which is what you want for silhouette and alignment checks; perspective quietly lies
  about both.
- The **camera-orientation widget** at the bottom left snaps to an axis view on click or drag.
- The **home button** beside it resets to the isometric view.

## Inspection

- **Section view (S)** — clips the model along its motor axis, with a SECTION slider along the
  bottom edge to sweep the cut. This is the honest way to look inside a bore or a slot; peering
  through transparency is not.
- **Deviation heatmap** — colors the rebuilt solid by its measured distance to the input mesh,
  instead of collapsing that into a single p95 number. It shows **where** the reconstruction
  deviates. The color scale is anchored to the verification tolerance and speaks the app's own
  state language: **green is nominal**, amber is approaching the gate, red is at or past it. A
  well-reconstructed part reads as a calm, clearly-visible green — not a dark surface lost
  against the dark background.

Both are also on the display-toggle overlay at the top left of the canvas, together with the
render-mode cycle. The overlay and the View menu are the same state, kept in sync.

## Render mode

| Mode | Shortcut | Notes |
|---|---|---|
| Shaded | Ctrl+1 | the default lit solid |
| Shaded + edges | Ctrl+2 | real geometric edges only |
| Wireframe | Ctrl+3 | full triangulation |

**W** cycles the three without going through the menu.

"Shaded + edges" draws **real geometric edges**, found from a dihedral-angle threshold — not the
raw triangulation. A smooth revolved wall genuinely has none, so from a typical side-on view this
mode can look identical to plain Shaded. That is correct behavior, not a failure: look at a bore,
a rim, or a slot transition to see it working.

## Exporting a picture

**File > Export viewport image (Ctrl+E)** saves the current 3D view as a PNG at the path you pick.
The existing file at that path is deleted before the new one is written, so the picture's creation
date always matches the picture.
