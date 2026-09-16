# Olondunge brand

The mark is one luminous seed sending three lanes outward, inside a dashed return ring. That is
the product drawn literally: a model decides where work goes, the work leaves along a lane, and
the dashed ring is the result coming back for a blind check by a separate verifier. The light
always comes from inside the artwork, never from a drop shadow.

## Files

| File | Use |
|---|---|
| `logo-mark.svg` | primary mark, glowing. Anything 64 px and larger on a dark surface |
| `logo-mark-flat.svg` | the same drawing with no filters, no sparks and slightly heavier weights, for light surfaces, print, and renderers that drop filters |
| `logo-mark-animated.svg` | the same mark with the lanes and their nodes turning once every 26 seconds and the seed breathing. For a site, a docs page or an `img` tag; a still renderer shows the first frame |
| `favicon.svg` | thicker strokes, no ring, no sparks, no filters. Anything under 64 px |
| `logo-wordmark-dark.svg` | mark plus name, for a dark background. The mark is the glowing one without its sparks, with a wider bloom (blur 9 and 3.2 against 7 and 2.2) because it is drawn at 0.9 scale beside large type |
| `logo-wordmark-light.svg` | mark plus name, for a light background. The mark has no filters and no sparks, its lane ends and nodes use the deep variants of the lane hues, and its seed has a violet deep edge and a core tint centre |
| `src/banner.svg` | the README banner scene |
| `src/social-preview.svg` | the GitHub social card scene |
| `src/hero-allocation.svg`, `src/hero-verification.svg` | the two hero scenes |
| `*.png` | rendered from the above by `python3 assets/build.py`; never edited by hand |

Which source each raster comes from, because it is not what the size suggests:
`icon-32.png` from `favicon.svg`, `icon-128.png` from `logo-mark-flat.svg` (no bloom),
`icon-512.png` from `logo-mark.svg` (with bloom), the wordmark PNGs from their own SVG, and
each scene PNG from its file in `src/`.

Backgrounds differ by file and it matters on a page that has a light theme. `icon-32.png`,
`icon-128.png`, `icon-512.png` and both wordmark PNGs ship with a transparent background. Two
of them are made for dark surfaces only: the dark wordmark sets near white type, so it
disappears on a light surface (use `logo-wordmark-light.png` there), and `icon-512.png` is the
glowing mark, whose pure white seed core reads as a hole on a light surface (use `icon-128.png`
or `logo-mark-flat.svg` there). `banner.png`, `social-preview.png` and the two hero PNGs ship
with the void colour baked in and look the same in either theme.

## Palette

Dark is the primary surface. Every value below appears in the committed SVG sources.

| Token | Hex | Role |
|---|---|---|
| void | `#05050C` | page and scene background |
| nebula | `#170F3A` | the deep indigo cloud inside an aurora field |
| violet | `#8B5CF6` | lane one (claude in the scenes), the seed edge, the return ring of the mark. In the verification scene it is the verifier, because that scene draws the README packet example, where codex produces and claude verifies |
| violet tip | `#A78BFA` | the bright end of a violet lane, violet sparks, and the dashed orbit rings around the verifier in the verification scene |
| violet pale | `#C4B5FD` | reserved tint, used only where violet tip would be too strong |
| violet deep | `#6D3FE0` | violet on a light background |
| cyan | `#22D3EE` | lane two (codex in the scenes), which makes it the producer in the verification scene |
| cyan tip | `#67E8F9` | the bright end of a cyan lane, and about half of the four point glints |
| cyan deep | `#0E8FA6` | cyan on a light background |
| rose | `#FF5FA2` | lane three (grok in the scenes) |
| rose tip | `#FF8ABF` | the bright end of a rose lane |
| rose deep | `#E23C86` | rose on a light background |
| ember | `#FFC97A` | the local lane, the artifact crossing the veil, warm sparks |
| mist | `#EDEAFF` | primary text on dark, most sparks and glints, and the return path in the allocation scene. There the return carries no lane hue on purpose: a result belongs to no lane |
| haze | `#A9A3D6` | secondary text on dark, including captions and the lane legend; set lower in the hierarchy with opacity rather than with a darker colour |
| slate | `#14121F` | primary text on light |
| slate haze | `#5B5480` | secondary text on light |
| seed mid | `#EADCFF` | the middle stop of the seed gradient |
| core tint | `#FAF6FF` | the seed centre in the flat variants, a faint lavender. It keeps the centre from being exactly the page colour on white, but at 1.07 to 1 against white it does not separate on its own: the disc reads through the gradient around it and the violet deep edge |
| white | `#FFFFFF` | the core of the seed and of every node |
| black | `#000000` | the vignette only, never a fill |

A lane always keeps its hue. Violet, cyan and rose are the three lanes of the mark; ember is the
fourth, quieter lane and is never used as one of the three. Outside a lane, colour names no
lane: the aurora fields carry the lane hues at low opacity as atmosphere, the four point glints
are mist or cyan tip at 0.7 to 0.95 opacity, and the return ring of the mark is violet because
it frames the whole mark in the seed's own colour, not because it belongs to lane one. Only the
scenes draw a return path, and there it is mist.

## Geometry

The mark is drawn in a 256 by 256 box, centred on (128, 128).

- Lanes are cubic curves fitted to a spiral from radius 21 to radius 88 over 80 degrees, starting
  straight up and repeating every 120 degrees. Stroke 15, round caps.
- Nodes sit at the outer end of each lane, radius 11.5, with a white core at 32 percent of that.
- The seed is radius 18 with a white core, on a radial gradient from white through seed mid to
  violet.
- The return ring is radius 112, stroke 2.2, dash `2 9`, violet at 34 percent opacity. Every
  file that draws the mark uses those four numbers, the scene copies included.
- The flat mark and the favicon carry more weight than the glowing mark, because without a
  bloom a stroke looks thinner than it measures. Lanes 15 in the glowing mark, 16 flat, 24 in
  the favicon. Nodes 11.5, 12.5, 17. Seed 18, 19, 26. The flat variants also give the seed a
  violet deep edge and a core tint centre, for white surfaces.
- The glowing mark carries sixteen small sparks. The flat mark and the favicon carry none, and
  the favicon also drops the ring, because dashes and hairlines disappear below 64 px.

## Clear space and minimum size

- Clear space on every side is 16 units of the 256 box, one sixteenth of its width, measured
  outward from the edge of the box and scaled with the mark. No text, logo or interface element
  enters that band. Background texture such as sparks and aurora may pass through it, and the
  mark's own sparks sit inside the box.
- Minimum size for `logo-mark.svg` is 64 px. Below that use `favicon.svg`. On a dark surface it
  stays legible down to 16 px. On white at 16 px the three arms still read, but the inner half of
  each lane fades toward the page, so use 32 px or more on white where the slot allows. The flat
  variants give the seed a violet edge so it still reads as a disc on a white surface, where its
  core would otherwise dissolve into the background.
- Minimum size for either wordmark is 300 px wide, where its mark is 65 px, just above the
  glowing mark's own floor. The line under the name is then about 9 px, so where that line must
  be read, use 400 px or more (about 12.5 px). Below 300 px use the mark alone.

## Typography

- Display and body: Fira Sans, falling back to Inter, then DejaVu Sans, then the system sans.
- Technical labels (lane names, captions in the scenes): Fira Mono, falling back to DejaVu Sans
  Mono, then the system monospace.
- The name is always set in lower case: `olondunge`.
- The line under the name is `one model, many minds`. It describes what the tool does and is not
  a translation of the name.

## Glow, and how to keep it

The glow is a two pass bloom: the artwork blurred wide, the artwork blurred tight, then the
artwork itself on top, merged in that order. The seed adds an outer halo built by dilating its
alpha, blurring it, flooding it with violet and compositing that under the original.

Two practical rules come out of this:

1. A perfectly horizontal or vertical straight path has a zero height or zero width bounding box,
   and a filter whose region is a percentage of that box then renders nothing. Give any glowing
   path a slight curve.
2. CairoSVG does not implement these filter primitives, so it renders the artwork without its
   glow. `assets/build.py` uses headless Chrome for that reason and will not fall back. It
   takes `google-chrome`, `google-chrome-stable`, `chromium` or `chromium-browser` from PATH,
   and `OLONDUNGE_CHROME` names any other Chromium build explicitly.

## Motion

`logo-mark-animated.svg` carries the motion, as SMIL inside the file, so it needs no script
and no CSS from the page. The three lanes and their nodes turn slowly clockwise as one group,
which is the direction work travels in the mark, and the seed breathes between radius 18 and
20.5 on a 3.6 second cycle. The return ring does not turn: it sits outside the rotating group,
so its dashes stay put while the lanes sweep past. The sparks and the ring are still. Use the
still mark wherever motion would distract, and never animate the favicon.

## Usage

Do:

- put the glowing mark on the void colour or on anything darker than about 15 percent lightness;
- use the flat mark on light surfaces, at small sizes, and anywhere filters may be dropped;
- keep the three lane hues in their fixed order when the mark is recoloured for one surface.

Do not:

- add a drop shadow, an outline, a bevel, or a background plate to the mark;
- rotate, shear, or re-space the lanes, or recolour one lane on its own;
- stretch a wordmark to fit a box, or set the name in capitals;
- edit a PNG by hand. Change the SVG and run the build.

## Known gap

There is no mid tone wordmark. Mist type needs a dark surface and slate type needs a light
one, so on a surface between roughly 40 and 60 percent lightness neither is safe. Place the
mark alone there, or put the lockup on a plate of the void colour.

## Licence

These files are part of the Olondunge repository and carry the same Apache-2.0 licence as the
code. No trademark is registered or claimed. If you fork the project, please change the name and
the mark so that users can tell your build from this one.
