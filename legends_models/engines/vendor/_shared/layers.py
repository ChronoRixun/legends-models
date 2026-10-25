"""Layered cloth and loose geometry: detection, coupling and the layer-crossing check.

Shared by the MUA1 and MUA2 fitters and validators (copied next to each engine's tools by
legends_models.engines.materialise). NumPy + SciPy only; no game data.

Layered cloth
    A coat with a separate lining (two shells a few tenths of a unit apart) deforms correctly only
    if both shells follow the same skinning. `find_layers` pairs every vertex of an inner shell with
    the closest point of the next shell out (a triangle + barycentric coordinates + the signed offset
    along that triangle's normal). Inner/outer is decided by distance from the skeleton, so the rule
    is general (lining, double-sided capes, layered skirts, folded collars).

    The fitter couples each inner vertex to its outer triangle: the inner vertex is bound with the
    outer surface's source weights (so the authored inset is carried through the bind refit) and its
    skin weights are tied to the outer surface's weights while fitting, so both layers deform by the
    same blend and the inset is only ever rotated/scaled, never inverted.

The layer-crossing check (`crossings`)
    For each detected pair, the posed inner vertex must stay on the same side of the posed outer
    triangle as in the source bind: sign((p_inner - q) . n_outer) unchanged, where q is the posed
    point at the bind barycentric coordinates. Any sign change (or touching) in any frame, or in the
    converted bind itself, is a crossing. The gate requires zero.

Loose geometry (long hair, tails of cloth beyond a leaf bone)
    `loose_vertices` finds vertices that sit further from every influencing bone chain than a
    limit and names, for each, the nearest body bone that is not part of its own anchor chain (the
    bone it rests on). The MUA1 fitter adds that bone as an influence with a smooth falloff along the
    strand and lets its chain objective settle the blend, so the stock-relative chain-distance rule
    holds without changing the gate.
"""
import numpy as np

LAYER_THICKNESS = 1.25      # model units: widest inset searched (Gambit's coat lining is ~0.6)
LAYER_MIN_GAP = 0.02        # closer than this is coincident/decal geometry (z-fights anyway), not a layer
LAYER_SIMILARITY = 0.5      # source skinning overlap needed: both shells of one garment follow the same bones
# Hands and feet are not layered cloth: curled fingers touch each other and the palm (self-contact
# of one skin), and the port folds all fingers onto two bones. Pairs whose inner vertex or outer
# point is mostly skinned to these bones are not layer pairs.
EXTREMITY_BONES = (' Hand', ' Finger', ' Foot', ' Toe', 'fx0')
LAYER_ALIGN = 0.9           # |offset . face normal| / |offset| needed: the offset runs along the normal
LAYER_NORMAL_ALIGN = 0.7    # |vertex normal . face normal|: the two sheets are (anti)parallel


def face_normals(pos, tri):
    n = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)


def closest_on_triangles(p, a, b, c):
    """Closest point of triangles (a,b,c) to points p, all (k,3). Returns (q, barycentric (k,3))."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum('ij,ij->i', ab, ap), np.einsum('ij,ij->i', ac, ap)
    bp = p - b
    d3, d4 = np.einsum('ij,ij->i', ab, bp), np.einsum('ij,ij->i', ac, bp)
    cp = p - c
    d5, d6 = np.einsum('ij,ij->i', ab, cp), np.einsum('ij,ij->i', ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    k = len(p)
    bary = np.zeros((k, 3))
    done = np.zeros(k, bool)

    def put(mask, u, v, w):
        nonlocal done
        m = mask & ~done
        bary[m] = np.column_stack([u, v, w])[m] if np.ndim(u) else np.array([u, v, w])
        done |= m
    put((d1 <= 0) & (d2 <= 0), np.ones(k), np.zeros(k), np.zeros(k))
    put((d3 >= 0) & (d4 <= d3), np.zeros(k), np.ones(k), np.zeros(k))
    put((d6 >= 0) & (d5 <= d6), np.zeros(k), np.zeros(k), np.ones(k))
    with np.errstate(divide='ignore', invalid='ignore'):
        v_ab = d1 / (d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), 1 - v_ab, v_ab, np.zeros(k))
        w_ac = d2 / (d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), 1 - w_ac, np.zeros(k), w_ac)
        w_bc = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), np.zeros(k), 1 - w_bc, w_bc)
        den = va + vb + vc
        v_in, w_in = vb / den, vc / den
    put(np.ones(k, bool), 1 - v_in - w_in, v_in, w_in)
    bary = np.nan_to_num(bary)
    q = bary[:, :1] * a + bary[:, 1:2] * b + bary[:, 2:] * c
    return q, bary


def segment_distance(points, segments):
    """Distance from each point to the nearest of the segments (s,2,3)."""
    best = np.full(len(points), np.inf)
    for a, b in segments:
        v = b - a
        u = np.clip((points - a) @ v / max(float(v @ v), 1e-15), 0, 1)
        best = np.minimum(best, np.linalg.norm(points - (a + u[:, None] * v), axis=1))
    return best


def skeleton_segments(world, parent):
    """Bone segments (parent origin -> child origin) of a skeleton's world matrices (row-vector, [3,:3])."""
    return [(world[int(p), 3, :3], world[i, 3, :3]) for i, p in enumerate(parent) if p >= 0
            and np.linalg.norm(world[i, 3, :3] - world[int(p), 3, :3]) > 1e-6]


def find_layers(pos, tri, norm=None, segments=None, thickness=LAYER_THICKNESS, min_gap=LAYER_MIN_GAP,
                align=LAYER_ALIGN, normal_align=LAYER_NORMAL_ALIGN, candidates=None, skin=None,
                min_similarity=LAYER_SIMILARITY, bone_names=None, extremities=EXTREMITY_BONES):
    """Pair inner-shell vertices with the closest point of the shell outside them.

    pos/tri/norm: the SOURCE bind (authored layering). segments: skeleton bone segments in the same
    space, used to tell inner (closer to the bones) from outer. candidates: optional bool mask of
    vertices that may take part (default all).
    Returns dict of arrays: vertex, tri, bary (k,3), offset (signed, along the outer face normal),
    leader (outer triangle vertex with the largest barycentric weight). Empty arrays when none.
    """
    from scipy.spatial import cKDTree
    pos = np.asarray(pos, float); tri = np.asarray(tri, int)
    n = len(pos)
    empty = dict(vertex=np.zeros(0, int), tri=np.zeros(0, int), bary=np.zeros((0, 3)), offset=np.zeros(0),
                 leader=np.zeros(0, int), tri_vertices=np.zeros((0, 3), int))
    if not len(tri):
        return empty
    allowed = np.ones(n, bool) if candidates is None else np.asarray(candidates, bool)
    # Coincident vertices (UV/normal seams) are one surface point: a triangle touching any copy of v
    # is v's own sheet.
    _, group = np.unique(np.round(pos, 6), axis=0, return_inverse=True)
    group = group.ravel()
    fn = face_normals(pos, tri)
    centroid = pos[tri].mean(1)
    radius = float(np.linalg.norm(pos[tri] - centroid[:, None], axis=2).max())
    tree = cKDTree(centroid)
    verts = np.flatnonzero(allowed)
    lists = tree.query_ball_point(pos[verts], thickness + radius)
    vv = np.repeat(verts, [len(x) for x in lists])
    tt = np.fromiter((t for x in lists for t in x), int, count=len(vv))
    keep = allowed[tri[tt]].all(1) & ~(group[tri[tt]] == group[vv][:, None]).any(1)
    vv, tt = vv[keep], tt[keep]
    if not len(vv):
        return empty
    q, bary = closest_on_triangles(pos[vv], pos[tri[tt, 0]], pos[tri[tt, 1]], pos[tri[tt, 2]])
    d = pos[vv] - q
    dist = np.linalg.norm(d, axis=1)
    along = np.einsum('ij,ij->i', d, fn[tt])
    ok = (dist > min_gap) & (dist <= thickness) & (np.abs(along) >= align * dist)
    if norm is not None:
        nv = np.asarray(norm, float)[vv]
        nv = nv / np.maximum(np.linalg.norm(nv, axis=1, keepdims=True), 1e-12)
        ok &= np.abs(np.einsum('ij,ij->i', nv, fn[tt])) >= normal_align
    vv, tt, q, bary, dist, along = vv[ok], tt[ok], q[ok], bary[ok], dist[ok], along[ok]
    if not len(vv):
        return empty
    if skin is not None:
        bi, bw = np.asarray(skin[0]), np.asarray(skin[1], float)
        nb = int(bi.max()) + 1
        dense = np.zeros((n, nb))
        for k in range(bi.shape[1]):
            np.add.at(dense, (np.arange(n), bi[:, k]), bw[:, k])
        dense /= np.maximum(dense.sum(1, keepdims=True), 1e-12)
        outer = np.einsum('kj,kjb->kb', bary, dense[tri[tt]])
        sim = np.minimum(dense[vv], outer).sum(1)
        good = sim >= min_similarity
        if bone_names is not None:
            ext = np.array([any(x in str(nm) for x in extremities) for nm in bone_names] + [False] * (nb - len(bone_names)))[:nb]
            good &= (dense[vv][:, ext].sum(1) < .5) & (outer[:, ext].sum(1) < .5)
        vv, tt, q, bary, dist, along = vv[good], tt[good], q[good], bary[good], dist[good], along[good]
        if not len(vv):
            return empty
    if segments is not None and len(segments):
        sv, sq = segment_distance(pos[vv], segments), segment_distance(q, segments)
        inner = sv < sq - 0.25 * dist     # v is nearer the bones than the surface over it
    else:
        inner = along < 0                 # fall back to: v lies behind the face
    vv, tt, bary, dist, along = vv[inner], tt[inner], bary[inner], dist[inner], along[inner]
    if not len(vv):
        return empty
    order = np.lexsort((dist, vv))
    first = np.r_[True, vv[order][1:] != vv[order][:-1]]
    pick = order[first]
    vv, tt, bary, along = vv[pick], tt[pick], bary[pick], along[pick]
    leader = tri[tt, np.argmax(bary, axis=1)]
    return dict(vertex=vv, tri=tt, bary=bary, offset=along, leader=leader, tri_vertices=tri[tt])


def leader_map(layers, n, group=None):
    """Follower -> outermost leader vertex (chains through middle layers). Identity for others."""
    lead = np.arange(n)
    lead[layers['vertex']] = layers['leader']
    for _ in range(8):                    # three-layer cloth at most needs two hops
        nxt = lead[lead]
        if np.array_equal(nxt, lead):
            break
        lead = nxt
    return lead


def crossings(points, layers, tri, sign=None):
    """Signed-offset check for one posed frame. points (n,3). Returns (count, min ratio s/s0).

    sign: the bind sign of each pair (default sign(layers['offset'])). A pair crosses when its
    posed signed offset is zero or has the opposite sign.
    """
    if not len(layers['vertex']):
        return 0, 1.0
    t = tri[layers['tri']]
    a, b, c = points[t[:, 0]], points[t[:, 1]], points[t[:, 2]]
    q = layers['bary'][:, :1] * a + layers['bary'][:, 1:2] * b + layers['bary'][:, 2:] * c
    fn = np.cross(b - a, c - a)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    s = np.einsum('ij,ij->i', points[layers['vertex']] - q, fn)
    s0 = np.sign(layers['offset']) if sign is None else sign
    rel = s * s0
    ratio = rel / np.maximum(np.abs(layers['offset']), 1e-12)
    return int((rel <= 0).sum()), float(ratio.min())


def crossing_detail(points, layers, tri):
    """Per-pair posed signed offset relative to the bind (for diagnostics)."""
    t = tri[layers['tri']]
    a, b, c = points[t[:, 0]], points[t[:, 1]], points[t[:, 2]]
    q = layers['bary'][:, :1] * a + layers['bary'][:, 1:2] * b + layers['bary'][:, 2:] * c
    fn = np.cross(b - a, c - a)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    return np.einsum('ij,ij->i', points[layers['vertex']] - q, fn) * np.sign(layers['offset'])


def loose_vertices(pos, bone_indices, weights, names, parent, world, limit, exclude=(), anchor_weight=1e-5, blocked=()):
    """Vertices further than `limit` from every influencing bone chain, plus their rest bone.

    Chain = the influencing bone's segments to its parent and children (validate.chain_distance);
    anchor_weight: only influences above it count as the vertex's anchor (a 0.0005 spill-over
    onto the spine is not what a hair strand hangs from). blocked: bone indices never used as rest.
    The rest bone is the nearest bone (by segment distance) that is not an influencing bone, not
    an ancestor-free leaf of the anchor chain, and not in `exclude` (e.g. hands/feet).
    Returns (vertex indices, rest bone per vertex, bind chain distance per vertex).
    """
    parent = np.asarray(parent)
    origin = world[:, 3, :3]

    def segs(i):
        s = [(i, int(parent[i]))] if parent[i] >= 0 else []
        return s + [(i, int(j)) for j in np.where(parent == i)[0]]

    def dist_to_bone(points, i):
        best = np.full(len(points), np.inf)
        for a, b in segs(i):
            best = np.minimum(best, segment_distance(points, [(origin[a], origin[b])]))
        return best
    nb = len(names)
    per_bone = np.stack([dist_to_bone(pos, i) for i in range(nb)], 1)
    influence = np.zeros((len(pos), nb), bool)
    for k in range(bone_indices.shape[1]):
        influence[np.arange(len(pos)), bone_indices[:, k]] |= weights[:, k] > anchor_weight
    chain = np.where(influence, per_bone, np.inf).min(1)
    loose = np.flatnonzero(chain > limit)
    rest = np.full(len(loose), -1)
    blocked = np.array([any(x in n for x in exclude) or not n for n in names]) | np.isin(np.arange(nb), list(blocked))
    for r, v in enumerate(loose):
        cand = np.where(influence[v] | blocked, np.inf, per_bone[v])
        # A bone whose chain ends at an influencing leaf strand is part of the strand, not the body.
        rest[r] = int(np.argmin(cand)) if np.isfinite(cand).any() else -1
    return loose, rest, chain[loose]


# ------------------------------------------------------------------------------- fitter coupling
def coupling_plan(layers, group=None, n=None):
    """Follower rows in dependency order (outer shells first), in `group` space.

    group: vertex -> fitter row (seam groups); identity when None. A follower whose outer triangle
    touches its own row, or that sits in a dependency cycle, is dropped (it stays free).
    Returns dict(rows, tri_rows (k,3), bary (k,3), levels: list of index arrays into rows).
    """
    v = np.asarray(layers['vertex'], int)
    tri_v = np.asarray(layers.get('tri_vertices', np.zeros((0, 3), int)), int)
    if group is None:
        group = np.arange(n if n is not None else (v.max() + 1 if len(v) else 0))
    group = np.asarray(group, int)
    rows, tri_rows, bary, seen = [], [], [], set()
    for k in range(len(v)):
        r = int(group[v[k]]); t = group[tri_v[k]]
        if r in seen or r in t:
            continue
        seen.add(r); rows.append(r); tri_rows.append(t); bary.append(layers['bary'][k])
    rows = np.asarray(rows, int); tri_rows = np.asarray(tri_rows, int).reshape(-1, 3); bary = np.asarray(bary).reshape(-1, 3)
    def depths(active):
        depth = {}
        for _ in range(12):
            changed = False
            for i, r in enumerate(rows.tolist()):
                if not active[i]:
                    continue
                ds = [0 if int(x) not in follower else depth.get(int(x)) for x in tri_rows[i]]
                if any(d is None for d in ds):
                    continue
                d = 1 + max(ds)
                if depth.get(r) != d:
                    depth[r] = d; changed = True
            if not changed:
                break
        return depth
    active = np.ones(len(rows), bool)
    follower = dict(zip(rows.tolist(), range(len(rows))))
    depth = depths(active)
    # Mutual / cyclic pairs (two shells each "inside" the other, e.g. a fold): free one member of
    # each cycle (it leads, the other follows) instead of dropping the whole cycle.
    for _ in range(len(rows)):
        stuck = [i for i, r in enumerate(rows.tolist()) if active[i] and r not in depth]
        if not stuck:
            break
        active[stuck[0]] = False
        follower.pop(int(rows[stuck[0]]), None)
        depth = depths(active)
    keep = np.array([active[i] and r in depth and depth[r] <= 8 for i, r in enumerate(rows.tolist())], bool)
    rows, tri_rows, bary = rows[keep], tri_rows[keep], bary[keep]
    # Rows dropped from the plan are free again; a kept follower may not hang off a dropped one
    # through a cycle (its depth would be undefined), so depth is recomputed on the kept set.
    dvals = np.array([depth[r] for r in rows.tolist()], int)
    levels = [np.flatnonzero(dvals == d) for d in sorted(set(dvals.tolist()))]
    return dict(rows=rows, tri_rows=tri_rows, bary=bary, levels=levels)


def blend_dense(dense, plan):
    """dense (rows, bones): follower rows := barycentric blend of their outer triangle (in order)."""
    dense = np.array(dense, float, copy=True)
    for lv in plan['levels']:
        r, t, b = plan['rows'][lv], plan['tri_rows'][lv], plan['bary'][lv]
        dense[r] = np.einsum('kj,kjb->kb', b, dense[t])
    return dense


class WeightTie:
    """Tie follower rows' four lane weights to the blend of their outer triangle's weights.

    forward(w) -> effective weights (followers replaced, renormalised over their lanes);
    backward(g) -> gradient with respect to the free rows' weights (followers' own rows get 0).
    """

    def __init__(self, plan, lanes, bones):
        self.plan, self.lanes, self.bones = plan, np.asarray(lanes, int), int(bones)

    def _dense(self, w):
        n = len(w); D = np.zeros((n, self.bones))
        np.add.at(D, (np.repeat(np.arange(n), w.shape[1]), self.lanes.ravel()), w.ravel())
        return D

    def forward(self, w):
        w = np.array(w, float, copy=True)
        if not len(self.plan['rows']):
            self.cache = None
            return w
        D = self._dense(w); cache = []
        for lv in self.plan['levels']:
            r, t, b = self.plan['rows'][lv], self.plan['tri_rows'][lv], self.plan['bary'][lv]
            blend = np.einsum('kj,kjb->kb', b, D[t])
            u = np.take_along_axis(blend, self.lanes[r], axis=1)
            S = np.maximum(u.sum(1, keepdims=True), 1e-12)
            wf = u / S
            w[r] = wf
            D[r] = 0
            np.add.at(D, (np.repeat(r, wf.shape[1]), self.lanes[r].ravel()), wf.ravel())
            cache.append((lv, S, wf))
        self.cache = cache
        return w

    def backward(self, g):
        g = np.array(g, float, copy=True)
        if not self.cache:
            return g
        gD = np.zeros((len(g), self.bones))
        for lv, S, wf in reversed(self.cache):
            r, t, b = self.plan['rows'][lv], self.plan['tri_rows'][lv], self.plan['bary'][lv]
            gw = g[r] + np.take_along_axis(gD[r], self.lanes[r], axis=1)
            gD[r] = 0; g[r] = 0
            du = (gw - (gw * wf).sum(1, keepdims=True)) / S
            gb = np.zeros((len(r), self.bones))
            np.put_along_axis(gb, self.lanes[r], du, axis=1)
            np.add.at(gD, t.ravel(), (b[:, :, None] * gb[:, None, :]).reshape(-1, self.bones))
        g += np.take_along_axis(gD, self.lanes, axis=1)
        follower = np.zeros(len(g), bool); follower[self.plan['rows']] = True
        g[follower] = 0
        return g


def tie_lanes(plan, initial, lanes, bones):
    """Pick each follower's four lanes from the blended initial weights. Returns (lanes, initial)."""
    lanes = np.array(lanes, int, copy=True); initial = np.array(initial, float, copy=True)
    if not len(plan['rows']):
        return lanes, initial
    n = len(initial); D = np.zeros((n, bones))
    np.add.at(D, (np.repeat(np.arange(n), initial.shape[1]), lanes.ravel()), initial.ravel())
    D = blend_dense(D, plan)
    r = plan['rows']
    top = np.argsort(D[r], axis=1)[:, -lanes.shape[1]:][:, ::-1]
    lanes[r] = top
    w = np.maximum(np.take_along_axis(D[r], top, axis=1), 0)
    w[w.sum(1) <= 0, 0] = 1
    initial[r] = w / w.sum(1, keepdims=True)
    return lanes, initial


# ------------------------------------------------------------------------------- crossing repair
def check_plan(layers, group):
    """Every detected pair in fitter-row space (row, tri_rows, bary, bind sign), for repair checks."""
    v = np.asarray(layers['vertex'], int)
    if not len(v):
        return dict(rows=np.zeros(0, int), tri_rows=np.zeros((0, 3), int), bary=np.zeros((0, 3)), sign=np.zeros(0),
                    offset=np.zeros(0))
    group = np.asarray(group, int)
    rows = group[v]; tri_rows = group[np.asarray(layers['tri_vertices'], int)]
    keep = ~(tri_rows == rows[:, None]).any(1)
    return dict(rows=rows[keep], tri_rows=tri_rows[keep], bary=np.asarray(layers['bary'])[keep],
                sign=np.sign(np.asarray(layers['offset']))[keep], offset=np.asarray(layers['offset'])[keep])


def posed_crossings(posed, checks, index_of):
    """posed (m, frames, 3) for the rows listed by index_of (row -> position in posed).
    Returns bool per check: crosses in any frame."""
    if not len(checks['rows']):
        return np.zeros(0, bool)
    f = posed[index_of[checks['rows']]]
    t = posed[index_of[checks['tri_rows']]]                      # (k,3,frames,3)
    b = checks['bary'][:, :, None, None]
    q = (b * t).sum(1)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    s = np.einsum('kfi,kfi->kf', f - q, n) * checks['sign'][:, None]
    return (s <= 0).any(1)


def repair_crossings(dense, checks, posed_fn, rounds=6):
    """Close residual crossings exactly: a crossing pair's inner row and its three outer rows get one
    shared (averaged) influence vector, so that patch moves by a single blend and the inset cannot
    invert. Repeats until no check crosses (or `rounds`). posed_fn(dense, rows) -> (len(rows), frames, 3).
    Returns (dense, report)."""
    dense = np.array(dense, float, copy=True)
    report = dict(rounds=0, crossing_pairs_before=None, crossing_pairs_after=None, unified_rows=0)
    if not len(checks['rows']):
        report.update(crossing_pairs_before=0, crossing_pairs_after=0)
        return dense, report
    rows = np.unique(np.r_[checks['rows'], checks['tri_rows'].ravel()])
    index_of = np.full(len(dense), -1); index_of[rows] = np.arange(len(rows))
    unified = set()
    best = None
    for r in range(rounds + 1):
        bad = posed_crossings(posed_fn(dense, rows), checks, index_of)
        if report['crossing_pairs_before'] is None:
            report['crossing_pairs_before'] = int(bad.sum())
        if best is not None and bad.sum() >= best[0]:
            # A round that does not reduce the count is undone: unifying a patch also changes the
            # outer surface other pairs sit behind. Stop with the best state found.
            dense, unified = best[1], best[2]
            report['crossing_pairs_after'] = int(best[0])
            break
        report['crossing_pairs_after'] = int(bad.sum())
        if not bad.any() or r == rounds:
            break
        best = (int(bad.sum()), dense.copy(), set(unified))
        report['rounds'] = r + 1
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                x = parent[x]
            return x
        for k in np.flatnonzero(bad):
            members = [int(checks['rows'][k])] + [int(x) for x in checks['tri_rows'][k]]
            root = find(members[0])
            for m in members[1:]:
                parent[find(m)] = root
        sets = {}
        for m in parent:
            sets.setdefault(find(m), set()).add(m)
        for root, s in sets.items():
            s = sorted(s | {root})
            lanes, w = lanes_from_dense(dense[s].mean(0, keepdims=True))
            row = to_dense(lanes, w, dense.shape[1])[0]
            dense[s] = row                    # one truncated four-influence vector for the patch
            unified.update(s)
    report['unified_rows'] = len(unified)
    return dense, report


def lanes_from_dense(dense, k=4):
    lanes = np.argsort(dense, axis=1)[:, -k:][:, ::-1]
    w = np.maximum(np.take_along_axis(dense, lanes, axis=1), 0)
    w[w.sum(1) <= 0, 0] = 1
    return lanes, w / w.sum(1, keepdims=True)


def to_dense(lanes, weights, bones):
    n = len(lanes); D = np.zeros((n, bones))
    np.add.at(D, (np.repeat(np.arange(n), lanes.shape[1]), np.asarray(lanes).ravel()), np.asarray(weights).ravel())
    return D


# ------------------------------------------------------------------------- offset-preserving term
LAYER_MARGIN = 0.3          # fitter keeps at least 30% of each authored inset (the gate needs > 0)
LAYER_WEIGHT = 1.0
LAYER_SCALE = 0.25          # penalty in units of max(inset, 0.25): a 0.02 decal inset weighs like 0.25


def layer_penalty(posed, checks, margin=LAYER_MARGIN):
    """Offset-preserving constraint for the fitters: hinge on each pair's posed signed inset.

    posed (rows, frames, 3). loss = sum(max(0, margin*|s0| - s)^2 / |s0|^2); gradient w.r.t. posed
    (the face-normal derivative is dropped: the offset runs along the normal, so its contribution is
    second order). Returns (loss, grad, crossing pairs (s <= 0 in any frame), min s/|s0|)."""
    grad = np.zeros_like(posed)
    if not len(checks['rows']):
        return 0., grad, 0, 1.
    r, t, b = checks['rows'], checks['tri_rows'], checks['bary']
    tp = posed[t]                                               # (k,3,frames,3)
    q = np.einsum('kj,kjfi->kfi', b, tp)
    e1, e2 = tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0]
    c = np.cross(e1, e2)
    cl = np.maximum(np.linalg.norm(c, axis=-1, keepdims=True), 1e-12)
    n = c / cl
    sign = checks['sign'][:, None]
    d = posed[r] - q
    dn = np.einsum('kfi,kfi->kf', d, n)
    s = dn * sign
    a = np.maximum(np.abs(checks['offset']), 1e-6)[:, None]
    over = np.maximum(margin * a - s, 0)
    scale = np.maximum(a, LAYER_SCALE)                           # thin insets do not dominate the fit
    loss = float(((over / scale) ** 2).sum())
    w = (-2 * over / scale ** 2 * sign)[:, :, None]              # dL/d(d.n)
    np.add.at(grad, r, w * n)
    u = (d - dn[:, :, None] * n) / cl                            # d(d.n)/dc
    g1, g2 = np.cross(e2, u), np.cross(u, e1)                    # exact normal derivative
    for j, extra in enumerate([-(g1 + g2), g1, g2]):
        np.add.at(grad, t[:, j], w * (extra - b[:, j, None, None] * n))
    return loss, grad, int((s <= 0).any(1).sum()), float((s / a).min())


def source_layers(source):
    """The one detection every fitter and validator uses: layer pairs of a source model's bind
    (pos/tri/norm/world/parent/bone_indices/weights/names, MUA1 Model or MUA2 Source)."""
    return find_layers(source.pos, source.tri, source.norm, skeleton_segments(source.world, source.parent),
                       skin=(source.bone_indices, source.weights), bone_names=list(source.names))


def place_inner_shells(pos, src_pos, layers, n):
    """Bind offset preservation: put every inner-shell vertex at its outer triangle's refitted
    barycentric point plus the authored source offset, carried by the triangle's own frame map
    (edges -> refitted edges, normal -> refitted normal scaled by sqrt(area ratio)). The inset can
    rotate and scale with the outer surface but keeps its side exactly. Returns (pos, moved count)."""
    pos = np.array(pos, float, copy=True)
    plan = coupling_plan(layers, None, n)
    moved = 0
    for lv in plan['levels']:
        r, t, b = plan['rows'][lv], plan['tri_rows'][lv], plan['bary'][lv]
        S, D = src_pos[t], pos[t]
        e1s, e2s, e1d, e2d = S[:, 1] - S[:, 0], S[:, 2] - S[:, 0], D[:, 1] - D[:, 0], D[:, 2] - D[:, 0]
        ns, nd = np.cross(e1s, e2s), np.cross(e1d, e2d)
        As, Ad = np.linalg.norm(ns, axis=1), np.linalg.norm(nd, axis=1)
        ok = (As > 1e-12) & (Ad > 1e-12)
        ns = ns / np.maximum(As, 1e-12)[:, None]; nd = nd / np.maximum(Ad, 1e-12)[:, None]
        scale = np.sqrt(np.maximum(Ad, 1e-12) / np.maximum(As, 1e-12))
        src_frame = np.stack([e1s, e2s, ns], 2)                         # columns
        dst_frame = np.stack([e1d, e2d, nd * scale[:, None]], 2)
        off = src_pos[r] - np.einsum('kj,kji->ki', b, S)
        coeff = np.linalg.solve(np.where(ok[:, None, None], src_frame, np.eye(3)), off[:, :, None])[:, :, 0]
        new = np.einsum('kj,kji->ki', b, D) + np.einsum('kij,kj->ki', dst_frame, coeff)
        pos[r[ok]] = new[ok]; moved += int(ok.sum())
    return pos, moved


# --------------------------------------------------------------------------------- one-leg coat rule
LEG_PARTS = (' Thigh', ' Calf', ' Foot', ' Toe0')


def coat_one_leg(slot_names, coat, left, weights, indices):
    """Coat rows follow ONE leg, as the port's own XML1 Gambit coat does (no stock vertex is weighted >0.05 to
    both legs). A coat-surface weight transfer blends the nearest stock vertices, which straddle the legs at a
    panel's centre; a row blended across both legs lands between them, away from every segment of its own
    bones, when the legs separate (the chain distance over stock). For coat rows the far leg's weight moves to
    the mirrored bone of the near leg (`left`: row hangs on the L side), then the top lanes are kept.

    slot_names: bone name per palette slot; indices: palette slots per lane. Returns (weights, indices, report).
    """
    slot = {nm: k for k, nm in enumerate(slot_names)}
    mirror = {}
    for k, nm in enumerate(slot_names):
        for a, b in ((' L ', ' R '), (' R ', ' L ')):
            if a in nm and nm.endswith(LEG_PARTS) and nm.replace(a, b) in slot:
                mirror[k] = (a.strip(), slot[nm.replace(a, b)])
    weights = np.asarray(weights, float); indices = np.asarray(indices)
    n, P = len(weights), len(slot_names)
    dense = to_dense(indices, weights, P)
    coat = np.asarray(coat, bool); left = np.asarray(left, bool)
    moved, rows = 0.0, set()
    for k, (side, other) in mirror.items():
        far = coat & ((left & (side == 'R')) | (~left & (side == 'L'))) & (dense[:, k] > 0)
        if far.any():
            moved += float(dense[far, k].sum()); rows.update(np.flatnonzero(far).tolist())
            dense[far, other] += dense[far, k]; dense[far, k] = 0
    lanes = indices.shape[1]
    ix = np.argsort(dense, axis=1)[:, -lanes:][:, ::-1].copy()
    w = np.maximum(np.take_along_axis(dense, ix, axis=1), 0)
    w[w.sum(1) <= 0, 0] = 1
    w /= w.sum(1, keepdims=True)
    return w, ix.astype(indices.dtype), dict(coat_rows=int(coat.sum()), rows_changed=len(rows), weight_moved=moved)
