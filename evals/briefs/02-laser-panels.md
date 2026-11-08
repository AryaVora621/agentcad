You are a fresh agent testing a CAD tool on a real job. Do not read agentcad's source code. Work only in a new,
empty folder. Use the agentcad skill (load it first and follow it) and the `agentcad` CLI; learn only from the skill,
its references and the CLI itself (`agentcad --help`, `agentcad api`, `agentcad skill show --reference NAME`).

The job: Design a laser-cut front panel and one side panel for a project box, both from 3 mm clear acrylic on a
600 x 400 mm laser bed with a 0.15 mm kerf. Front panel 160 x 90 mm with a window for a 2.8 inch display (visible
area 58 x 44 mm, centred 20 mm left of the panel centre), four M3 mounting holes 5 mm in from each corner, and two
tabs along its bottom edge that fit snugly into matching slots in a 160 x 60 mm side panel lying at 90 degrees to it.
Export the DXF files.

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
