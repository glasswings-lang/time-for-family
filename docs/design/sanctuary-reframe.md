# TFF Sanctuary Reframe — Design Spec

**Status:** Spec only. Not built yet. Future sessions to implement.

**Date drafted:** 2026-06-02 (during conversation with Rozaya).

**Premise of the reframe:** TFF has been drifting away from "cozy creature park" toward "wildlife/creature sanctuary" the longer the user has played it. Several existing concepts (the **village**, the **wild emigration**) stopped landing because they were trying to solve problems that the sanctuary frame doesn't have. This doc captures the reframe and the design decisions that flowed from it.

The reframe also resolves several pain points the user named directly:
- "The wild thing just isn't working." (Solved: wild gets reframed; see below.)
- "The village thing isn't, either, not really." (Solved: village is deprecated entirely.)
- The "overwhelmed with species" pull when more creatures get added. (Solved: NPC humans handle the daily tracking; player only sees what needs decisions.)
- The hands-on per-room affection-meter tending feeling like busywork. (Solved: that moves entirely to NPCs.)

This is NOT a sibling game. It's TFF evolving. Existing code, existing save format, existing species library all carry forward with migration.

---

## The conceptual reframe

**Old TFF frame:** A cozy creature park. Players adopt creatures into rooms, build affection with them, watch them mature and pair, occasionally release neglected ones to "the wild" or move settled ones to "the village" offstage.

**New TFF frame:** A wildlife/creature sanctuary. The player is the **director** — strategic decisions, intake approvals, release approvals, building expansion. NPC **caregivers** handle the day-to-day care, observe creatures, and surface notes and decisions for the player via an inline Messages destination. A **facility human** handles habitat upkeep and physical infrastructure. Creatures are autonomous beings the sanctuary provides for, not pets the player owns. Some get released back to the wild as a *positive* outcome of successful rehabilitation. Others live their lives in the sanctuary.

**Why this fits the existing design values better than "creature park" did:**
- No debuffs — sanctuary doesn't punish neglect; creatures are tended by caregivers regardless of player engagement.
- Disability as respectful representation — fits naturally in a sanctuary context.
- Cozy by default — the relationship is "we provide for these beings," not "we own them and must keep them happy."
- No completionism — there's no "collect all the species" pull in a sanctuary; you take in who needs taking in.
- Mixed-species rooms become *habitats*, and rather than feeling like a UX problem they become the natural shape.

---

## NPC roles (the new labor split)

Real-world wildlife sanctuaries have a clean three-tier structure (per the Open Sanctuary Project and similar references). TFF maps onto this directly:

### Player = Executive Director

Strategic, mission-level. Handles:
- **Intake decisions** — "Should we take in this rescued creature?" Yes/no.
- **Release decisions** — "Hazel seems ready for release. Approve?"
- **Habitat construction** — Building new rooms, expanding existing ones.
- **Resource allocation** — When to spend accumulated resources, where to invest.
- **Naming confirmations** — Caregivers suggest names; player confirms, edits, or overrides.

Does NOT do:
- Day-to-day feeding or affection tending.
- Manual pairing choices.
- Habitat repair / facility upkeep.

### Caregivers = Animal Care Staff (the new "humans" feature)

Per-creature, daily, on-the-ground. Each caregiver is an NPC inhabitant of the sanctuary. Handles:
- **Daily care** of creatures in their assigned habitats — feeding, affection meter maintenance, behavioral observation.
- **Notes / observations** that appear in room views as flavor text (this is the steady-state game content; see "Observation as content" below).
- **Naming suggestions** for new arrivals.
- **Pairing observations** — caregivers notice when creatures form bonds and report; they don't *decide* pairings (those emerge naturally).
- **Release-readiness reports** — caregivers notice when a rehabilitated creature seems ready and flag it.
- **Resource collection** — they collect things creatures produce naturally on their rounds.

Caregivers have their own life: they may pair (with each other or stay solo), they may take on more responsibility over time, they may eventually move on. Their presence in the sanctuary is itself part of the texture.

Caregivers replace the **village** entirely. The village existed as an offstage destination for creatures the player wasn't actively tending. In the sanctuary frame, caregivers tend those creatures; there's no offstage. Settled creatures stay in their habitats.

### Facility Human = Facilities Manager

Physical environment, infrastructure, maintenance. Handles:
- Habitat upkeep — building repairs, structural soundness.
- Build-out execution — when the player approves expansion, the facility human carries it out.
- Implicit cost — small ongoing resource drain to keep facilities maintained.

Probably one NPC for this role (vs. multiple caregivers).

---

## Wild + village: deprecated, reframed

### Village → gone

The village is removed entirely. Settled creatures stay in their habitats; caregivers tend them. No more offstage void.

**Migration story (existing saves):** Any creatures in the existing village move to a compatible habitat at load time. If no habitat exists, a "general habitat" is auto-created at first launch of the new version to hold them, with a note in the changelog dialog explaining the migration. (Open question: do we auto-create a habitat per species, or one mixed-species shelter habitat for the migration only?)

### Wild → inverted

In current TFF, **wild = emigration triggered by neglect** — a creature leaves because their affection dropped too low. This is a debuff dressed cozy.

In the sanctuary frame, **wild = rehabilitation release** — a creature successfully recovered and returned to where they came from. Positive outcome, not loss.

Mechanically this is almost an inversion: the trigger flips from "low affection over time" to "high sustained care over time AND species suitable for release." Some species are sanctuary residents for life (domestic cats, dogs, those that can't be released for reasons described per-species). Others have a release path (wild birds, deer, wolves, etc.).

Per-species `releasable` field (bool) plus `release_readiness_threshold` (how much sustained care is needed before release becomes possible). When threshold is met for a releasable creature, a caregiver flags it in Messages; the player decides whether to approve the release.

After release, the creature appears in a "Released" section of the StatsPanel's existing memorial-style display — alive, returned to the wild, a positive memorial.

---

## Resources from creature life

Resources for sanctuary expansion (building habitats, etc.) come from things creatures **naturally produce** as part of being alive — no harvest button, no active player action required.

Examples (each species would specify what it produces):
- Chickens lay eggs.
- Sheep shed wool.
- Goats / cows give milk.
- Bees produce honey.
- Mammals shed fur.
- Birds drop feathers.
- All creatures produce droppings → fertilizer.
- Long-lived creatures have a passive "presence" benefit (companionship → joy → social capital).

Caregivers collect these on their daily rounds. They accumulate as sanctuary resources. The player doesn't see "+3 eggs today"; they see (over time) a Messages note from a caregiver: "We've got enough materials now to build the aviary you were thinking about."

Per-species schema additions:
- `produces: list of {item, frequency_seconds, amount}` (modder-editable, defaults reasonable).
- `release_readiness_threshold` (in addition to the existing care fields).
- `releasable` boolean.

---

## Dialog architecture: NO popup windows for routine dialogs

**Critical user requirement.** Caretaker communication must NOT spawn separate wxPython modal windows. Reasons:
- Modal popups steal focus, interrupting NVDA's reading flow.
- Multiple events queue up as stacked popups — bad UX.
- The "frantic" feeling the user wants to escape comes partly from popup interrupts.

**The fix:** A **Messages** destination in the existing combo-box + simplebook navigation. Sits alongside rooms, Village (deprecated), Park. Pending caregiver messages appear there. Player navigates to Messages on their own pace. Focus stays in the main frame; nothing yanks focus or interrupts.

**Implementation notes:**
- New page in MainFrame's simplebook, selectable via the existing room-navigator combo-box (so it appears as "Messages" in the dropdown).
- Each pending message renders inline in the Messages page — a scrollable list with each item expandable in place. Buttons for the response (yes/no/text input/dismiss) are rendered inline within the message, not in a separate dialog.
- Pending message count appears in the combo-box label (e.g. "Messages (3)" when 3 pending) for visibility without focus-stealing.
- New messages can optionally fire a quiet status-line announce ("New caregiver report") via the existing NVDA announce system. Aggregated for multiple events per the existing pattern.

**For "genuine emergency" moments — even those should land in Messages first.** In a cozy game, there should be ~zero events that can't wait. The bar for a real wxDialog popup is "the player is actively configuring something via a modal flow they themselves opened" (Settings, Species editor, etc.). Caregiver-initiated communication never opens a modal popup.

**Open question — message rhythm:**
- Always-available (continuous queue, player reads on their own pace)?
- Batched (caregivers "report" at intervals — daily, weekly)?
- Event-driven (notable thing happens → message appears, otherwise quiet)?
- Some mix?

The choice affects whether Messages has an always-visible count, or quietly populates and only nudges when something new lands.

---

## Observation as primary content

For the user specifically (NVDA, blind), the game content is **read** rather than visually scanned. The load-bearing piece of the design is therefore the **caregiver observations** — the small text snippets that appear in room views describing what's been happening.

Each room view shows:
- List of creatures present (existing).
- Recent caregiver observations: "Pepper's been sleeping in the corner most days." "Sage and Mira spend a lot of time near each other." "The new fawn settled in well — they make a little noise when greeted." "Hazel has been showing strong recovery — might be ready for release soon."
- Building / habitat status (existing room metadata).

The observations are the COZY. They're what the player reads when they drift through rooms catching up. Decisions are the spine of player engagement; observations are the meat.

Observations are generated procedurally per species, per creature, per current state. Each species has an `observation_pool` — text snippets the system draws from based on the creature's current state (mood, age, recent activity, bonding status). Modders edit these pools the same way they edit other text pools today.

The caregiver attribution makes these feel ground-level and real ("Mira noticed that Pepper has been...") rather than narrator-omniscient.

---

## Pairing: emerges naturally

In current TFF, pairing is something the player decides (open the pairing UI, pick two creatures, breed). In the sanctuary frame, this becomes **caregivers observe pairs forming naturally and report**:

- Compatibility check runs in background as it does now.
- When a compatible pair forms (existing `progress_pairing` logic), the result surfaces as a caregiver message: "Pepper and Sage have been spending a lot of time together — I think they might be forming a pair. Want me to keep an eye on them?"
- Player responses: "Let them be" (pair proceeds naturally), "Separate them" (rare — if player has reason), or "Yes, watch them" (default — pair watched, eventual breeding observed).
- Breeding itself becomes a caregiver report after the fact, with the existing pre-rolled babies + birth-into-rooms logic from session 5's basket rework.

This fits the existing "no completionism, no debuffs, autonomous creatures" values better than the current "you pick who pairs" UI does.

---

## Naming: caregivers suggest, player confirms

Currently TFF auto-generates names via Markov on the species' name corpus. In the sanctuary frame:

- New arrival appears.
- A caregiver suggests a name based on something they noticed about the creature — drawing from the same Markov-on-corpus system AND from a per-species "observation-flavored naming" pool (e.g. for a creature that arrived with a notable trait, the suggestion has flavor).
- Player can:
  - Confirm the suggestion (default — Enter).
  - Reject and ask for another suggestion (the caregiver "comes up with another").
  - Override with a custom name (text input).

This becomes a small relational moment per creature instead of a forgettable auto-generated string.

Modder note: existing Markov + name corpus + species editor flow all continues. Just the surfacing changes.

---

## New species expansion (the original brainstorm)

The brainstorm Rozaya and her grandmother came up with:

Squirrel, monkey, elephant, lion, snake, different species of birds (parrots / finches / parakeets — explicitly NOT a generic "bird" species), caterpillar/butterfly with multi-stage transformation, moose, bear, caribou, wolf, lynx, kitten/puppy as named life stages, cheetah, leopard, tiger, sea creatures (TBD), chinchilla, plus humans as caretakers (resolved above as a TFF feature, not a separate species).

Three mechanic threads run through this list:

1. **Species differentiation matters within categories.** "Not just a generic bird" is the principle, but it applies to everything — there's no generic "cat" (lion, tiger, leopard, cheetah, lynx all distinct). The existing per-species architecture already supports this; just need the species specs.

2. **Multi-stage transformation** — caterpillar → chrysalis → butterfly is a creature CHANGING species, not maturing within one. New machinery needed (`transforms_into: <species_id>`, `transformation_age_seconds`).

3. **Inter-animal interaction** — animals interact with each other in habitats. Architecturally small (room model already knows who's where); design question is what kinds of interactions fire and how they surface (probably via caregiver observations: "Mira saw Pepper and Hazel playing in the meadow this morning").

### Multi-stage transformation: design sketch

Per-species fields:
- `transforms_into: species_id` (optional)
- `transformation_age_seconds: int` (when the transformation triggers)
- `transformation_observation_pool: list[str]` (text describing the in-between/just-after state)

At `transformation_age_seconds`, the creature is replaced with a new creature of the target species, keeping:
- `id` (same creature, continuous identity)
- `name`
- `disability` (if any)
- `affection` (carries forward — they don't reset emotionally)
- `parent_ids` (their history is theirs)
- `arrived_at` (original sanctuary arrival)

Resets:
- `age_seconds` (resets to 0 in the new species — they're a newly-emerged butterfly, a recently-born kit if applied that way)
- `mature_at` (recalculated against new species' breeding age)
- `current_room` (may need re-placement based on new species' compatible_room_types)

A transformation event fires a caregiver observation: "The chrysalis we were watching has opened — it's a butterfly now."

### Named life stages: design sketch

Per-species fields:
- `baby_name: str` (e.g. "kitten" for cat, "puppy" for dog, "calf" for cow/moose, "fawn" for deer/caribou, "cub" for big cats/bears, "joey" if marsupials added)
- Optionally: `juvenile_name: str` (e.g. "fledgling" for birds, between baby and adult)
- `elder_name: str` (already exists in some form — verify)

These names propagate into:
- Announcement templates ("Pepper had a litter of 3 kittens.")
- Room view labels.
- Caregiver observations.

No actual lifecycle machinery change — just per-species naming. Existing baby → adult → elder transitions stay as-is.

### Inter-animal interaction: design sketch

Per-room tick (already happens), introduce an inter-creature event roll:
- Low chance per tick that a random pair of compatible-coexisting creatures in the same room produces an interaction event.
- Each species has an `interaction_pool` (similar to observation_pool but two-creature shaped): "{a} and {b} were play-wrestling near the water." "{a} watched {b} carefully but kept her distance." "{a} and {b} groomed each other in the sun."
- Interactions surface as caregiver observations in the room view.
- Different species pairs draw from different pools or fall back to a generic "general coexistence" pool.

Modder-editable text pools.

---

## Species roster: incremental adoption

Don't try to ship all the brainstormed species at once. The reframe ships first; species expansion happens incrementally as specs get authored. The existing Species dialog ("Bring [species] home" / "Create a new species") continues to work — modders and the player author specs over time.

Recommended initial expansion (after the reframe machinery is built):
- Reframe-companion species that test the new mechanics: butterfly + caterpillar (multi-stage), one named-life-stage example (puppy/dog), one inter-interaction-heavy pair (wolf pack? prairie dogs?).
- Then a broader rollout based on what feels right to add.

---

## Migration story (existing TFF saves)

The reframe must not break existing saves. Migration at load time:

1. **Village creatures** → moved to compatible habitats. If none exists, a transitional "shelter" habitat is auto-created to hold them.
2. **Wild emigration history** → preserved. The existing "creatures who left for the wild" history stays in the memorial / released section; the framing in UI shifts from "left because affection dropped" to "released" for these legacy records. (Slight historical fiction, but cleaner than purging the data.)
3. **Affection meters** → carried forward. They still mean something — caregivers maintain them in the new frame.
4. **Existing baskets / expecting records / pairings** → all preserved per the session 5 work.
5. **No caregivers exist in old saves.** First-launch flow on the new version surfaces a Messages note: "Your sanctuary now has caregivers and a facility human. You'll meet them as you play." Initial NPC humans auto-populate based on sanctuary size at migration.

Pre-reframe saves load cleanly. Existing modder content (species, room types, text pools) all carries forward.

---

## Open questions (for future sessions)

These are real design decisions still to make, not just implementation details:

1. **Rhythm of caregiver messages** — always-available continuous queue, batched at intervals, event-driven only, or some mix? Affects how Messages presents pending content.

2. **How many caregivers / how do they "enter" the sanctuary?** Do they auto-arrive as the sanctuary grows? Does the player explicitly hire? Are they themselves a sanctuary-arrival event ("a person came to help" — the caregivers are themselves a kind of resident)? This affects whether caregivers feel like staff or like inhabitants.

3. **Do caregivers have their own affection / wellbeing / aging?** If they're inhabitants, they should — but it can't become busywork the way the old per-creature tending was. Probably yes, low-touch, surfaces via the same observation/messages system.

4. **Pairing between caregivers** — does it happen? Rozaya's initial brainstorm framed "2 humans who may or may not pair" — that fits naturally if caregivers do pair, age, retire / move on. Cozy and honest.

5. **Facility human(s) — one or several?** Probably one for v1. Could expand later.

6. **Player resources visibility** — should the resource counts be visible somewhere, or only surface via Messages ("we have enough now")? The all-visible version invites min-max thinking; the message-only version is more honest to the design intent but might frustrate. Probably hybrid: counts viewable in the Park page for the curious, but never required to engage with.

7. **Release UI** — when a caregiver flags release-readiness, what's the dialog moment? Inline yes/no, with a confirmation step? Single click? Some kind of small ceremony? This is one of the emotionally weighted decision points and probably deserves thought.

8. **Migration: what happens to existing modder content?** Pools etc. carry forward, but does the new system want NEW pool types (caregiver observation pools, interaction pools) added to the modder schema? Yes, almost certainly. Document in MODDING.md as part of the build.

9. **Naming flow specifics** — exactly what UI shape does the "caregiver suggests, player confirms" flow take in Messages? Probably a tight inline panel: name suggestion text + "Confirm" + "Try another" + "Custom..." (with custom revealing a text field).

---

## Non-goals / explicit "don't do this"

- **Don't reintroduce manual per-creature affection tending.** The whole point of the reframe is moving that to NPCs. If a feature would require the player to actively maintain meters, it's wrong.
- **Don't add completionism mechanics.** No "you've rescued 12 of 47 species" counters, no "all caregivers maxed!" achievements. The sanctuary doesn't have a win state.
- **Don't add debuffs anywhere.** Failed care isn't a thing in the new frame; caregivers tend regardless of player engagement. The only failure-shaped event is "no habitat space for a rescue we're considering taking in" — and that's a decision moment, not a punishment.
- **Don't make Messages a popup-anything.** Inline destination only. If a message needs immediate attention, it can fire a quiet status-line announce, but never seize focus.
- **Don't add a "all your animals' affection is too low" warning system.** That whole class of player-anxiety-inducing systems is what the sanctuary reframe removes.

---

## What this doc isn't

- Not a build plan. The user explicitly said "speck it, but not build yet" — implementation is for future sessions.
- Not exhaustive. Plenty of details (UI specifics, exact widget choices, exact menu structures) are deferred.
- Not a commitment to ship all brainstormed species. The species roster is incremental and modder-editable.

The future sessions implementing this should read this doc, then read the relevant existing TFF code (Species dialog, room/habitat machinery, the basket-to-birth rework from session 5, the offline catchup work from session 6) before drafting an implementation plan. The reframe is conceptually clean but touches a lot of existing systems; it deserves a careful staged build.
