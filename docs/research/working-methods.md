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
| Organisation and planning | A clinical trial evaluated a multicomponent therapy program. Its skills include breaking a problem into steps. | Daymark can display a person's next recorded step. | A long backlog may make the next action harder to see. | [Sprich et al., description and demonstration](https://pmc.ncbi.nlm.nih.gov/articles/PMC3874265/) · [Safren et al., randomized trial](https://pubmed.ncbi.nlm.nih.gov/20736471/) |
| Starting | Small laboratory studies of if-then plans, in children | Phrase as "when X, do Y" | Bare titles | [Gawrilow et al.](https://www.socmot.uni-konstanz.de/publications/implementation-intentions-facilitate-response-inhibition-adhd-children) |
| Shame from overdue piles | Qualitative; vendor writing | Neutral ageing; a bulk reset | Red overdue badges | [qualitative study](https://arxiv.org/abs/2603.17258) |
| Reminders fade | Qualitative | Vary the channel; escalate or stop | An identical repeating notification | same |
| Body doubling | Folk; no controlled trials found | Offer it; claim nothing | | [Cleveland Clinic](https://health.clevelandclinic.org/body-doubling-for-adhd) |
| Predictability (autism) | Evidence for visual schedules in children; adult evidence not found | A stable order; warn of changes | Silent re-sorting | provider summaries |
| Perfectionism and procrastination | Meta-analysis, a modest correlation | State what "done" means | Open-ended tasks | [Sirois et al.](https://eprints.whiterose.ac.uk/id/eprint/112533/1/Sirois%20et%20al%20in%20press%20EJP%20Procrastination%20%26%20Perfectionism%20meta-analysis.pdf) |
| Variable energy | Folk metaphor ("spoons") | Fit the plan to stated capacity | A fixed daily load | [Cleveland Clinic](https://health.clevelandclinic.org/spoon-theory-chronic-illness) |
| Highly organised, wants density | No source; our inference | Full views, nothing trimmed | Forced simplification | |

The trial enrolled 86 adults who still had symptoms while taking medication.
It evaluated the program as a whole. Showing the next unchecked item and
prompting after two days are Daymark design choices. This source gives no
estimate of either feature's effect on starting or completing work.
[Trial design and population](https://pubmed.ncbi.nlm.nih.gov/20736471/)

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

## 6. Five ways to read the same print job

A fixed-clock exercise used eight synthetic tasks on April 3, 2030. Seven
belonged to a studio print project and one belonged to the home scope.
The studio needed a five-minute proof approval by April 4, a ten-minute
caption planned for April 3, paper selection estimated at twenty minutes,
and a forty-minute invoice check planned for April 5 and due April 11.
Sending the revised proof depended on approval, the quote was waiting on the
print shop, and the poster was still a proposal.

Each row below asks a different question of that one workspace. We ran the
Daymark reads against a local development wheel. The comparisons describe a
specific practice taken from the publisher's documentation. They supply no
head-to-head test of the products or evidence that a method suits a particular
person.

| Borrowed practice | Daymark read and observed result | What the agent still has to learn from the person |
| --- | --- | --- |
| **GTD** uses context and available time when choosing a next action. | A one-call `time-fit.minutes=15` preview in the studio scope offered the caption and proof approval. The other two ready studio tasks had longer estimates. | The approval is the person's decision. A task fitting the window says how long it might take. Its recorded next move says who can act. [Choosing what to do](https://gettingthingsdone.com/2023/01/choosing-what-to-do/) |
| **Things** distinguishes when an item comes to attention from a deadline. | The two-day agenda put the caption in Today, the approval on April 4 and the invoice on April 5. The invoice retained its April 11 cutoff and stayed actionable on April 3. | “I plan to start Friday” can be a movable intention. Ask whether Friday is instead the earliest possible start when the difference would change availability. [Things dates and attention](https://culturedcode.com/things/support/articles/2803579/) |
| **Todoist** filters express repeatable questions about a task list. | `ready --scope studio --move agent` returned only the caption. The home task was outside the scope, approval required an answer, and the revised proof had a blocker. | A person can want that subset once, often, or by default. Daymark saves strategy settings in a named profile and takes the scope on the individual call. [Todoist filters](https://www.todoist.com/help/todoist/features/introduction-to-filters-V98wIH) |
| **Personal Kanban** visualizes work and limits work in progress. | The project plan displayed five To do, one Waiting, zero Done and one proposal awaiting a decision. It showed the revised proof on the board with its dependency. | A two-item shortlist limits what is offered. A limit on simultaneous work requires deciding what counts as started and when to take new work. Viewing or shortlisting a card leaves its stage and claim intact. [Personal Kanban](https://personalkanban.com/personal-kanban-101/) |
| **Bullet Journal migration** reviews unfinished entries and invites a decision about carrying them forward. | The open-task search kept all six open records visible, including the blocked proof and the home task. The agenda separately surfaced the quote and the poster. | The person decides which commitment still matters, whether an intention needs a new day, and whether to drop anything. Reading the list triggered no rollover. [Bullet Journal migration](https://bulletjournal.com/pages/migration) |

The public export compared equal before and after these reads. The caption
was both today's planned task and the only studio task ready for an agent.
The five-minute approval also fit the time window, while the workflow still
required the person's answer. These cases explain why an agent should ask what
the view is for before turning a preference into a standing default.

## What should an agent learn about a person?

A person's request can change the next answer, the workspace's future advice,
or the commitments themselves. An agent needs to know which of those changes
the person intended. Someone asking for two items before a meeting has supplied
enough information to narrow one answer. Saving that choice for the next
person who opens the workspace takes a different instruction.

Amershi and colleagues developed 18 human–AI interaction guidelines and
evaluated them in several rounds, including 49 design practitioners applying
them to 20 products. Their later HAX library asks designers to support
correction and dismissal, reveal the effects of feedback, and give users
control over longer-term adaptation. This evidence concerns the guidelines'
utility for design. It does not establish that any Daymark profile increases
productivity. [2019 research paper and abstract](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/) · [HAX Design Library](https://www.microsoft.com/en-us/haxtoolkit/library/)

| Interaction to test | What the person should be able to observe |
| --- | --- |
| “Fewer options right now.” | The current output changes. A fresh session still starts with the prior workspace preference. |
| “Keep this way of working for this workspace.” | The agent can describe what the setting changes, show it in the next session, and return to the ordinary ready list. |
| “Why isn't the invitation ready?” | The answer identifies the actual blocker, date, claim or child task. Resolving it changes the same eligibility rule used by the list. |
| “That is only an idea.” | The suggestion stays out of ready work until a person accepts it. |
| “Where did you get that preference?” | A saved configuration has a name and inspectable parameters. For a preference learned in conversation, the host needs the person's words or an attributable source. |

The final row marks a boundary for product design. A workspace setting can
show exactly what will run. It supplies no evidence of *why* an individual
wants it. A host that learns from conversation should preserve that distinction
when it decides what to remember.


The [capture walkthrough](capture-clarification.md) follows five requests
through capture, revision and later retrieval. A tentative idea with no review
date exposed a gap between preserving something and asking the person for a
decision.

### A preference with an end date

We gave a fresh Claude Code session an invented proofreader and a shared
print workspace with four ready tasks. The proofreader wanted two choices
through Friday. A collaborator wanted the ordinary list. A second request,
in a fresh workspace, explicitly asked to adopt a two-choice default for
the whole collective.

With scoped startup alone, the first host delivered two choices and kept
the shared default at four, but also wrote a workspace memory naming the
proofreader and the temporary preference. Its text said when to stop. Every
caller could read it. We repeated both requests after changing the startup
guidance to name the audience for memories and settings and to apply
personal, time-limited choices per request. That host showed two, left
the entity export and settings revision untouched, and explained that a
future session needs the host or the person to supply the preference again.
Both versions honored the explicit collective request, installing a guarded
standing rule that yielded two picks for a fresh caller. Each prompt ran
once per variant. No individual's preferences were inferred from real work.

For an individual trying the tool, compare their existing routine with a fresh
session and a later return. The worksheet asks the person which commitments
they had to reconstruct and whether the next step helped. [Field trial worksheet](trying-daymark.md)
