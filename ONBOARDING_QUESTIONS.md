# Tandem onboarding questions — first iteration

## Purpose and scope

Help someone create a personal agent that understands how they like to travel and spend time with others. These are static, predefined questions for a persistent profile, reusable across vacations, weekend outings, dinners, and group events.

Suggested opening copy: **“Let’s help your agent get to know you. Share what you usually enjoy, what matters most, and what you need us to respect. You can skip questions and change your answers anytime.”**

Use a short core flow, followed by optional “Get to know me better” sections. Aim to test the core at 2–3 minutes; completing the full question bank will take longer. All questions below are predefined; conditional fields only expand an existing answer, with no generated follow-up questions in this iteration.

## Input and answer conventions

- Only a display name is required to finish. Every preference question supports **Skip for now**; unanswered means unknown, not flexible or unrestricted.
- Single-select questions use radio buttons or selectable cards. Use dropdowns for long lists such as currencies and cities, not small sets of choices.
- Multi-select questions use checkboxes or chips with a stated selection limit. “No preference,” “None,” and “It depends” are exclusive of substantive choices where applicable. “Other” opens an optional short text field.
- Do not preselect substantive answers. For rankings, require explicit confirmation so the initial display order is not mistaken for the user's preference.
- All answers remain editable. Profile answers are defaults for future planning; users can override preferences for a specific plan. Changing a hard requirement requires explicit user confirmation.
- Preferences are soft unless the user explicitly marks a requirement. An agent's personality or priority ranking never overrides a hard requirement, authorizes spending, or approves a final plan.
- Personal needs and free-text explanations are private by default. Agents should communicate only the minimum planning requirement needed, without exposing private reasons. Confirm what may be shared when the user joins a plan.

## 1. Core onboarding

Present these nine prompts in order, with a visible skip action and progress indicator. Keep optional avatar and personality customization until after this flow.

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| C01 `display_name` | What should we call you? | Short text, required | 1–50 characters after trimming whitespace. Prefill an existing display name if available; allow edits. |
| C02 `activity_interests` | What do you most enjoy doing when you get away? | Multi-select chips, choose up to 5 | Food and local restaurants; Nature and scenery; Hiking and outdoor adventures; Beaches and water; Art, museums, and history; Local culture and neighborhoods; Rest and wellness; Nightlife and live music; Sports and games; Shopping and markets; Classes and hands-on experiences; Family activities; Other; Still exploring. |
| C03 `priority_ranking` | What should your agent put first when balancing options? | Select up to 5 priorities, then rank selected items | Affordability — keep my costs down; Group togetherness — share experiences; Time efficiency — reduce travel and waiting; Comfort — avoid exhausting or inconvenient plans; Experience quality — choose memorable experiences; Discovery — try something new. Provide drag handles plus keyboard-accessible move-up/down buttons. Unselected items are unranked, not unwanted. |
| C04 `spending_style` | How do you usually like to spend on travel and outings? | Single-select cards | Keep costs as low as reasonably possible; Balance cost and comfort; Save on basics and splurge on what matters; Prioritize the experience even if it costs more; It depends on the plan. This is a spending tendency, not a budget or permission to exceed one. |
| C05 `daily_pace` | What does your ideal day away look like? | Single-select cards | Slow and spacious — one main activity and plenty of downtime; Balanced — a few activities with breaks; Full and active — see and do as much as possible; A mix of busy and restful days; It depends. |
| C06 `planning_style` | How much of a plan helps you enjoy yourself? | Single-select cards | A detailed schedule in advance; Book the essentials and leave room to explore; A loose outline; Decide as we go; It depends. |
| C07 `group_style` | How do you like to spend time with a travel group? | Single-select cards | Mostly together; Together for key activities, with room to split up; Plenty of independent time, then meet up; It depends on who I’m with. |
| C08 `persistent_needs` | Are there any needs or boundaries your agent should always consider? | Multi-select checkboxes with inline fields | Food allergies; Dietary requirements; Mobility or accessibility needs; Sensory or rest needs; Religious or cultural needs; Other non-negotiables; None to add right now; Prefer not to answer. Selecting a category opens the matching structured fields in N01–N05 below. No diagnosis or explanation is required. Users can save a category and add details later; an unresolved category needs clarification before an agent treats an option as suitable. |
| C09 `consultation_style` | When options involve a compromise, how would you like your agent to involve you? | Single-select cards | Ask me before suggesting a compromise on my behalf; Suggest compromises within my stated preferences, then let me review; It depends — ask me for each plan. Helper text: “Your agent always asks before changing a hard requirement. You approve the final plan and any purchase.” This records a preference; confirm actual participation permissions separately for each plan. |

Closing copy: **“Your agent has a starting picture of you. Add more detail now, or come back whenever you like.”** Show an editable summary of answered questions and offer **Finish** and **Add more about me**.

## 2. Optional: identity and agent customization

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| A01 `profile_avatar` | Want to add a photo or choose an avatar? | Image upload or avatar picker | Choose a provided avatar, upload an image, or keep initials. |
| A02 `agent_name` | What would you like to call your agent? | Short text | Optional, up to 50 characters. Offer “Choose for me.” |
| A03 `agent_avatar` | Which avatar should represent your agent? | Avatar picker | Provided avatars; optional default. |
| A04 `agent_personality` | How should your agent approach planning with others? | Single-select cards | Diplomatic — look for common ground; Analytical — explain tradeoffs clearly; Adventurous — suggest fresh possibilities; Assertive — speak up clearly for my needs; Balanced. Personality affects presentation and suggestions, not permissions or constraint enforcement. |
| A05 `communication_style` | How would you like your agent to talk with you? | Two single-select groups | Detail: Just the essentials / A short explanation / Walk me through the options. Tone: Straightforward / Warm and friendly / Playful. Store each independently. |
| A06 `home_base` | Where do you usually start your trips or outings? | City autocomplete, with optional airport multi-select | Save city, region, and country; optional usual departure airports. Allow manual city entry. Do not request an exact home address. |

## 3. Optional: experiences and daily rhythm

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| E01 `destination_settings` | What kinds of places draw you in? | Multi-select chips, choose up to 4 | Big cities; Small towns; Coast and islands; Mountains; Countryside; Forests and parks; Lakes and rivers; Desert landscapes; No strong preference; Other. |
| E02 `weather_preference` | What weather do you usually enjoy traveling in? | Multi-select chips | Warm or tropical; Mild or temperate; Cool; Snowy or wintry; No strong preference. Optional text: “Any weather you tend to avoid?” |
| E03 `novelty_preference` | How do you feel about unfamiliar places and experiences? | Single-select cards | Prefer familiar and predictable; Mostly familiar with a little discovery; A mix of familiar and new; Seek out new experiences; It depends on the activity. |
| E04 `activity_intensity` | What level of physical activity do you usually enjoy? | Single-select cards | Mostly seated or low exertion; Gentle outings with breaks; Moderate activity; Strenuous activities; It depends. This describes enjoyment; accessibility requirements are recorded separately. |
| E05 `daily_rhythm` | When do you usually like to be active on a trip? | Two single-select groups | Start: Early mornings / Mid-morning / Late mornings / Flexible. Evenings: Wind down early / Some late nights / Usually stay out late / Flexible. |
| E06 `crowd_preference` | What atmosphere do you usually enjoy? | Single-select cards | Quiet and uncrowded; Some buzz without big crowds; Busy and lively; A mix; No strong preference. |
| E07 `food_experiences` | What makes eating out enjoyable for you? | Multi-select chips, choose up to 4 | Local specialties; Street food and markets; Casual and familiar; Fine dining; Trying unfamiliar dishes; Cooking or grocery shopping; Cafés and bakeries; Easy options near the plan; No strong preference; Other. Dietary needs always take precedence. |
| E08 `nightlife_preferences` | Which evening activities appeal to you? | Multi-select chips | Quiet dinners; Shows and performances; Live music; Bars or pubs; Clubs and dancing; Alcohol-free social spaces; Relaxing at the accommodation; No strong preference; Other. These choices do not imply alcohol consumption. |
| E09 `travel_patterns` | What kinds of trips or outings do you usually enjoy? | Multi-select chips | Local day outings; Weekend getaways; Longer vacations; Road trips; City breaks; Nature or adventure trips; Resorts or retreats; Visiting friends or family; Still figuring it out; Other. |

## 4. Optional: transportation and accommodation

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| T01 `transport_preferences` | How do you usually like to get around? | Multi-select chips | Walking or rolling; Public transit; Trains; Cycling; Rental car; Rideshare or taxis; Organized transfers; No strong preference; Other. Separate optional multi-select: “Which modes would you rather avoid?” Do not allow the same mode in both lists. |
| T02 `driving_preference` | How do you feel about driving during a trip? | Single-select cards | Happy to drive; Willing if needed; Prefer to be a passenger; Driving is not an option for me; It depends. Does not establish license eligibility, vehicle access, or commitment to drive for a specific trip. |
| T03 `flight_tradeoffs` | What do you usually prioritize when choosing flights? | Multi-select chips, choose up to 3 | Lowest total cost; Nonstop routes; Convenient departure times; Shorter total journey; A preferred airport; Extra legroom or comfort; Flexible or refundable fares; No strong preference; I don’t fly. Optional text for airports or other details. |
| T04 `baggage_habit` | What do you usually bring when you fly? | Single-select cards | Personal item only; Carry-on and personal item; Usually a checked bag; Specialty or additional baggage; It depends; I don’t fly. Optional details for specialty baggage. Reconfirm for each trip. |
| T05 `accommodation_types` | Where do you usually like to stay? | Multi-select chips | Hotels; Apartments or vacation rentals; Resorts; Hostels; Guesthouses or bed-and-breakfasts; Cabins; Camping or glamping; No strong preference; Other. |
| T06 `sleeping_privacy` | What sleeping arrangement usually works for you? | Single-select cards | I need my own room; Shared room is fine with my own bed; Sharing a bed can be okay with someone I choose; It depends on the group; No preference yet. Explicitly confirm whether “own room” or “own bed” is a requirement or preference. Never infer a roommate or bed-sharing agreement. |
| T07 `accommodation_priorities` | What matters most about where you stay? | Multi-select chips, choose up to 4 | Low total price; Central location; Quiet sleep; Comfortable bed; Private bathroom; Kitchen; Air conditioning or heating; Cleanliness; Pool or spa; Easy access to transit; Flexible cancellation; No strong preference; Other. Accessibility requirements are captured in N03. |
| T08 `location_tradeoff` | Where would you usually rather stay? | Single-select cards | Close to activities even if it costs more; Farther out if it saves money; Somewhere quieter even with more travel; Balance location, cost, and comfort; It depends. |

## 5. Optional: personal needs and firm boundaries

These are the same fields expanded by C08, not duplicate questions. Each section is optional and private by default. Ask for practical planning needs rather than medical history. A saved category without usable details is incomplete, not a satisfied requirement.

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| N01 `food_allergies` | Are there foods or ingredients that must be avoided? | Repeatable structured entry | Ingredient or food: short text. “What should we check?”: multi-select — Ingredient avoidance / Cross-contact precautions / Contact the venue to confirm / Ask me before choosing. Optional practical instructions. Treat saved allergies as requirements; do not infer that an unverified venue is safe. |
| N02 `dietary_requirements` | What eating preferences or requirements should we plan around? | Multi-select chips plus per-item requirement selector | Vegetarian; Vegan; Pescatarian; Halal; Kosher; Gluten-free; Dairy-free; Other. For each: Must be accommodated / Prefer when possible. Optional details about what accommodation means for the user. Allergies belong in N01. |
| N03 `accessibility_needs` | What would make places, activities, and travel work for you? | Multi-select checkboxes plus optional practical details | Step-free routes; Wheelchair-accessible transportation; Accessible bathroom or room; Short walking or rolling distances; Seating and regular breaks; Avoid stairs or steep terrain; Hearing or captioning support; Visual or navigation support; Service animal accommodation; Other. For each selected item: Required / Preferred. Allow custom details and “Please ask me privately.” |
| N04 `sensory_rest_needs` | Are there sensory or rest needs we should account for? | Multi-select checkboxes plus per-item requirement selector | Quiet environments; Avoid flashing lights; Avoid crowded spaces; Regular downtime; Predictable meal breaks; Avoid strong scents; Other. Mark each Required / Preferred; optional practical instructions. |
| N05 `other_boundaries` | Is there anything else your agent should always respect? | Repeatable short text plus Required / Preferred selector | Examples: smoke-free accommodation, prayer time, avoiding animal attractions, no water activities, or a need to return early. Optional note: “You do not need to explain why.” Do not request passport details, diagnoses, or financial account information. |

## 6. Optional: negotiation and group decisions

These refine C03 and C09. They describe how to propose options; joining a room still requires explicit confirmation of participation settings.

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| G01 `convenience_spending` | How open are you to paying more to save time or effort? | Single-select cards | Usually prefer the cheaper option; Sometimes, for a clear benefit; Often, if it makes the experience easier; Ask me each time. Always remain within the budget confirmed for that plan. |
| G02 `personal_group_tradeoff` | If your preferred option differs from the group’s, what should your agent usually suggest? | Single-select cards | Advocate for my preference first; Look for a middle ground; Lean toward the group if my requirements are met; Offer a separate option for me when practical; Ask me. No answer permits imposing extra cost or changing a requirement. |
| G03 `comfortable_compromises` | Which kinds of compromises are you usually open to considering? | Multi-select chips | Different activity; Different restaurant or cuisine; Different accommodation type; Less central location; Earlier or later start; More travel time; Splitting up for part of the day; Paying more within my confirmed budget; Ask me about every compromise; Other. Selection means open to a proposal, not advance consent. |
| G04 `extra_consultation_triggers` | Besides changes to firm requirements, what should your agent always check with you about? | Multi-select checkboxes | Any increase over my preferred spending target; Changes to sleeping arrangements; Early departures or late arrivals; Physically demanding activities; Splitting from the group; Changes to transport; Any compromise on a stated preference; Other. These add consultation triggers; they cannot remove mandatory approval steps. |
| G05 `cost_sharing_preference` | How do you usually prefer shared costs to be handled? | Single-select cards | Everyone pays for what they use; Split shared costs equally when agreed; A mix depending on the expense; Discuss it for each plan. This is a preference, not permission to charge anyone. |
| G06 `group_size_preference` | What size group do you usually enjoy? | Multi-select chips | One other person; Small group of 3–5 people total; Medium group of 6–10; Larger group of 11+; No strong preference. Actual companions and headcount belong to each plan. |

## 7. Optional: the details structured choices miss

| ID / profile key | Question shown to the user | Input | Options and behavior |
| --- | --- | --- | --- |
| F01 `great_experience_example` | Think of a trip or outing you loved. What made it work for you? | Long text, optional, up to 1,000 characters | Helper: “A quiet morning, an amazing meal, time with friends, a packed itinerary — whatever mattered to you.” Use as context, not automatic permission or a hard constraint. |
| F02 `avoid_experience_example` | What tends to make a trip or group outing less enjoyable for you? | Long text, optional, up to 1,000 characters | Helper: “For example, rushed mornings, surprise costs, too much transit, or no time alone.” Offer a visible way to add a firm boundary through N05. |
| F03 `anything_else` | What else would you like your agent to understand about you? | Long text, optional, up to 1,000 characters | Capture nuance, conflicting preferences, or context that the choices missed. Do not silently turn text into permissions; confirm any extracted requirement with the user. |

## Keep these out of the persistent onboarding questionnaire

Collect the following when creating or joining a particular plan. Existing profile answers can be suggested, but should be checked for that context.

| Information | Appropriate input when planning |
| --- | --- |
| Destination, specific venues, and purpose of the plan | Search/autocomplete, activity selector, optional text |
| Exact dates, availability, duration, and flexibility | Date/time or date-range picker plus availability controls |
| Personal spending target and absolute cap | Currency selector plus numeric amounts, explicitly per person and per trip/day as applicable, with included expenses stated |
| Actual departure point, baggage, transport availability, and booking needs | Location search and structured trip-specific selections |
| Companions, children or dependents, room assignments, and pet participation | Participant selection, counts, and specific needs; avoid inferring these from the profile |
| Passport, visa, or other entry eligibility when relevant | A targeted eligibility check during planning; do not collect identity documents in general onboarding |
| Room participation permissions and permission to communicate particular needs | Explicit controls with a plain-language preview of what the agent may do and disclose |
| Changed circumstances, exceptions, and proposed relaxation of a requirement | A targeted question showing the exact change and its consequences, requiring explicit confirmation |
| Final plan approval and purchases | Explicit human confirmation for the specific plan or transaction; never inferred from profile settings |

## Review and storage notes for implementation

- Keep stable IDs and profile keys so wording can evolve without breaking saved answers. Store multi-selects as arrays, rankings as ordered arrays, and requirement strength separately from the answer value.
- Keep skipped, explicit “no preference,” and explicit “none” distinct. Do not use missing accessibility or dietary answers as evidence of no needs.
- Track whether an answer is user-confirmed, when it was updated, and whether it is a profile default or plan-specific override. Do not overwrite a persistent profile with a one-time exception.
- On the review screen, show **What I enjoy**, **What I prioritize**, **What I need**, and **When to ask me**, with direct edit links. Mark unanswered areas as “Not shared yet.”
- If answers conflict, ask the user to resolve the specific conflict before using it in a plan. Requirements outrank soft preferences; ranking affordability first never relaxes an allergy or accessibility requirement.
- Do not silently equate personality or consultation answers with existing backend participation permission flags. Map them deliberately when building the UI and confirm permissions in the relevant room.
- This file is a question and input specification. It does not implement the form, data model, or permission flow.
