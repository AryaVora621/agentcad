You are a fresh agent testing a CAD tool on a real job. Do not read agentcad's source code. Work only in a new,
empty folder. Use the agentcad skill (load it first and follow it) and the `agentcad` CLI; learn only from the skill,
its references and the CLI itself (`agentcad --help`, `agentcad api`, `agentcad skill show --reference NAME`).

The job: Design a two-part snap-fit enclosure (base and lid) for a 40 x 25 x 10 mm circuit board with 0.3 mm
clearance around the board, a 9 x 4 mm USB-C opening centred on one 25 mm end, and lid snaps that hold it shut. Both
parts print without supports on a generic 220 x 220 x 250 printer. Then make a dimensioned drawing of the base for a
person to check.

Deliver a report with:
1. The project folder, the part list and the final `agentcad check PROJECT --json --brief` result from a run after
   your last edit.
2. Each requirement, the check that covers it, the measured number, and whether you saw that check fail when you
   broke the geometry on purpose.
3. What you looked at (render sheet, drawing PNG) and what you saw.
4. A friction log, the most important part: every moment the skill or the CLI made you guess, misled you, gave a
   confusing message or cost an extra attempt. Quote the exact text and say what would have helped. List anything in
   the skill you never needed.
5. Rough counts: agentcad commands run, failed builds (exit 3), failed checks you fixed.
