# Photographic website navigation

The eleven public pages in `pawflow-website/` keep their existing URLs and
complete static HTML. The shared ESPER interface turns their sections into a
photographic journey: the wheel, keyboard, touch, framed photographs and section
links all drive the same camera. The section index searches all 153 destinations,
including all 56 full how-to recipes and eight thematic guide indexes.

## Scenes and navigation

`esper-world.js` describes twelve generated photographs and their measured
openings in normalized image coordinates. The control room has four branches;
the archive has eight. Each photograph has an outgoing passage, and the graph
contains cycles. Twelve generated scenes provide the repeating visual world.

`esper-camera.js` composites the child photographs into the actual openings.
It interpolates camera position and scale, restoring a child's natural aspect
ratio as the camera enters. Local coordinates are rebased near the current depth
to prevent cumulative floating-point drift. Rendering is bounded and only runs
during transitions or resizing. Mobile uses the whole photograph above the reader.

`site.js` extracts the existing content into section records. Sequential navigation
and direct section links use stable destinations. Sequential movement advances
deeper through the graph, including when the section sequence wraps. Direct jumps between branches pull back
to their common ancestor before entering the target. Clicking a photograph retains
the route used to enter it; Pull back restores the previous visit and Junction
returns to its enclosing branching scene. The last section can enter another cycle.
Browser history retains the destination and its physical path.

The wheel scrolls long content first and requires a fresh gesture to leave it.
A single trackpad gesture cannot skip several sections. Keyboard arrows/Page Up/
Page Down and vertical swipes offer the same navigation. Form fields, code blocks,
videos, dialogs and the help chat retain their own input behavior.
Reduced motion removes camera travel; the section index is a native modal dialog.

## Media

All twelve new photographs were generated with `gpt_image_service`.
`assets/media/esper/provenance.json` records their sources and generation receipts.
The soundtrack and two zoom effects come from the validated installer mockup.
The installer and mockup remain separate from the website.

One looping player survives internal navigation. Playback starts on a user gesture
when browser policy requires it. Sound and volume controls persist preferences;
background tabs pause audio, demo playback lowers music volume, and full-page
navigation preserves the playback position. Product screenshots and videos stay
in the explanatory content. Downloads still resolve against the GitHub release API
and the existing same-origin help endpoint remains `/api/help`.

## Validation and preview

Run `tests/test_website_product_story.py` and `tests/test_website_esper.py`.
The latter validates graph reachability and camera precision with Node, and runs
the browser contract when Playwright and Chromium are installed. The browser
checks real navigation, all section destinations, the complete recipe catalogue,
history, audio, mobile controls and reduced motion.

Serve `pawflow-website/` over HTTP for the complete website. A generated review HTML
may embed the documents, photographs and soundtrack to open directly in a browser;
original product demos in that preview use the public site's media URLs.
Public deployment is a separate step after review.
