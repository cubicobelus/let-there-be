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


def new_page(browser, errors, query="?seed=3"):
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
            return (n == 50 and box.count() == 1 and ph == "Speak, god..." and not errors,
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

        errors.extend(ui_errors)
        check("1b no console or page errors across the whole run", lambda: (not errors, f"{errors[:3]}"))

        # screenshot
        SHOTS.mkdir(exist_ok=True)
        shot = new_page(browser, [], "?seed=7")
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
