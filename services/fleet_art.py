# services/fleet_art.py
"""Side profiles of the actual cars in the fleet, drawn to each model's own
proportions.

The first attempt at this was four cartoon shapes, and they looked it. The
difference between a Sportage and an Outlander is not "both are SUVs" — it is
the wheelbase against the overhangs, where the roof stops, how upright the
tailgate is, and the shape of the glass. Those are the things the eye actually
uses, so those are what is specified here, per model, in millimetres taken from
the published dimensions and then scaled to one canvas.

Everything is built from one PROFILE: give it the real length, wheelbase and
height and it solves the rest, so the five cars are drawn to a common scale and
a Pickup really is longer than a Lancer on the page rather than just differently
shaped. Add a model by adding a profile.

Colour is the AJi palette rather than each car's real paint: a fleet page wants
five pictures that look like a set, and the plate is what tells two Sportages
apart.
"""
from collections import namedtuple

INK = '#111111'
YELLOW = '#FCD700'
YELLOW_DK = '#E0BC00'        # the shaded lower body
YELLOW_LT = '#FFE24A'        # the highlight along the shoulder
GLASS = '#2A2E33'
GLASS_LT = '#464C54'
TYRE = '#1A1A1A'
RIM = '#D8D8D8'
RIM_DK = '#9A9A9A'

#: One canvas for every car, and one scale. 4 800 mm of car fills the frame,
#: so anything shorter is drawn shorter.
CANVAS_W, CANVAS_H = 1120, 460
SCALE_MM = 4800.0
GROUND_Y = 372


Profile = namedtuple('Profile', [
    'name',
    'length_mm', 'wheelbase_mm', 'height_mm', 'wheel_dia_mm',
    'front_overhang_mm',
    # every one of these is a fraction of the LENGTH, measured from the nose,
    # so a profile reads the same whatever the car's real size
    'bonnet_x', 'screen_top_x', 'roof_end_x', 'tail_x',
    'bonnet_y', 'belt_y', 'roof_y', 'sill_y',
    'body',                  # 'suv' | 'sedan' | 'pickup'
    'doors',                 # x fractions of the door shut lines
    'windows',               # (x0, x1) fractions of each side window
    'bed_x',                 # pickup only: where the bed starts
])


def suv(name, length, wheelbase, height, wheel_dia, front_oh, **kw):
    base = dict(bonnet_x=.30, screen_top_x=.44, roof_end_x=.74, tail_x=.90,
                bonnet_y=.46, belt_y=.52, roof_y=.10, sill_y=.80,
                body='suv', doors=(.47, .655), bed_x=0,
                windows=((.455, .615), (.635, .745), (.762, .83)))
    base.update(kw)
    return Profile(name, length, wheelbase, height, wheel_dia, front_oh, **base)


#: Published kerb dimensions, rounded. The exact millimetre does not matter;
#: the RATIOS between the five do, and those are what make them tell apart.
PROFILES = {
    # KIA Sportage NQ5 — short front overhang, long roof, raked tailgate
    'sportage': suv('KIA Sportage', 4660, 2755, 1680, 700, 900,
                    roof_end_x=.755, tail_x=.915, roof_y=.10),

    # Mitsubishi Outlander — taller, squarer, tailgate nearly vertical
    'outlander': suv('Mitsubishi Outlander', 4710, 2705, 1745, 715, 950,
                     roof_end_x=.80, tail_x=.945, roof_y=.07,
                     windows=((.455, .615), (.635, .755), (.775, .855))),

    # Mitsubishi Lancer — low roof, long bonnet, a boot behind the glass
    'lancer': Profile('Mitsubishi Lancer', 4570, 2635, 1490, 640, 900,
                      bonnet_x=.34, screen_top_x=.50, roof_end_x=.70, tail_x=.90,
                      bonnet_y=.52, belt_y=.58, roof_y=.16, sill_y=.82,
                      body='sedan', doors=(.52, .70), bed_x=0,
                      windows=((.515, .655), (.675, .775))),

    # Mitsubishi L200 double cab — cab stops early, open bed behind it
    'pickup': Profile('Mitsubishi Pickup', 5225, 3000, 1795, 760, 900,
                      bonnet_x=.28, screen_top_x=.40, roof_end_x=.64, tail_x=.98,
                      bonnet_y=.44, belt_y=.50, roof_y=.08, sill_y=.78,
                      body='pickup', doors=(.435, .555), bed_x=.66,
                      windows=((.415, .535), (.555, .635))),
}

#: Which profile a car gets, matched on its model name. First hit wins.
MATCH = [
    ('sportage', 'sportage'), ('tucson', 'sportage'), ('sorento', 'outlander'),
    ('outlander', 'outlander'), ('asx', 'sportage'), ('pajero', 'outlander'),
    ('rav', 'sportage'), ('x-trail', 'outlander'), ('prado', 'outlander'),
    ('pickup', 'pickup'), ('l200', 'pickup'), ('hilux', 'pickup'),
    ('navara', 'pickup'), ('triton', 'pickup'),
    ('lancer', 'lancer'), ('corolla', 'lancer'), ('elantra', 'lancer'),
    ('accent', 'lancer'), ('cerato', 'lancer'), ('sedan', 'lancer'),
]
DEFAULT = 'sportage'


def profile_for(make, model):
    name = f'{make} {model}'.lower()
    for needle, key in MATCH:
        if needle in name:
            return PROFILES[key]
    return PROFILES[DEFAULT]


# ══════════════════════════════════════════════════════════════════════════
#  THE DRAWING
#
#  One routine for all of them. The profile decides the shape; everything
#  below is the same car-drawing logic applied to different numbers, which is
#  why the five come out looking like one set.
# ══════════════════════════════════════════════════════════════════════════
def _f(v, n=1):
    return f'{v:.{n}f}'


def _alloy(cx, cy, r):
    """A wheel with a five-spoke rim.

    Spokes are drawn rather than suggested because at the size the fleet page
    uses these, a plain grey disc reads as a hole in the car.
    """
    import math
    hub = r * 0.56
    out = [
        f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}" fill="{TYRE}"/>',
        f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r*0.80)}" fill="{INK}"/>',
        f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(hub)}" fill="{RIM}"/>',
    ]
    for i in range(5):
        a = math.radians(i * 72 - 90)
        x1, y1 = cx + math.cos(a) * hub * 0.22, cy + math.sin(a) * hub * 0.22
        x2, y2 = cx + math.cos(a) * hub * 0.92, cy + math.sin(a) * hub * 0.92
        out.append(f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}"'
                   f' stroke="{RIM_DK}" stroke-width="{_f(r*0.17)}"'
                   f' stroke-linecap="round"/>')
    out.append(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(hub*0.26)}" fill="{RIM_DK}"/>')
    return ''.join(out)


def build(p):
    """The SVG for one profile."""
    import math

    # ── solve the geometry in canvas units ────────────────────────────────
    k = (CANVAS_W - 120) / SCALE_MM            # mm -> px, one scale for all
    L = p.length_mm * k
    x0 = (CANVAS_W - L) / 2                    # centred, so short cars are short
    wheel_r = p.wheel_dia_mm * k / 2
    front_cx = x0 + p.front_overhang_mm * k + wheel_r
    rear_cx = front_cx + p.wheelbase_mm * k
    wheel_cy = GROUND_Y - wheel_r
    H = p.height_mm * k
    roof_top = GROUND_Y - H

    def X(frac):
        return x0 + L * frac

    def Y(frac):
        """0 at the roof, 1 at the ground."""
        return roof_top + (GROUND_Y - roof_top) * frac

    nose, tail = x0, x0 + L
    bonnet_y, belt_y, roof_y, sill_y = (Y(p.bonnet_y), Y(p.belt_y),
                                        Y(p.roof_y), Y(p.sill_y))
    arch_r = wheel_r * 1.16

    # ── the body, with the arches cut out of its underside ────────────────
    if p.body == 'pickup':
        deck_y = Y(p.belt_y + .10)          # the gap behind the cab
        bed_top_y = Y(p.belt_y - .01)       # the top rail of the bed
        upper = (f'M {_f(nose)} {_f(bonnet_y + 26)} '
                 f'C {_f(nose - 4)} {_f(bonnet_y + 4)} {_f(nose + 18)} {_f(bonnet_y - 4)} '
                 f'{_f(X(.10))} {_f(bonnet_y - 6)} '
                 f'L {_f(X(p.bonnet_x))} {_f(bonnet_y - 16)} '
                 f'L {_f(X(p.screen_top_x))} {_f(roof_y + 8)} '
                 f'Q {_f(X(p.screen_top_x + .02))} {_f(roof_y)} {_f(X(p.screen_top_x + .05))} {_f(roof_y)} '
                 f'L {_f(X(p.roof_end_x - .02))} {_f(roof_y)} '
                 f'Q {_f(X(p.roof_end_x))} {_f(roof_y)} {_f(X(p.roof_end_x))} {_f(roof_y + 14)} '
                 # down the back of the cab, across the gap, then UP the front
                 # of the bed and along its top rail to the tailgate. Running
                 # straight from the cab to the tail at deck height drew a
                 # flatbed, which is a different vehicle.
                 f'L {_f(X(p.roof_end_x))} {_f(deck_y)} '
                 f'L {_f(X(p.bed_x))} {_f(deck_y)} '
                 f'L {_f(X(p.bed_x))} {_f(bed_top_y)} '
                 f'L {_f(tail - 6)} {_f(bed_top_y)} '
                 f'Q {_f(tail)} {_f(bed_top_y)} {_f(tail)} {_f(bed_top_y + 14)} '
                 f'L {_f(tail)} {_f(sill_y)} ')
    else:
        upper = (f'M {_f(nose)} {_f(bonnet_y + 26)} '
                 f'C {_f(nose - 4)} {_f(bonnet_y + 2)} {_f(nose + 16)} {_f(bonnet_y - 8)} '
                 f'{_f(X(.10))} {_f(bonnet_y - 12)} '
                 f'L {_f(X(p.bonnet_x))} {_f(bonnet_y - 22)} '
                 f'L {_f(X(p.screen_top_x))} {_f(roof_y + 10)} '
                 f'Q {_f(X(p.screen_top_x + .015))} {_f(roof_y)} {_f(X(p.screen_top_x + .05))} {_f(roof_y)} '
                 f'L {_f(X(p.roof_end_x - .03))} {_f(roof_y)} '
                 f'Q {_f(X(p.roof_end_x))} {_f(roof_y + 2)} {_f(X(p.roof_end_x + .015))} {_f(roof_y + 14)} '
                 f'L {_f(X(p.tail_x))} {_f(belt_y - 6)} '
                 f'C {_f(X(p.tail_x + .04))} {_f(belt_y + 14)} {_f(tail)} {_f(belt_y + 30)} '
                 f'{_f(tail)} {_f(belt_y + 46)} '
                 f'L {_f(tail)} {_f(sill_y)} ')

    # underside: right to left, lifting over each arch
    lower = (f'L {_f(rear_cx + arch_r)} {_f(sill_y)} '
             f'A {_f(arch_r)} {_f(arch_r)} 0 0 0 {_f(rear_cx - arch_r)} {_f(sill_y)} '
             f'L {_f(front_cx + arch_r)} {_f(sill_y)} '
             f'A {_f(arch_r)} {_f(arch_r)} 0 0 0 {_f(front_cx - arch_r)} {_f(sill_y)} '
             f'L {_f(nose)} {_f(sill_y)} Z')
    body_path = upper + lower

    # ── the glass ─────────────────────────────────────────────────────────
    panes = []
    for i, (a, b) in enumerate(p.windows):
        xa, xb = X(a), X(b)
        top_a = roof_y + (12 if i else 20)
        if i == 0:                       # the windscreen side: raked front edge
            panes.append(f'<path d="M {_f(xa)} {_f(belt_y - 6)} '
                         f'L {_f(xa + L * .035)} {_f(top_a)} L {_f(xb)} {_f(top_a)} '
                         f'L {_f(xb)} {_f(belt_y - 6)} Z" fill="{GLASS}"/>')
        elif i == len(p.windows) - 1 and p.body != 'pickup':
            # Its top edge follows the rear screen down, rather than running
            # level and poking out through the tailgate — which is exactly what
            # a rectangle did, and it read as a hole in the back of the car.
            def under_roof(x):
                x1, y1 = X(p.roof_end_x), roof_y + 12
                x2, y2 = X(p.tail_x), belt_y - 10
                t = max(0.0, min(1.0, (x - x1) / (x2 - x1)))
                return y1 + (y2 - y1) * t + 6
            panes.append(f'<path d="M {_f(xa)} {_f(under_roof(xa))} '
                         f'L {_f(xb)} {_f(under_roof(xb))} '
                         f'L {_f(xb)} {_f(belt_y - 6)} L {_f(xa)} {_f(belt_y - 6)} Z"'
                         f' fill="{GLASS}"/>')
        else:
            panes.append(f'<rect x="{_f(xa)}" y="{_f(top_a)}" width="{_f(xb - xa)}"'
                         f' height="{_f(belt_y - 6 - top_a)}" rx="3" fill="{GLASS}"/>')
    glass = ''.join(panes)

    # ── the details that make it read as a car ────────────────────────────
    detail = []
    # shoulder crease
    detail.append(f'<path d="M {_f(X(.14))} {_f(belt_y + 34)} L {_f(X(p.tail_x))} {_f(belt_y + 26)}"'
                  f' stroke="{YELLOW_LT}" stroke-width="5" fill="none" opacity=".95"/>')
    # door shut lines
    for d in p.doors:
        detail.append(f'<path d="M {_f(X(d))} {_f(belt_y - 4)} L {_f(X(d))} {_f(sill_y - 6)}"'
                      f' stroke="{INK}" stroke-width="2.4" opacity=".55"/>')
        detail.append(f'<rect x="{_f(X(d) + 10)}" y="{_f(belt_y + 24)}" width="{_f(L*.036)}"'
                      f' height="7" rx="3.5" fill="{INK}" opacity=".6"/>')
    # mirror
    mx = X(p.windows[0][0]) + L * .012
    detail.append(f'<path d="M {_f(mx)} {_f(belt_y - 2)} l -{_f(L*.028)} -4 l 2 12 l {_f(L*.026)} 2 Z"'
                  f' fill="{INK}"/>')
    # headlamp and tail lamp
    detail.append(f'<path d="M {_f(nose + 4)} {_f(bonnet_y + 6)} l {_f(L*.055)} -6 '
                  f'l 3 15 l -{_f(L*.055)} 5 Z" fill="#FFFFFF" stroke="{INK}"'
                  f' stroke-width="2.5" stroke-linejoin="round"/>')
    if p.body == 'pickup':
        detail.append(f'<rect x="{_f(tail - L*.045)}" y="{_f(Y(p.belt_y + .20))}"'
                      f' width="{_f(L*.032)}" height="{_f(L*.050)}" rx="3" fill="#C0392B"'
                      f' stroke="{INK}" stroke-width="2.5"/>')
        # the bed: wall, and the gap behind the cab
        detail.append(f'<path d="M {_f(X(p.roof_end_x))} {_f(deck_y)} L {_f(tail)} {_f(deck_y)}"'
                      f' stroke="{INK}" stroke-width="4" opacity=".8"/>')
    else:
        detail.append(f'<path d="M {_f(tail - L*.050)} {_f(belt_y + 18)} l {_f(L*.044)} 6 '
                      f'l -2 22 l -{_f(L*.042)} -4 Z" fill="#C0392B" stroke="{INK}"'
                      f' stroke-width="2.5" stroke-linejoin="round"/>')
    # fuel flap
    detail.append(f'<circle cx="{_f(X(p.tail_x - .06))}" cy="{_f(belt_y + 52)}" r="7"'
                  f' fill="none" stroke="{INK}" stroke-width="2.2" opacity=".5"/>')

    # a darker rocker along the bottom of the doors: the one piece of shading
    # that survives being flat, and it stops the body reading as a slab
    detail.append(f'<path d="M {_f(front_cx + arch_r - 6)} {_f(sill_y - 4)} '
                  f'L {_f(rear_cx - arch_r + 6)} {_f(sill_y - 4)} '
                  f'L {_f(rear_cx - arch_r + 6)} {_f(sill_y - 20)} '
                  f'L {_f(front_cx + arch_r - 6)} {_f(sill_y - 20)} Z"'
                  f' fill="{YELLOW_DK}"/>')

    arches = ''.join(
        f'<path d="M {_f(cx - arch_r)} {_f(sill_y)} '
        f'A {_f(arch_r)} {_f(arch_r)} 0 0 1 {_f(cx + arch_r)} {_f(sill_y)}"'
        f' fill="none" stroke="{INK}" stroke-width="5" opacity=".85"/>'
        for cx in (front_cx, rear_cx))

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS_W} {CANVAS_H}"
     width="{CANVAS_W}" height="{CANVAS_H}">
  <rect width="{CANVAS_W}" height="{CANVAS_H}" fill="#FFFFFF"/>
  <ellipse cx="{_f(CANVAS_W/2)}" cy="{_f(GROUND_Y + 16)}" rx="{_f(L*0.52)}" ry="20"
           fill="{INK}" opacity=".09"/>
  {_alloy(front_cx, wheel_cy, wheel_r)}
  {_alloy(rear_cx, wheel_cy, wheel_r)}
  <path d="{body_path}" fill="{YELLOW}" stroke="{INK}" stroke-width="5"
        stroke-linejoin="round"/>
  {glass}
  {''.join(detail)}
  {arches}
  {_alloy(front_cx, wheel_cy, wheel_r)}
  {_alloy(rear_cx, wheel_cy, wheel_r)}
</svg>'''
