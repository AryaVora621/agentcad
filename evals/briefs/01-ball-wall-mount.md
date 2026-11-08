You are a fresh agent testing a CAD tool on a real job. Do not read agentcad's source code. Work only in a new,
empty folder. Use the agentcad skill (load it first and follow it) and the `agentcad` CLI; learn only from the skill,
its references and the CLI itself (`agentcad --help`, `agentcad api`, `agentcad skill show --reference NAME`).

The job: Design a wall mount that holds a 54 mm ball (a display sphere) in a cradle so it cannot roll off, and
screws to a wall with two M4 countersunk wood screws. It must print on a Bambu Lab P1S (bed 256 x 256 x 256 mm) in
PETG with no supports, and the ball must drop in and lift out from above by hand.

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
