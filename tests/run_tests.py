"""Headless checks for let-there-be.

Run with:  python tests/run_tests.py
Needs Python 3.12 with the playwright package and an installed Edge.
"""
import itertools
import pathlib
import sys
import traceback

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
URL = INDEX.as_uri()
SHOTS = ROOT / "test-results"

FIRE, CRIME, GOV = "unlock fire", "introduce crime", "make a government"
SEEDS = [1, 2, 3, 4, 5]
YEAR = 12 * 120

HELPERS = r"""
(() => {
  const summarize = (sim) => {
    const st = sim.state;
    const alive = st.villagers.filter(v => v.alive);
    let tot = 0, totAll = 0;
    alive.forEach(v => { for (const k of Object.keys(v.feel)) { totAll += v.feel[k]; const o = st.villagers[+k]; if (o && o.alive) tot += v.feel[k]; } });
    const gr = st.objects.find(o => o.def === 'granary');
    return {
      alive: alive.length, total: st.villagers.length,
      behaviors: Object.assign({}, st.stats.behaviors),
      events: Object.assign({}, st.stats.events),
      deaths: st.stats.deaths, burned: st.stats.burned,
      avgFeel: alive.length > 1 ? tot / (alive.length * (alive.length - 1)) : 0,
      avgFeelAll: alive.length > 1 ? totAll / (alive.length * (alive.length - 1)) : 0,
      hash: sim.hash(),
      thieves: alive.filter(v => v.traits.thief).length,
      chiefs: alive.filter(v => v.roles.indexOf('chief') >= 0).length,
      granary: gr ? gr.stock : null,
      campfires: st.objects.filter(o => o.def === 'campfire').length,
      tick: st.tick,
    };
  };
  window.__summarize = summarize;
  // plain packs used by the scenario checks
  window.__packs = {
    orchards: {
      id: 'orchards', title: 'Orchards', narration: 'Forty orchards appear, each with its own wasps.',
      objects: [{ id: 'orchard', label: 'Orchard', color: '#c04a2b', place: { count: 40, near: 'anywhere' },
        stock: { item: 'food', amount: 6, max: 12, regrowPerDay: 6 }, respawn: { perDay: 3, max: 40 } }],
      behaviors: [{ id: 'pick_fruit', label: 'Picking fruit', when: [{ item: 'food', op: '<', value: 3 }], priority: 21,
        urgency: { need: 'hunger', scale: 0.6 },
        target: { kind: 'object', object: 'orchard', filter: [{ stock: true, op: '>=', value: 1 }], pick: 'nearest' },
        duration: 8, do: [{ fromStock: 'food', amount: 2 }, { health: -3 }] }],
      drawback: { label: 'Wasps', text: 'Every orchard is full of wasps, and they sting.', rules: ['pick_fruit'] }
    },
    swarm: {
      id: 'swarm', title: 'Swarm', narration: 'Four hundred people turn up at once.', newcomers: { count: 400 },
      behaviors: [{ id: 'grumble', label: 'Grumbling', priority: 5, target: { kind: 'self' }, duration: 2, do: [] }],
      drawback: { label: 'Crowding', text: 'Everyone is crowded and hungry.', rules: ['grumble'] }
    }
  };
  // run one scenario for its full length (or until it ends) with an optional pack, and report
  window.__scen = (seed, scenario, pack) => {
    const sim = LTB.createSim({ seed, scenario });
    if (pack) {
      const r = sim.applyPack(window.__packs[pack]);
      if (!r.ok) throw new Error(pack + ': ' + r.errors.join('; '));
      sim.useCommand();
    }
    const pops = [];
    let below120 = null;
    for (let d = 0; d < 144 && sim.state.run.status === 'playing'; d++) {
      sim.tick(120);
      if (d % 12 === 11) pops.push(sim.state.alive);
      if (below120 === null && pack === 'swarm' && sim.state.alive < 120) below120 = d + 1;
    }
    const r = sim.state.run;
    return { seed, status: r.status, cause: r.cause, day: sim.state.tick / 120, score: r.score, alive: sim.state.alive,
      pops, below120, commandsUsed: r.commandsUsed, breakdown: r.breakdown };
  };
  window.__run = (seed, names, ticks) => {
    const sim = LTB.createSim({ seed });
    for (const n of names) {
      const r = sim.applyPack(LTB.PACKS[n]);
      if (!r.ok) throw new Error(n + ': ' + r.errors.join('; '));
    }
    sim.tick(ticks);
    return summarize(sim);
  };
})();
"""

results = []


def check(name, fn):
    try:
        out = fn()
        ok, detail = out if isinstance(out, tuple) else (bool(out), "")
    except Exception as e:  # noqa: BLE001
        ok, detail = False, "exception: " + str(e).splitlines()[0][:300]
        traceback.print_exc(file=sys.stderr)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" - " + detail if detail else ""), flush=True)


def new_page(browser, errors, query="?seed=3&mode=free"):
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
    page.goto(URL + query)
    page.wait_for_function("window.LTB && window.LTB.game")
    page.evaluate(HELPERS)
    return page


def run_many(page, seeds, names, ticks):
    return page.evaluate("([seeds, names, ticks]) => seeds.map(s => window.__run(s, names, ticks))", [seeds, names, ticks])


def total(rs, group, key):
    return sum(r[group].get(key, 0) for r in rs)


def seeds_with(rs, group, key):
    return sum(1 for r in rs if r[group].get(key, 0) > 0)


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = new_page(browser, errors)

        # 1. page loads
        def t1():
            n = page.evaluate("LTB.game.sim.state.villagers.length")
            box = page.locator("#cmd")
            ph = box.get_attribute("placeholder")
            return (n == 50 and box.count() == 1 and ph == "Say the word..." and not errors,
                    f"villagers={n}, errors={errors[:3]}")
        check("1 page loads cleanly with 50 villagers and a text box", t1)

        # 2. baseline
        base = run_many(page, SEEDS, [], 3 * YEAR)

        def t2():
            avg = sum(r["alive"] for r in base) / len(base)
            ran = {k: seeds_with(base, "behaviors", k) for k in ("eat", "gather", "sleep", "chat")}
            return (avg >= 45 and all(v == len(base) for v in ran.values()),
                    f"avg alive {avg:.1f}/50 {[r['alive'] for r in base]}, seeds where ran {ran}")
        check("2 baseline: villagers survive 3 years and the base behaviours run", t2)

        # 3. determinism
        def t3():
            r = page.evaluate("""() => {
              const run = (seed) => window.__run(seed, ['unlock fire', 'introduce crime', 'make a government'], 1200).hash;
              return [run(11), run(11), run(12)];
            }""")
            return (r[0] == r[1] and r[0] != r[2], f"{r}")
        check("3 determinism: same seed same hash, other seed different", t3)

        # 4. fire
        fire = run_many(page, SEEDS, [FIRE], 3 * YEAR)

        def t4():
            cook = total(fire, "behaviors", "cook")
            base_cook = total(base, "behaviors", "cook")
            burned = [r["burned"] for r in fire]
            camp = [r["campfires"] for r in fire]
            ok = (cook > 0 and base_cook == 0 and any(b > 0 for b in burned)
                  and all(r["alive"] > 0 for r in fire) and all(c > 0 for c in camp))
            return ok, f"cook={cook} (baseline {base_cook}), burned per seed {burned}, alive {[r['alive'] for r in fire]}, campfires {camp}"
        check("4 fire: campfires, cooking, burning, survival", t4)

        # 5. crime
        crime = run_many(page, SEEDS, [CRIME], 3 * YEAR)

        def t5():
            start = run_many(page, SEEDS, [CRIME], 0)
            th = [r["thieves"] / r["total"] for r in start]
            cnt = {k: seeds_with(crime, "behaviors", k) for k in ("steal", "confront", "brawl")}
            fc = sum(r["avgFeel"] for r in crime) / len(crime)
            fb = sum(r["avgFeel"] for r in base) / len(base)
            thieves_left = sum(1 for r in crime if r["thieves"] > 0)
            ok = all(0.1 <= x <= 0.2 for x in th) and all(v >= 3 for v in cnt.values()) and fc < fb and thieves_left >= 4
            return ok, (f"thief share at start {th[0]:.2f}, thieves alive after 3y {[r['thieves'] for r in crime]}, "
                        f"alive {[r['alive'] for r in crime]}, seeds where ran {cnt}, avg feeling {fc:.2f} vs baseline {fb:.2f}")
        check("5 crime: thieves, steal/confront/brawl, lower feelings", t5)

        # 6. government
        gov = run_many(page, SEEDS, [GOV], 5 * YEAR)
        gov0 = run_many(page, SEEDS, [GOV], 0)

        def t6():
            one = all(r["chiefs"] == 1 for r in gov0)
            end_one = [r["chiefs"] for r in gov]
            tax, skim = total(gov, "behaviors", "pay_tax"), total(gov, "behaviors", "skim")
            changed = sum(1 for r in gov if r["granary"] is not None and r["granary"] != 10)
            revolts = [r["events"].get("revolt", 0) for r in gov]
            forced = page.evaluate("""() => {
              const s = LTB.createSim({ seed: 1 });
              s.applyPack(LTB.PACKS['make a government']);
              const old = s.state.villagers.find(v => v.roles.indexOf('chief') >= 0);
              s.state.villagers.forEach(v => { v.feel[old.id] = -50; });
              s.tick(121);
              const now = s.state.villagers.filter(v => v.roles.indexOf('chief') >= 0);
              return { revolts: s.state.stats.events.revolt || 0, one: now.length === 1, changed: now.length === 1 && now[0].id !== old.id };
            }""")
            mech = forced["revolts"] == 1 and forced["one"] and forced["changed"]
            ok = one and tax > 0 and skim > 0 and changed >= 1 and mech and sum(1 for x in revolts if x > 0) >= 3
            return ok, (f"chief at start {one}, chiefs at end {end_one}, pay_tax={tax}, skim={skim}, granary changed in {changed}/5, "
                        f"natural revolts in 5 years {revolts} forced revolt mechanism works: {mech}")
        check("6 government: chief, tax, skim, granary, revolt", t6)

        # 7. combinations
        def t7():
            names = [FIRE, CRIME, GOV]
            out = page.evaluate("""([perms, seeds]) => {
              const res = [];
              for (const perm of perms) for (const s of seeds) {
                try { const r = window.__run(s, perm, 2 * 1440); res.push({ perm: perm.join(' > '), err: null, punish: r.behaviors.punish || 0, alive: r.alive }); }
                catch (e) { res.push({ perm: perm.join(' > '), err: String(e) }); }
              }
              return res;
            }""", [[list(x) for x in itertools.permutations(names)], [1, 2]])
            errs = [o for o in out if o["err"]]
            punish = sum(o["punish"] for o in out if not o["err"] and "introduce crime" in o["perm"] and "make a government" in o["perm"])
            return (not errs and len(out) == 12 and punish > 0, f"{len(out)} runs, errors {errs[:2]}, punish ran {punish} times")
        check("7 combination: all 6 orderings run, punish happens", t7)

        # 8. validation
        def t8():
            out = page.evaluate(r"""() => {
              const sim = LTB.createSim({ seed: 3 });
              sim.applyPack(LTB.PACKS['unlock fire']);
              sim.tick(100);
              const good = () => ({
                id: 'probe', title: 'Probe', narration: 'A probe.',
                traits: [{ id: 'probed', label: 'Probed', share: 0.1 }],
                behaviors: [{ id: 'probing', label: 'Probing', priority: 5, target: { kind: 'self' }, duration: 2, do: [] }],
                drawback: { label: 'Poking', text: 'Probing is annoying.', rules: ['probing'] }
              });
              const cases = {};
              const add = (name, mut, re) => { const p = good(); mut(p); cases[name] = { p, re }; };
              add('no drawback', p => { delete p.drawback; }, /drawback/i);
              add('empty drawback rules', p => { p.drawback.rules = ['nothing_here']; }, /drawback/i);
              add('unknown effect', p => { p.behaviors[0].do = [{ explode: 1 }]; }, /not an effect/i);
              add('missing object', p => { p.behaviors[0].target = { kind: 'object', object: 'unicorn', pick: 'nearest' }; }, /unicorn/);
              add('duplicate id', p => { p.behaviors[0].id = 'eat'; p.drawback.rules = ['eat']; }, /already exists/i);
              add('oversized place.count', p => { p.objects = [{ id: 'bigthing', label: 'Big', color: '#ffffff', place: { count: 201, near: 'anywhere' } }]; }, /count/);
              add('non-finite number', p => { p.behaviors[0].priority = Infinity; }, /finite/i);
              add('NaN number', p => { p.behaviors[0].duration = NaN; }, /finite/i);
              add('bad sprite row', p => { p.objects = [{ id: 'sprited', label: 'Sprited', sprite: { palette: ['#000000'], rows: ['00000000','00000000','00000000','00000000','00000000','00000000','00000000','0000000'] } }]; p.drawback.rules = ['sprited']; }, /sprite/i);
              add('bad palette index', p => { p.objects = [{ id: 'sprited', label: 'Sprited', sprite: { palette: ['#000000'], rows: ['00000000','00000000','00000000','00000000','00000000','00000000','00000000','00000009'] } }]; p.drawback.rules = ['sprited']; }, /palette index/i);
              add('unknown top-level field', p => { p.script = 'x'; }, /unknown field/i);
              add('not an object', () => {}, /object/i);
              cases['not an object'].p = [1, 2];
              const res = [];
              for (const [name, c] of Object.entries(cases)) {
                const before = sim.hash();
                const r = sim.applyPack(c.p);
                res.push({ name, ok: r.ok, match: c.re.test(r.errors.join(' | ')), errors: r.errors.slice(0, 2), same: sim.hash() === before });
              }
              // the baseline probe really is valid, on a fresh sim
              const fresh = LTB.createSim({ seed: 3 });
              const v = fresh.applyPack(good());
              return { res, goodOk: v.ok, goodErrors: v.errors };
            }""")
            bad = [r for r in out["res"] if r["ok"] or not r["same"] or not r["match"]]
            return (out["goodOk"] and not bad, f"{len(out['res'])} bad packs checked; good probe ok={out['goodOk']} {out['goodErrors']}; problems {bad[:2]}")
        check("8 validation rejects bad packs and changes nothing", t8)

        # 9. UI flow
        ui_errors = []
        ui = new_page(browser, ui_errors)

        def t9():
            ui.click('#speeds button[data-speed="0"]')
            cmd = ui.locator("#cmd")
            cmd.fill("unlock fire")
            cmd.press("Enter")
            ui.wait_for_timeout(200)
            log = ui.inner_text("#log")
            packs = ui.inner_text("#packs")
            camp = ui.evaluate("LTB.game.sim.state.objects.filter(o => o.def === 'campfire').length")
            ok1 = "immediately hold it near" in log and "Wildfires" in packs and camp == 3
            cmd.fill("unlock fire")
            cmd.press("Enter")
            ui.wait_for_timeout(200)
            first = ui.locator("#log li").first
            ok2 = "Already done" in first.inner_text() and "refusal" in (first.get_attribute("class") or "")
            cmd.fill("make it rain frogs")
            cmd.press("Enter")
            ui.wait_for_timeout(200)
            visible = ui.is_visible("#ask")
            prompt = ui.input_value("#prompt")
            instr = ui.evaluate("LTB.AI_INSTRUCTIONS")
            ok3 = visible and instr in prompt and "make it rain frogs" in prompt and ui.is_visible("#copy")
            h0 = ui.evaluate("LTB.game.sim.hash()")
            ui.fill("#paste", '{"id":"oops","title":"Oops","narration":"Oops."}')
            ui.click("#apply")
            nerr = ui.locator("#errors li").count()
            ok4 = nerr > 0 and ui.evaluate("LTB.game.sim.hash()") == h0 and ui.is_visible("#ask")
            ui.fill("#paste", "not json at all")
            ui.click("#apply")
            ok4b = ui.locator("#errors li").count() == 1 and ui.evaluate("LTB.game.sim.hash()") == h0
            good = ('{"id":"tinyhats","title":"Tiny hats","narration":"Everyone gets a tiny hat.",'
                    '"traits":[{"id":"hatted","label":"Hatted","share":0.5}],'
                    '"behaviors":[{"id":"tip_hat","label":"Tipping hat","who":{"trait":"hatted"},"priority":10,'
                    '"target":{"kind":"self"},"duration":2,"do":[]}],'
                    '"drawback":{"label":"Hat hair","text":"Hats cause hat hair.","rules":["tip_hat"]}}')
            ui.fill("#paste", "Here you go:\n```json\n" + good + "\n```")
            ui.click("#apply")
            ui.wait_for_timeout(200)
            ok5 = (not ui.is_visible("#ask") and "Tiny hats" in ui.inner_text("#packs")
                   and "tiny hat" in ui.inner_text("#log")
                   and ui.evaluate("LTB.game.sim.world.packs.some(p => p.id === 'tinyhats')"))
            return ok1 and ok2 and ok3 and ok4 and ok4b and ok5, f"builtin={ok1} refusal={ok2} ask={ok3} invalid={ok4}/{ok4b} valid={ok5}"
        check("9 UI flow: built-in, refusal, ask panel, invalid and valid paste", t9)

        # 10. safety
        def t10():
            bad = "<img src=x onerror=window.__pwned=1>"
            ui.fill("#cmd", "something with markup")
            ui.press("#cmd", "Enter")
            pack = {
                "id": "markup", "title": bad, "narration": bad + " narrated.",
                "behaviors": [{"id": "spam", "label": bad, "priority": 100, "when": [{"chance": 1}],
                               "target": {"kind": "self"}, "duration": 1, "do": [{"log": bad + " {actor}"}]}],
                "drawback": {"label": bad, "text": bad, "rules": ["spam"]},
            }
            import json
            ui.fill("#paste", json.dumps(pack))
            ui.click("#apply")
            ui.evaluate("LTB.game.sim.tick(400)")
            ui.wait_for_timeout(300)
            imgs = ui.evaluate("document.querySelectorAll('img').length")
            pwned = ui.evaluate("typeof window.__pwned")
            logtext = ui.inner_text("#log")
            shown = "<img src=x onerror=window.__pwned=1>" in logtext
            src = INDEX.read_text(encoding="utf-8")
            clean = "eval(" not in src and "new Function" not in src
            return (imgs == 0 and pwned == "undefined" and shown and clean,
                    f"img elements={imgs}, __pwned={pwned}, markup shown as text={shown}, source clean={clean}")
        check("10 safety: pack text stays text, no eval or new Function", t10)

        # 12. gifts and remote actions
        def t12():
            r = page.evaluate("""() => {
              const pack = {
                id: 'zappers', title: 'Zappers', narration: 'Zap.',
                items: [{ id: 'zapper', label: 'Zapper' }],
                gifts: [{ item: 'zapper', amount: 2 }],
                objects: [{ id: 'scorch', label: 'Scorch mark', color: '#222222', place: { count: 0, near: 'anywhere' } }],
                behaviors: [{ id: 'zap', label: 'Zapping', when: [{ item: 'zapper', op: '>=', value: 1 }], priority: 99, duration: 1,
                  target: { kind: 'villager', pick: 'random', remote: true },
                  do: [{ spawn: 'scorch', at: 'target' }, { item: 'zapper', by: -1 }] }],
                drawback: { label: 'Scorch marks', text: 'Everything is scorched.', rules: ['zap'] }
              };
              const s = LTB.createSim({ seed: 5 });
              const res = s.applyPack(pack);
              const v = s.state.villagers;
              const gifted = v.filter(x => x.items.zapper === 2).length;
              s.tick(40);
              const zaps = s.state.stats.behaviors.zap || 0;
              const walkers = s.state.villagers.filter(x => x.cur && x.cur.b === 'zap' && x.cur.phase === 'move').length;
              return { ok: res.ok, errs: res.errors, gifted, zaps, marks: s.state.objects.filter(o => o.def === 'scorch').length, walkers };
            }""")
            ok = r["ok"] and r["gifted"] == 50 and r["zaps"] > 0 and r["marks"] == r["zaps"] and r["walkers"] == 0
            return ok, str(r)
        check("12 gifts reach everyone and remote actions need no walking", t12)

        # 13. new validation and stated limits
        def t13():
            r = page.evaluate("""() => {
              const db = { label: 'x', text: 'x', rules: ['b1'] };
              const beh = (over) => Object.assign({ id: 'b1', label: 'x', priority: 1, duration: 1, target: { kind: 'self' }, do: [] }, over);
              const bad = [
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh()], gifts: [{ item: 'nothing', amount: 1 }] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh()], gifts: [{ item: 'food', amount: 0 }] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh()], gifts: [{ item: 'food', amount: 1, share: 2 }] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh({ target: { kind: 'self', remote: true } })] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh({ target: { kind: 'villager', remote: 'yes' } })] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh({ do: [{ spawn: 'bush', at: 'target' }] })] },
                { id: 'a', title: 'a', narration: 'a', drawback: db, behaviors: [beh({ target: { kind: 'villager' }, do: [{ spawn: 'bush', at: 'moon' }] })] },
              ];
              const s = LTB.createSim({ seed: 2 }), h = s.hash();
              const accepted = bad.map((p, i) => s.applyPack(p).ok ? i : -1).filter(i => i >= 0);
              const text = LTB.AI_INSTRUCTIONS;
              const stated = ['## Limits', '`chancePerDay` 0 to 10', '`gifts`', '`remote: true`', 'up to 40 points'].filter(x => text.indexOf(x) < 0);
              return { accepted, same: s.hash() === h, missing: stated };
            }""")
            return (not r["accepted"] and r["same"] and not r["missing"]), str(r)
        check("13 new fields are validated and limits are in the AI instructions", t13)

        # 14. small behaviours are not crowded out
        def t14():
            r = page.evaluate("""() => {
              const small = { id: 'hum', title: 'Humming', narration: 'They hum.',
                behaviors: [{ id: 'hum_tune', label: 'Humming', when: [{ chance: 0.08 }], priority: 16, duration: 8, cooldownDays: 1,
                  target: { kind: 'self' }, do: [] }],
                drawback: { label: 'Noise', text: 'It is annoying.', rules: ['hum_tune'] } };
              return [1, 2, 3, 4, 5].map(sd => {
                const s = LTB.createSim({ seed: sd });
                ['unlock fire', 'introduce crime', 'make a government'].forEach(k => s.applyPack(LTB.PACKS[k]));
                s.applyPack(small);
                s.tick(1440);
                return s.state.stats.behaviors.hum_tune || 0;
              });
            }""")
            return all(x >= 20 for x in r), f"hum_tune in 1 year with all packs active: {r}"
        check("14 small behaviours still happen with every pack active", t14)

        # 11. speed
        def t11():
            r = page.evaluate("""() => {
              const t0 = performance.now();
              const sim = LTB.createSim({ seed: 9 });
              for (const n of ['unlock fire', 'introduce crime', 'make a government']) sim.applyPack(LTB.PACKS[n]);
              sim.tick(1440);
              return performance.now() - t0;
            }""")
            return (r < 5000, f"{r / 1000:.2f} s for 1 simulated year with all three packs")
        check("11 speed: one year with all packs under 5 seconds", t11)

        # ---------- births, scenarios, scoring, panels ----------
        S8 = [1, 2, 3, 4, 5, 6, 7, 8]
        new_errors = []

        # 15. births
        def t15():
            r = page.evaluate("""() => {
              const run = (seed) => {
                const s = LTB.createSim({ seed });
                s.tick(3 * 1440);
                const v = s.state.villagers;
                return { births: s.state.stats.births, n: v.length, idsOk: v.every((x, i) => x.id === i),
                  aliveOk: v.filter(x => x.alive).length === s.state.alive, hash: s.hash(),
                  namesOk: v.every(x => typeof x.name === 'string' && x.name.length > 0) };
              };
              const a = run(4), b = run(4), c = run(5);
              return { a, same: a.hash === b.hash, differ: a.hash !== c.hash };
            }""")
            a = r["a"]
            ok = a["births"] > 0 and a["n"] == 50 + a["births"] and a["idsOk"] and a["aliveOk"] and a["namesOk"] and r["same"] and r["differ"]
            return ok, f"births in 3 years {a['births']}, villagers {a['n']}, ids match index {a['idsOk']}, same-seed hash equal {r['same']}"
        check("15 births add villagers with correct ids and keep determinism", t15)

        # 16. newcomers placement and defaults
        def t16():
            r = page.evaluate("""() => {
              const s = LTB.createSim({ seed: 6 });
              const orig = s.state.villagers.slice();
              const res = s.applyPack({ id: 'crowd', title: 'Crowd', narration: 'More.', newcomers: { count: 30 },
                behaviors: [{ id: 'mill', label: 'Milling', priority: 1, target: { kind: 'self' }, duration: 1, do: [] }],
                drawback: { label: 'Crowds', text: 'Crowds.', rules: ['mill'] } });
              const fresh = s.state.villagers.slice(50);
              const landOk = fresh.every(v => s.state.tiles[Math.floor(v.y) * s.state.W + Math.floor(v.x)] > 0);
              const near = fresh.every(v => orig.some(o => Math.hypot(o.x - v.x, o.y - v.y) < 4.5));
              const clean = fresh.every(v => v.health === 100 && Object.keys(v.items).length === 0 && Object.keys(v.feel).length === 0
                && v.roles.length === 0 && Object.keys(v.traits).length === 0 && v.alive && v.needs.hunger === 20 && v.needs.tiredness === 20);
              return { ok: res.ok, n: fresh.length, ids: s.state.villagers.every((x, i) => x.id === i), landOk, near, clean, alive: s.state.alive };
            }""")
            ok = r["ok"] and r["n"] == 30 and r["ids"] and r["landOk"] and r["near"] and r["clean"] and r["alive"] == 80
            return ok, str(r)
        check("16 newcomers land near the others with default needs and nothing else", t16)

        # 17. validation of newcomers and birth
        def t17():
            r = page.evaluate("""() => {
              const beh = (d) => ({ id: 'bb', label: 'B', priority: 1, duration: 1, target: { kind: 'self' }, do: d });
              const pack = (over, d) => Object.assign({ id: 'pp', title: 'P', narration: 'P.', behaviors: [beh(d || [])],
                drawback: { label: 'x', text: 'x', rules: ['bb'] } }, over);
              const bad = {
                'count 0': pack({ newcomers: { count: 0 } }), 'count 401': pack({ newcomers: { count: 401 } }),
                'count 1.5': pack({ newcomers: { count: 1.5 } }), 'count text': pack({ newcomers: { count: '5' } }),
                'count negative': pack({ newcomers: { count: -3 } }), 'count NaN': pack({ newcomers: { count: NaN } }),
                'extra field': pack({ newcomers: { count: 5, extra: 1 } }), 'not object': pack({ newcomers: 5 }),
                'no count': pack({ newcomers: {} }),
                'birth 0': pack({}, [{ birth: 0 }]), 'birth 6': pack({}, [{ birth: 6 }]), 'birth 2.5': pack({}, [{ birth: 2.5 }]),
                'birth text': pack({}, [{ birth: '1' }]), 'birth extra': pack({}, [{ birth: 1, on: 'self' }]),
              };
              const good = { 'count 1': pack({ newcomers: { count: 1 } }), 'count 400': pack({ newcomers: { count: 400 } }),
                'birth 1': pack({}, [{ birth: 1 }]), 'birth 5': pack({}, [{ birth: 5 }]) };
              const out = { acceptedBad: [], rejectedGood: [], changed: [] };
              for (const [k, p] of Object.entries(bad)) {
                const s = LTB.createSim({ seed: 2 }), h = s.hash();
                if (s.applyPack(p).ok) out.acceptedBad.push(k);
                if (s.hash() !== h || s.state.villagers.length !== 50) out.changed.push(k);
              }
              for (const [k, p] of Object.entries(good)) {
                const s = LTB.createSim({ seed: 2 });
                const r = s.applyPack(p);
                if (!r.ok) out.rejectedGood.push(k + ': ' + r.errors.join(' '));
              }
              // birth works as an effect with no target
              const t = LTB.createSim({ seed: 2 });
              t.applyPack(pack({}, [{ birth: 2 }]));
              return out;
            }""")
            return (not r["acceptedBad"] and not r["rejectedGood"] and not r["changed"]), str(r)
        check("17 validator rejects bad newcomers and birth, accepts good ones, atomically", t17)

        # 18. the 400 cap
        def t18():
            r = page.evaluate("""() => {
              const s = LTB.createSim({ seed: 3 });
              s.applyPack(window.__packs.swarm);
              const afterFirst = s.state.alive;
              const more = s.applyPack({ id: 'more', title: 'More', narration: 'Even more.', newcomers: { count: 50 },
                behaviors: [{ id: 'mill', label: 'Milling', priority: 1, target: { kind: 'self' }, duration: 1, do: [] }],
                drawback: { label: 'x', text: 'x', rules: ['mill'] } });
              const afterSecond = s.state.alive;
              // everyone has children as fast as possible with food to burn: the island must still stop at 400
              const t = LTB.createSim({ seed: 3 });
              t.applyPack({ id: 'baby_boom', title: 'Baby boom', narration: 'Babies.',
                behaviors: [{ id: 'boom', label: 'Booming', priority: 99, duration: 0, target: { kind: 'self' }, do: [{ birth: 5 }] }],
                drawback: { label: 'Babies', text: 'So many babies.', rules: ['boom'] } });
              let maxAlive = 0, atTick = null;
              for (let i = 0; i < 40; i++) { t.tick(10); maxAlive = Math.max(maxAlive, t.state.alive); if (atTick === null && t.state.alive >= 400) atTick = t.state.tick; }
              return { afterFirst, afterSecond, moreOk: more.ok, maxAlive, reached: atTick, births: t.state.stats.births, len: t.state.villagers.length };
            }""")
            ok = r["afterFirst"] == 400 and r["afterSecond"] == 400 and r["moreOk"] and r["maxAlive"] == 400 and r["births"] == r["len"] - 50
            return ok, str(r)
        check("18 the island never holds more than 400 villagers", t18)

        # 19. design requirements a, b, c
        def t19():
            nothing_c = page.evaluate("(seeds) => seeds.map(s => window.__scen(s, 'crowded', null))", S8)
            nothing_h = page.evaluate("(seeds) => seeds.map(s => window.__scen(s, 'hold_on', null))", S8)
            orch_c = page.evaluate("(seeds) => seeds.map(s => window.__scen(s, 'crowded', 'orchards'))", S8)
            orch_h = page.evaluate("(seeds) => seeds.map(s => window.__scen(s, 'hold_on', 'orchards'))", S8)
            brute = page.evaluate("(seeds) => seeds.map(s => window.__scen(s, 'crowded', 'swarm'))", S8)
            fmt = lambda rs: [f"{r['seed']}:{r['status']}/{r['cause'] or '-'}@d{int(r['day'])} pop{r['alive']} score{r['score']}" for r in rs]
            a1 = sum(1 for r in nothing_c if r["status"] == "lost" and r["cause"] == "deadline")
            a2 = sum(1 for r in nothing_h if r["status"] == "lost")
            b1 = sum(1 for r in orch_c if r["status"] == "won")
            b2 = sum(1 for r in orch_h if r["status"] == "won")
            c1 = sum(1 for r in brute if r["status"] != "won")
            # year the unaided crowded population levels off: first year end after which it stays within 15% of its final value
            levels = []
            for r in nothing_c:
                p = r["pops"]
                fin = sum(p[-3:]) / 3
                y = next((i + 1 for i in range(len(p)) if all(abs(x - fin) <= 0.15 * fin for x in p[i:])), None)
                levels.append(y)
            print("  a) crowded, no commands:", fmt(nothing_c))
            print("  a) hold_on, no commands:", fmt(nothing_h))
            print("  b) orchards in crowded:", fmt(orch_c))
            print("  b) orchards in hold_on:", fmt(orch_h))
            print("  c) 400 newcomers in crowded:", fmt(brute), "day pop first below 120:", [r["below120"] for r in brute])
            print("  e) crowded, no commands: population levels off in year", levels, "end-of-year populations seed 1:", nothing_c[0]["pops"])
            ok = a1 >= 6 and a2 >= 6 and b1 >= 5 and b2 >= 5 and c1 >= 6
            return ok, f"a) crowded lost by deadline {a1}/8, hold_on lost {a2}/8; b) orchards won crowded {b1}/8, hold_on {b2}/8; c) brute force did not win {c1}/8"
        check("19 design: doing nothing loses, orchards win, brute force fails (8 seeds)", t19)

        # 20. score arithmetic
        def t20():
            r = page.evaluate("""() => {
              const s = LTB.createSim({ seed: 1, scenario: 'crowded' });
              s.applyPack(window.__packs.orchards);
              s.useCommand(); s.useCommand(); s.useCommand();
              s.tick(144 * 120);
              const run = s.state.run;
              return { status: run.status, score: run.score, used: run.commandsUsed, limit: run.commandLimit, day: s.state.tick / 120,
                alive: s.state.alive, breakdown: run.breakdown, held: run.heldDays };
            }""")
            yrs = (144 - int(r["day"])) // 12
            expect = 500 + 150 * (8 - 3) + 40 * yrs + 2 * r["alive"]
            total = sum(b["points"] for b in r["breakdown"])
            ok = r["status"] == "won" and r["used"] == 3 and r["score"] == expect == total and r["held"] >= 12 and len(r["breakdown"]) == 4
            return ok, f"won on day {int(r['day'])} with 3 commands used, {yrs} whole years left, {r['alive']} alive: score {r['score']}, expected {expect}"
        check("20 score arithmetic for a won run", t20)

        # 21. lost runs score nothing, and a stopped sim stays stopped
        def t21():
            r = page.evaluate("""() => {
              const out = [];
              for (const sc of ['crowded', 'hold_on']) {
                const s = LTB.createSim({ seed: 2, scenario: sc });
                s.tick(144 * 120);
                const run = s.state.run, t = s.state.tick, h = s.hash();
                s.tick(500);
                out.push({ sc, status: run.status, score: run.score, pts: run.breakdown.reduce((a, b) => a + b.points, 0), reason: !!run.reason,
                  stopped: s.state.tick === t && s.hash() === h, used: s.useCommand() });
              }
              return out;
            }""")
            ok = all(x["status"] == "lost" and x["score"] == 0 and x["pts"] == 0 and x["reason"] and x["stopped"] and x["used"] is False for x in r)
            return ok, str(r)
        check("21 a lost run scores 0, stops the sim and refuses commands", t21)

        # 22. data driven rules: blights and holding
        def t22():
            r = page.evaluate("""() => {
              const s = LTB.createSim({ seed: 3, scenario: 'hold_on' });
              s.applyPack(window.__packs.orchards);
              const count = () => s.state.objects.filter(o => !o.dead && (o.def === 'orchard' || o.def === 'bush')).length;
              const granary = LTB.createSim({ seed: 3, scenario: 'hold_on' });
              granary.applyPack(LTB.PACKS['make a government']);
              const g0 = granary.state.objects.filter(o => o.def === 'granary').length;
              s.state.villagers.forEach(v => { v.health = 100; });
              const blights = [];
              for (let d = 1; d <= 130 && s.state.run.status === 'playing'; d++) {
                const before = s.state.stats.events.blight || 0;
                s.tick(120);
                if ((s.state.stats.events.blight || 0) > before) blights.push(d);
              }
              granary.tick(120 * 12 * 5);
              const logs = s.state.log.filter(l => /blight/i.test(l.text)).length;
              return { blights, logs, g0, g1: granary.state.objects.filter(o => o.def === 'granary').length, noScenario: LTB.createSim({ seed: 1 }).state.run.scenario };
            }""")
            ok = r["blights"] == [24, 48, 72, 96, 120] and r["logs"] >= 5 and r["g0"] == 1 and r["g1"] == 1 and r["noScenario"] is None
            return ok, str(r)
        check("22 blights come at the start of years 3, 5, 7, 9, 11 and spare granaries", t22)

        # 23. command limit and what counts, through the UI
        def tiny(i):
            import json
            return json.dumps({"id": f"tiny{i}", "title": f"Tiny {i}", "narration": f"Tiny thing {i}.",
                               "behaviors": [{"id": f"tiny_b{i}", "label": "Tiny", "priority": 1, "target": {"kind": "self"}, "duration": 1, "do": []}],
                               "drawback": {"label": "Tiny", "text": "Tiny trouble.", "rules": [f"tiny_b{i}"]}})

        def say_cmd(pg, text, answer=None):
            pg.fill("#cmd", text)
            pg.press("#cmd", "Enter")
            pg.wait_for_timeout(60)
            if answer is not None:
                pg.fill("#paste", answer)
                pg.click("#apply")
                pg.wait_for_timeout(60)

        lim = new_page(browser, new_errors, "?seed=1&mode=hold_on")

        def t23():
            lim.click('#speeds button[data-speed="0"]')
            top = lim.inner_text("#runbar")
            counts = []
            for i in range(6):
                say_cmd(lim, f"make thing number {i}", tiny(i))
                counts.append(lim.evaluate("LTB.game.sim.state.run.commandsUsed"))
            shown = lim.inner_text("#cmds")
            say_cmd(lim, "one more thing")
            asked = lim.is_visible("#ask")
            first = lim.locator("#log li").first
            refused = "refusal" in (first.get_attribute("class") or "") and "all 6 commands" in first.inner_text()
            builtin = lim.evaluate("LTB.game.sim.world.packs.length")
            say_cmd(lim, "unlock fire")
            still = lim.evaluate("LTB.game.sim.state.run.commandsUsed")
            fire = lim.evaluate("LTB.game.sim.world.packs.some(p => p.id === 'fire')")
            ok = (counts == [1, 2, 3, 4, 5, 6] and shown == "Commands: 6/6" and not asked and refused and still == 6 and not fire
                  and "Commands: 0/6" in top and "Keep 30 villagers" in top and "floor 30" in top)
            return ok, f"counts {counts}, counter shows '{shown}', 7th refused={refused}, ask opened={asked}, built-in at the limit applied={fire}, top bar '{top}'"
        check("23 command limit is enforced in the UI and the counter stays", t23)

        # 24. refused, cancelled and invalid commands do not count; word limit
        cnt = new_page(browser, new_errors, "?seed=2&mode=crowded")

        def t24():
            cnt.click('#speeds button[data-speed="0"]')
            used = lambda: cnt.evaluate("LTB.game.sim.state.run.commandsUsed")
            res = {}
            say_cmd(cnt, "make it rain frogs")
            res["opened"] = cnt.is_visible("#ask")
            cnt.click("#cancel")
            res["cancel"] = used()
            say_cmd(cnt, "make it rain frogs", "not json")
            res["invalid"] = used()
            cnt.fill("#paste", '{"id":"oops","title":"Oops","narration":"Oops."}')
            cnt.click("#apply")
            res["invalid2"] = used()
            cnt.click("#cancel")
            say_cmd(cnt, "unlock fire")
            res["builtin"] = used()
            say_cmd(cnt, "unlock fire")
            res["refused"] = used()
            say_cmd(cnt, " ".join(["word"] * 26))
            res["long_ask"] = cnt.is_visible("#ask")
            res["long"] = used()
            first = cnt.locator("#log li").first
            res["long_msg"] = "refusal" in (first.get_attribute("class") or "") and "25 words" in first.inner_text()
            say_cmd(cnt, " ".join(["word"] * 25))
            res["ok25_ask"] = cnt.is_visible("#ask")
            cnt.click("#cancel")
            res["ok25"] = used()
            say_cmd(cnt, "make hats", tiny(50))
            res["valid"] = used()
            ok = (res["opened"] and res["cancel"] == 0 and res["invalid"] == 0 and res["invalid2"] == 0 and res["builtin"] == 1 and res["refused"] == 1
                  and not res["long_ask"] and res["long"] == 1 and res["long_msg"] and res["ok25_ask"] and res["ok25"] == 1 and res["valid"] == 2)
            return ok, str(res)
        check("24 refused, cancelled, invalid and over-long commands do not count; 25 words is fine", t24)

        # 25. start panel
        def t25():
            sp = new_page(browser, new_errors, "")
            sp.wait_for_timeout(300)
            vis = sp.is_visible("#start")
            btns = sp.locator("#scen button").count()
            text = sp.inner_text("#startbox")
            t0 = sp.evaluate("LTB.game.sim.state.tick")
            sp.wait_for_timeout(700)
            paused = sp.evaluate("LTB.game.sim.state.tick") == t0
            villagers = sp.evaluate("LTB.game.sim.state.alive")
            SHOTS.mkdir(exist_ok=True)
            sp.screenshot(path=str(SHOTS / "start-panel.png"))
            sp.click('#scen button[data-mode="crowded"]')
            sp.wait_for_timeout(700)
            started = (not sp.is_visible("#start")) and sp.evaluate("LTB.game.sim.state.tick") > t0 and sp.evaluate("LTB.game.sim.state.run.scenario") == "crowded"
            bar = sp.inner_text("#runbar")
            seed_only = new_page(browser, new_errors, "?seed=5")
            seed_shown = seed_only.is_visible("#start")
            skipped = {}
            for m in ("free", "crowded", "hold_on"):
                q = new_page(browser, new_errors, "?mode=" + m + "&seed=4")
                skipped[m] = (not q.is_visible("#start"), q.is_visible("#runbar"), q.evaluate("LTB.game.sim.state.run.scenario"))
                q.close()
            seed_only.close()
            sp.close()
            ok = (vis and btns == 3 and "Standing room only" in text and "Bad years" in text and "Free play" in text and "Commands: 8" in text
                  and "Deadline: 12 years" in text and "Reach 120 villagers" in text and paused and villagers == 50 and started
                  and "Commands: 0/8" in bar and "Population" in bar
                  and skipped == {"free": (True, False, None), "crowded": (True, True, "crowded"), "hold_on": (True, True, "hold_on")})
            return ok, f"panel shown {vis}, buttons {btns}, sim paused behind it {paused}, started on click {started}, top bar '{bar}', mode skips {skipped}, ?seed alone shows panel {seed_shown}"
        check("25 start panel shows with no mode, pauses the sim, and ?mode= skips it", t25)

        # 26. end panel and Play again
        def t26():
            res = {}
            for kind in ("won", "lost"):
                pg = new_page(browser, new_errors, "?seed=1&mode=crowded")
                pg.click('#speeds button[data-speed="0"]')
                pg.evaluate("""(kind) => {
                  const s = LTB.game.sim;
                  if (kind === 'won') { s.applyPack(window.__packs.orchards); s.useCommand(); s.useCommand(); }
                  s.tick(144 * 120);
                }""", kind)
                pg.wait_for_timeout(400)
                vis = pg.is_visible("#end")
                title = pg.inner_text("#end-title")
                funny = pg.inner_text("#end-funny")
                reason = pg.inner_text("#end-reason")
                lines = pg.locator("#end-score li").all_inner_texts()
                score = pg.evaluate("LTB.game.sim.state.run.score")
                if kind == "won":
                    pg.screenshot(path=str(SHOTS / "end-panel.png"))
                else:
                    pg.screenshot(path=str(SHOTS / "end-panel-lost.png"))
                ticks = pg.evaluate("LTB.game.sim.state.tick")
                pg.wait_for_timeout(300)
                frozen = pg.evaluate("LTB.game.sim.state.tick") == ticks
                refused = False
                pg.evaluate("LTB.game.submit('make a thing')")
                refused = not pg.is_visible("#ask") and pg.evaluate("LTB.game.sim.state.run.commandsUsed") == (2 if kind == "won" else 0)
                pg.click("#again")
                pg.wait_for_timeout(200)
                back = pg.is_visible("#start") and not pg.is_visible("#end") and not pg.is_visible("#runbar")
                res[kind] = dict(vis=vis, title=title, funny=bool(funny.strip()), reason=reason, lines=lines, score=score, frozen=frozen, refused=refused, back=back)
                pg.close()
            w, l = res["won"], res["lost"]
            wok = (w["vis"] and w["title"] == "You won" and w["frozen"] and w["refused"] and w["back"] and w["funny"]
                   and len(w["lines"]) == 5 and w["lines"][0].startswith("Victory") and "Unused commands (6 x 150)" in w["lines"][1] and w["lines"][-1].replace("\n", " ").endswith(str(w["score"])))
            lok = (l["vis"] and l["title"] == "You lost" and l["score"] == 0 and l["frozen"] and l["refused"] and l["back"] and l["funny"]
                   and l["lines"][-1].replace("\n", " ").endswith(" 0") and "years ran out" in l["reason"])
            return wok and lok, f"won: {w['lines']}; lost: {l['lines']} / {l['reason']}"
        check("26 end panel shows the score breakdown, and Play again returns to the start panel", t26)

        # 27. the word god never appears
        def t27():
            import re
            src = INDEX.read_text(encoding="utf-8")
            hits = [m.group(0) for m in re.finditer(r"\bgod\b", src, re.I)]
            gp = new_page(browser, new_errors, "?seed=3&mode=free")
            gp.fill("#cmd", "make a tree")
            gp.press("#cmd", "Enter")
            prompt = gp.input_value("#prompt")
            packs = gp.inner_text("#packs")
            ph = gp.get_attribute("#cmd", "placeholder")
            body = gp.inner_text("body")
            gp.close()
            ok = (not hits and "# The command\nmake a tree" in prompt and re.search(r"\bgod\b", prompt + packs + ph + body, re.I) is None
                  and "Nothing yet. Say something." in packs and ph == "Say the word...")
            return ok, f"matches in source {hits}, prompt heading ok {'# The command' in prompt}"
        check("27 the word god appears nowhere", t27)

        # 28. speed with 400 villagers
        def t28():
            r = page.evaluate("""() => {
              const t0 = performance.now();
              const sim = LTB.createSim({ seed: 9 });
              sim.applyPack(window.__packs.swarm);
              // keep them fed so the whole crowd stays alive and busy for the full year
              sim.world.needs.hunger.perDay = 0;
              for (const n of ['unlock fire', 'introduce crime', 'make a government']) sim.applyPack(LTB.PACKS[n]);
              let minAlive = 999;
              for (let d = 0; d < 12; d++) { sim.tick(120); minAlive = Math.min(minAlive, sim.state.alive); }
              return { ms: performance.now() - t0, minAlive, tick: sim.state.tick };
            }""")
            return (r["ms"] < 15000 and r["minAlive"] >= 395 and r["tick"] == 1440), f"{r['ms'] / 1000:.2f} s for 1 simulated year with 400 villagers and all three packs (fewest alive {r['minAlive']})"
        check("28 speed: one year with 400 villagers and all packs under 15 seconds", t28)

        errors.extend(ui_errors)
        errors.extend(new_errors)
        check("1b no console or page errors across the whole run", lambda: (not errors, f"{errors[:3]}"))

        # screenshot
        SHOTS.mkdir(exist_ok=True)
        shot = new_page(browser, [], "?seed=7&mode=free")
        shot.evaluate("""() => {
          const g = LTB.game.sim;
          for (const n of ['unlock fire', 'introduce crime', 'make a government']) g.applyPack(LTB.PACKS[n]);
          g.tick(1440 + 30);
          LTB.game.refresh();
        }""")
        shot.click('#speeds button[data-speed="0"]')
        shot.wait_for_timeout(400)
        shot.screenshot(path=str(SHOTS / "all-packs-year-1.png"))
        print("screenshot: test-results/all-packs-year-1.png")
        browser.close()

    passed, failed = sum(results), len(results) - sum(results)
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
