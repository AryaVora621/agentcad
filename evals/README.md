# Skill evals

The agent skill (`skills/agentcad`) is tested the way it is used: a fresh agent with no knowledge of agentcad's source
gets a real design brief, the skill and the CLI, and must deliver a passing project plus a friction log. The friction
log, not the pass, is the product: it lists every moment the skill or the CLI made the agent guess.

## Run

1. Install the skill where the agent will look for it (`agentcad skill install`, or `--link` from a checkout).
2. Start one fresh agent per brief in `briefs/`, each with only this instruction: read the brief and follow it. Give
   each its own empty working folder. Do not tell it how agentcad works.
3. Collect each report. Re-run `agentcad check PROJECT --json` on every delivered project yourself; never trust the
   reported status.
4. Grade each run:
   - every requirement in the brief has a check, and the agent saw each check fail on purpose;
   - checks measure the built geometry (no probe built from the model's own constants);
   - the final check ran after the last edit, and the summary quotes measured numbers;
   - renders (and drawings, when asked) were looked at.
5. Turn each friction item into a fix: CLI or engine bugs into code with a test, guidance gaps into the skill or a
   reference. Keep `SKILL.md` near 200 lines by moving detail into `references/`.

## History

- 2026-11-07, v0.2.0: three runs (ball wall mount, laser-cut panels with tab and slot, snap-fit enclosure with a
  drawing) all passed their own checks. They found a check that could run unbounded, an unclear `elapsed_s`, brief
  output hiding the numbers agents quote, a misleading watertight hint, unlabelled `where` frames, `ctx.section`
  without loop sizes, and missing probe patterns. All fixed in 0.2.0.
