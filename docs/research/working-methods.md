# How people and their assistants actually work: evidence for Daymark's design

The November 18 follow-up on choosing an action draws on current publisher
guides from Things, Todoist and Getting Things Done. It connects their
documented practices to one-call changes of view and an optional time window
based on task estimates. [Working with a person's preferences](../working-preferences.md)

Research date: 2026-11-15. Bounded lookups, not a systematic review. Each row
says how well supported it is: **clinical** (trials or reviews), **practitioner**
(people who do the work, describing it), **folk** (widely repeated, untested),
**convention** (a tool's own choice). Where a lookup failed, it says so. This
document supplies evidence; [architecture.md](../architecture.md) is the design.

## 1. Methods: what each needs stored, and what it imposes

| Method | Stored data | Ritual or rule | Source |
|---|---|---|---|
| Getting Things Done | Inbox item; next action with a context; project; waiting-for; someday/maybe | Capture, clarify, organize, reflect, engage; a weekly review so every project has a current action | [gettingthingsdone.com](https://gettingthingsdone.com/what-is-gtd/), [weekly review checklist](https://gettingthingsdone.com/wp-content/uploads/2014/10/Weekly_Review_Checklist.pdf) |
| Bullet Journal | Entry type; state (done, migrated, scheduled); the day or month it sits in | Rapid logging; migration: rewrite, schedule or strike each open task | [bulletjournal.com](https://bulletjournal.com/blogs/faq) |
| Time blocking | Per day: ordered blocks with an assignment, and their revisions | Plan every minute beforehand; redraw when the day breaks | [Cal Newport](https://calnewport.com/deep-habits-the-importance-of-planning-every-minute-of-your-work-day/) |
| Personal Kanban | Stage per card; order | Visualise the work; limit work in progress | [personalkanban.com](https://www.personalkanban.com/personal-kanban-101) |
| Autofocus / Final Version | One ordered list; a mark; a dismissed flag | Scan and do what stands out; chain by "what do I want to do before this?" | [Mark Forster](http://markforster.squarespace.com/autofocus-system/) |
| Ivy Lee, "eat the frog" | Per day: a ranked list of at most six | List six the evening before; work in order; carry over | [James Clear](https://jamesclear.com/ivy-lee) (secondary) |
| Pomodoro | Count of completed intervals per task | 25 minutes on, a short break, a long break after four | [pomodorotechnique.com](https://www.pomodorotechnique.com/) |
| Shutdown and reviews | A weekly plan; a day note; last reviewed | Check lists, calendar and plan, then stop | [Cal Newport](https://calnewport.com/drastically-reduce-stress-with-a-work-shutdown-ritual/) |

**In three or more methods**, so candidates for stored data every client can
rely on: open/done/dropped; a start date distinct from a deadline; a planned
day; a manual order; a container; waiting-on; a parked ("someday") state; free
tags; a duration estimate; a stage; created and last-touched times; typed
custom fields.

**In one method only**, so data a plugin should own: interval counts, Autofocus
marks, urgent/important flags, time blocks and their revisions, reward points,
review intervals, signifiers.

## 2. Software that separates data from method

| Tool | What is data, what is method | Extension mechanism | Source |
|---|---|---|---|
| Amazing Marvin | Tasks, projects and categories are fixed; "most features are optional" switches | About 25 groups of *strategies*, each with settings; *workflows* switch on a bundle (GTD, 1-3-5, Eisenhower, Autofocus) and switch off cleanly | [strategies](https://help.amazingmarvin.com/amazing-marvin/categories/strategies), [workflows](https://help.amazingmarvin.com/en/articles/2541356-setting-up-your-workflow) |
| Taskwarrior | Core attributes; user-defined attributes are typed data with no behaviour | Urgency coefficients; reports as filter, sort and columns; hooks that may rewrite or reject a write | [UDAs](https://taskwarrior.org/docs/udas/), [hooks](https://taskwarrior.org/docs/hooks/) |
| Org-mode | Heading, keyword, tags, timestamps, properties | Custom agenda commands; query languages | [manual](https://orgmode.org/manual/Matching-tags-and-properties.html) |
| OmniFocus | Defer date, due date, a review interval per project | Perspectives; automation plug-ins | [perspectives](https://support.omnigroup.com/documentation/omnifocus/universal/4.9.3/en/perspectives/) |
| Sunsama | Task, planned and actual time, day | Daily planning and shutdown rituals; rollover | [daily planning](https://help.sunsama.com/docs/daily-planning) |
| Motion, Reclaim | Duration, deadline, start, priority, chunk size, working hours | A closed scheduler with parameters | [Motion](https://www.usemotion.com/help/time-management/auto-scheduling/reference-auto-scheduling/what-auto-scheduling-considers), [Reclaim](https://help.reclaim.ai/en/articles/5108936) |
| Beads | Issues and dependency edges | Formulas: templates with variables, needs and gates | [beads](https://github.com/steveyegge/beads) |

Marvin is the closest precedent for a strategy layer. Concrete strategies and
their parameters: a truncated list (size; random or suggested selection), a
staleness warning (days), a procrastination count (one mark per day past the
first schedule), a focus picker, day planning that warns when the day is
overbooked and counts an unestimated task as 20 minutes
([truncated list](https://help.amazingmarvin.com/en/articles/3623546-truncated-list),
[staleness](https://help.amazingmarvin.com/en/articles/1950228-staleness-warning),
[day planning](https://help.amazingmarvin.com/amazing-marvin/day-planning-2)).

**Kinds of strategy that recur** (our grouping): choosing and ranking what to
do next; limits; rituals at a cadence; an inbox with a clarify step; rollover
of unfinished work; scheduling into time; decomposition and templates; hygiene
warnings; reward and visibility; views; and bundles of the above.

**What a pure function over stored tasks cannot do**: read a calendar (any
scheduler); run timers and clock triggers (Pomodoro, midnight rollover); ask a
person to judge ("stands out", migration); call a language model (task
breakdown); observe another system; veto or rewrite a write (Taskwarrior
hooks); keep per-day state that is not a task (blocks, a day note).

## 3. What good executive assistants do

Practitioner sources throughout; thin where noted.

| Practice | What must be stored | Source |
|---|---|---|
| Bring-forward ("tickler"): file an item under the day it needs attention, and check daily | A resurface date separate from a due date | [Executive Support Magazine](https://executivesupportmagazine.com/setting-up-a-tickler-file/) |
| Work backward from fixed dates: travel four to six weeks out, a final brief two to three days before, buffers around meetings | Per event: the anchor, a lead-time template, linked preparation tasks | [Navan](https://navan.com/blog/executive-travel-itinerary-checklist), [Executive Assistant Institute](https://executiveassistantinstitute.com/executive-assistant-travel-checklist/) |
| Look two to four weeks ahead for conflicts and missing preparation | Commitments per day; recurring obligations | [InSync](https://insyncea.com/blog/calendar-management-for-executive-assistants/) |
| A daily brief: schedule, priorities, problems with a suggested fix, decisions needed; readable in ten to fifteen minutes | A brief per day; a decision-needed mark per item | [The EA Campus](https://theeacampus.com/resources/daily-briefing/) |
| Batch questions into one list for the regular sync | A question queue with urgency | [Base](https://basehq.com/resources/executive-assistant-one-on-one-agenda-template/) |
| Delegation levels set per kind of decision, widened step by step | Per decision kind: a level and a review date | [Management 3.0](https://management30.com/practice/delegation-poker/) |
| A preference document the assistant maintains | Standing instructions with a scope and a date | [The EA Campus](https://theeacampus.com/blog/building-your-ea-operating-manual/) |
| Trust is earned: play back the request, show a partial pass, propose before sending | Per decision kind: accepted and rejected history | [First Round Review](https://review.firstround.com/a-tactical-guide-to-working-with-eas-how-to-make-delegation-your-superpower/) |
| A waiting-for list with the person, the item and the date; escalate by age | Who, asked on, next nudge, nudge count | [Intentional Academia](https://intentionalacademia.substack.com/p/how-and-why-to-use-a-waiting-for) |
| Failure modes: saying yes to everything, no buffers, weak follow-through, speaking without authority | Capacity per day | [Full Focus](https://fullfocus.co/top-ten-executive-assistant-mistakes/) |

Not established: the Harvard Business Review piece and Jan Jones's book were
read only second-hand; no usable source was found for declining on the
principal's behalf or for working to energy patterns.

## 4. Executive-function differences

| Difficulty | Evidence | What helps | A default that harms | Source |
|---|---|---|---|---|
| Knowing what to do but not doing it at the moment it matters | Clinical theory (Barkley), read second-hand | Put the cue where and when the task happens | An app that must be opened to help | [qualitative study](https://arxiv.org/abs/2603.17258) |
| Time blindness and underestimation | Clinical: a pooled meta-analysis; adult evidence scarce | Record estimate against actual; plan backward | Lists with no dates or durations | [Marx et al.](https://eprints.soton.ac.uk/456003), [CHADD](https://chadd.org/wp-content/uploads/2018/06/ATTN_10_15_BeatingTimeBlindness.pdf) |
| Organisation and planning | Clinical trials of skills training: one calendar and one list reviewed daily, tasks broken into steps, "good enough" | Offer the first step only | Many lists; a large undifferentiated backlog | [Safren et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC3874265) |
| Starting | Small laboratory studies of if-then plans, in children | Phrase as "when X, do Y" | Bare titles | [Gawrilow et al.](https://www.socmot.uni-konstanz.de/publications/implementation-intentions-facilitate-response-inhibition-adhd-children) |
| Shame from overdue piles | Qualitative; vendor writing | Neutral ageing; a bulk reset | Red overdue badges | [qualitative study](https://arxiv.org/abs/2603.17258) |
| Reminders fade | Qualitative | Vary the channel; escalate or stop | An identical repeating notification | same |
| Body doubling | Folk; no controlled trials found | Offer it; claim nothing | | [Cleveland Clinic](https://health.clevelandclinic.org/body-doubling-for-adhd) |
| Predictability (autism) | Evidence for visual schedules in children; adult evidence not found | A stable order; warn of changes | Silent re-sorting | provider summaries |
| Perfectionism and procrastination | Meta-analysis, a modest correlation | State what "done" means | Open-ended tasks | [Sirois et al.](https://eprints.whiterose.ac.uk/id/eprint/112533/1/Sirois%20et%20al%20in%20press%20EJP%20Procrastination%20%26%20Perfectionism%20meta-analysis.pdf) |
| Variable energy | Folk metaphor ("spoons") | Fit the plan to stated capacity | A fixed daily load | [Cleveland Clinic](https://health.clevelandclinic.org/spoon-theory-chronic-illness) |
| Highly organised, wants density | No source; our inference | Full views, nothing trimmed | Forced simplification | |

The clinical guidance page (NICE NG87) could not be fetched. "Interest-based
nervous system", "wall of awful", dopamine menus and novelty decay were not
checked and should be treated as folk.

## 5. What should never happen without the person's say

From the assistant sources and the qualitative study, in the participants'
words where given:

1. Committing or speaking for the person.
2. Sending a message to someone else, a nudge included. Propose it first.
3. Rewriting or enforcing the plan: "I want it to suggest, not decide."
4. Sharing tasks or status with anyone else; some called shared planning "an invasion of privacy".
5. Inferring someone's state and acting on it ("it feels like it's watching me"), and an agent widening its own authority.
