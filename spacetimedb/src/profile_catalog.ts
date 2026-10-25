// Generated from contracts/ by scripts/sync-catalogs.py. Do not edit.

export const PROFILE_CATALOG = {
  "version": 1,
  "intro": "Let's help your agent get to know you. Share what you usually enjoy, what matters most, and what you need us to respect. You can skip questions and change your answers anytime.",
  "outro": "Your agent has a starting picture of you. Add more detail now, or come back whenever you like.",
  "sections": [
    {
      "id": "core",
      "title": "Get started",
      "optional": false
    },
    {
      "id": "identity",
      "title": "You and your agent",
      "optional": true
    },
    {
      "id": "experiences",
      "title": "Experiences and daily rhythm",
      "optional": true
    },
    {
      "id": "travel",
      "title": "Getting around and where you stay",
      "optional": true
    },
    {
      "id": "needs",
      "title": "Needs and firm boundaries",
      "optional": true,
      "private_note": "Private by default. Your agent only ever shares the minimum planning requirement, never why."
    },
    {
      "id": "negotiation",
      "title": "Negotiation and group decisions",
      "optional": true
    },
    {
      "id": "details",
      "title": "The details choices miss",
      "optional": true
    }
  ],
  "review_groups": [
    {
      "id": "enjoy",
      "title": "What I enjoy",
      "questions": [
        "C02",
        "E01",
        "E02",
        "E03",
        "E04",
        "E05",
        "E06",
        "E07",
        "E08",
        "E09",
        "T05",
        "F01",
        "F02"
      ]
    },
    {
      "id": "prioritize",
      "title": "What I prioritize",
      "questions": [
        "C03",
        "C04",
        "C05",
        "C06",
        "C07",
        "T03",
        "T07",
        "T08",
        "G01",
        "G02",
        "G03",
        "G05",
        "G06"
      ]
    },
    {
      "id": "need",
      "title": "What I need",
      "questions": [
        "C08",
        "N01",
        "N02",
        "N03",
        "N04",
        "N05",
        "T01",
        "T02",
        "T04",
        "T06",
        "A06"
      ]
    },
    {
      "id": "ask",
      "title": "When to ask me",
      "questions": [
        "C09",
        "G04"
      ]
    },
    {
      "id": "about",
      "title": "About me and my agent",
      "questions": [
        "C01",
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "F03"
      ]
    }
  ],
  "questions": [
    {
      "id": "C01",
      "key": "display_name",
      "section": "core",
      "prompt": "What should we call you?",
      "input": "text",
      "required": true,
      "min_length": 1,
      "max_length": 50
    },
    {
      "id": "C02",
      "key": "activity_interests",
      "section": "core",
      "prompt": "What do you most enjoy doing when you get away?",
      "input": "multi",
      "max": 5,
      "options": [
        {
          "id": "food",
          "label": "Food and local restaurants"
        },
        {
          "id": "nature",
          "label": "Nature and scenery"
        },
        {
          "id": "hiking",
          "label": "Hiking and outdoor adventures"
        },
        {
          "id": "beaches",
          "label": "Beaches and water"
        },
        {
          "id": "art_history",
          "label": "Art, museums, and history"
        },
        {
          "id": "local_culture",
          "label": "Local culture and neighborhoods"
        },
        {
          "id": "wellness",
          "label": "Rest and wellness"
        },
        {
          "id": "nightlife",
          "label": "Nightlife and live music"
        },
        {
          "id": "sports",
          "label": "Sports and games"
        },
        {
          "id": "shopping",
          "label": "Shopping and markets"
        },
        {
          "id": "classes",
          "label": "Classes and hands-on experiences"
        },
        {
          "id": "family",
          "label": "Family activities"
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        },
        {
          "id": "still_exploring",
          "label": "Still exploring",
          "exclusive": true
        }
      ]
    },
    {
      "id": "C03",
      "key": "priority_ranking",
      "section": "core",
      "prompt": "What should your agent put first when balancing options?",
      "input": "rank",
      "max": 5,
      "helper": "Pick up to five, then put them in order. Anything you don't pick is unranked, not unwanted.",
      "options": [
        {
          "id": "affordability",
          "label": "Affordability",
          "hint": "Keep my costs down"
        },
        {
          "id": "togetherness",
          "label": "Group togetherness",
          "hint": "Share experiences"
        },
        {
          "id": "time_efficiency",
          "label": "Time efficiency",
          "hint": "Reduce travel and waiting"
        },
        {
          "id": "comfort",
          "label": "Comfort",
          "hint": "Avoid exhausting or inconvenient plans"
        },
        {
          "id": "experience_quality",
          "label": "Experience quality",
          "hint": "Choose memorable experiences"
        },
        {
          "id": "discovery",
          "label": "Discovery",
          "hint": "Try something new"
        }
      ]
    },
    {
      "id": "C04",
      "key": "spending_style",
      "section": "core",
      "prompt": "How do you usually like to spend on travel and outings?",
      "input": "single",
      "helper": "This is a spending tendency, not a budget or permission to exceed one.",
      "options": [
        {
          "id": "low",
          "label": "Keep costs as low as reasonably possible"
        },
        {
          "id": "balance",
          "label": "Balance cost and comfort"
        },
        {
          "id": "save_and_splurge",
          "label": "Save on basics and splurge on what matters"
        },
        {
          "id": "experience_first",
          "label": "Prioritize the experience even if it costs more"
        },
        {
          "id": "depends",
          "label": "It depends on the plan"
        }
      ]
    },
    {
      "id": "C05",
      "key": "daily_pace",
      "section": "core",
      "prompt": "What does your ideal day away look like?",
      "input": "single",
      "options": [
        {
          "id": "slow",
          "label": "Slow and spacious",
          "hint": "One main activity and plenty of downtime"
        },
        {
          "id": "balanced",
          "label": "Balanced",
          "hint": "A few activities with breaks"
        },
        {
          "id": "full",
          "label": "Full and active",
          "hint": "See and do as much as possible"
        },
        {
          "id": "mix",
          "label": "A mix of busy and restful days"
        },
        {
          "id": "depends",
          "label": "It depends"
        }
      ]
    },
    {
      "id": "C06",
      "key": "planning_style",
      "section": "core",
      "prompt": "How much of a plan helps you enjoy yourself?",
      "input": "single",
      "options": [
        {
          "id": "detailed",
          "label": "A detailed schedule in advance"
        },
        {
          "id": "essentials",
          "label": "Book the essentials and leave room to explore"
        },
        {
          "id": "loose",
          "label": "A loose outline"
        },
        {
          "id": "as_we_go",
          "label": "Decide as we go"
        },
        {
          "id": "depends",
          "label": "It depends"
        }
      ]
    },
    {
      "id": "C07",
      "key": "group_style",
      "section": "core",
      "prompt": "How do you like to spend time with a travel group?",
      "input": "single",
      "options": [
        {
          "id": "together",
          "label": "Mostly together"
        },
        {
          "id": "key_together",
          "label": "Together for key activities, with room to split up"
        },
        {
          "id": "independent",
          "label": "Plenty of independent time, then meet up"
        },
        {
          "id": "depends",
          "label": "It depends on who I'm with"
        }
      ]
    },
    {
      "id": "C08",
      "key": "persistent_needs",
      "section": "core",
      "prompt": "Are there any needs or boundaries your agent should always consider?",
      "input": "multi",
      "helper": "Picking one opens a few details you can fill in now or later. No diagnosis or explanation needed.",
      "options": [
        {
          "id": "food_allergies",
          "label": "Food allergies",
          "expands": "N01"
        },
        {
          "id": "dietary",
          "label": "Dietary requirements",
          "expands": "N02"
        },
        {
          "id": "accessibility",
          "label": "Mobility or accessibility needs",
          "expands": "N03"
        },
        {
          "id": "sensory_rest",
          "label": "Sensory or rest needs",
          "expands": "N04"
        },
        {
          "id": "religious_cultural",
          "label": "Religious or cultural needs",
          "expands": "N05"
        },
        {
          "id": "other",
          "label": "Other non-negotiables",
          "expands": "N05"
        },
        {
          "id": "none",
          "label": "None to add right now",
          "exclusive": true
        },
        {
          "id": "prefer_not",
          "label": "Prefer not to answer",
          "exclusive": true
        }
      ]
    },
    {
      "id": "C09",
      "key": "consultation_style",
      "section": "core",
      "prompt": "When options involve a compromise, how would you like your agent to involve you?",
      "input": "single",
      "helper": "Your agent always asks before changing a hard requirement. You approve the final plan and any purchase.",
      "options": [
        {
          "id": "ask_first",
          "label": "Ask me before suggesting a compromise on my behalf"
        },
        {
          "id": "suggest_then_review",
          "label": "Suggest compromises within my stated preferences, then let me review"
        },
        {
          "id": "ask_per_plan",
          "label": "It depends — ask me for each plan"
        }
      ]
    },
    {
      "id": "A01",
      "key": "profile_avatar",
      "section": "identity",
      "prompt": "Want to choose an avatar?",
      "input": "avatar"
    },
    {
      "id": "A02",
      "key": "agent_name",
      "section": "identity",
      "prompt": "What would you like to call your agent?",
      "input": "text",
      "max_length": 50,
      "choose_for_me": true
    },
    {
      "id": "A03",
      "key": "agent_avatar",
      "section": "identity",
      "prompt": "Which avatar should represent your agent?",
      "input": "avatar"
    },
    {
      "id": "A04",
      "key": "agent_personality",
      "section": "identity",
      "prompt": "How should your agent approach planning with others?",
      "input": "single",
      "helper": "This shapes how your agent presents and suggests, never what it's allowed to do.",
      "options": [
        {
          "id": "diplomatic",
          "label": "Diplomatic",
          "hint": "Look for common ground"
        },
        {
          "id": "analytical",
          "label": "Analytical",
          "hint": "Explain tradeoffs clearly"
        },
        {
          "id": "adventurous",
          "label": "Adventurous",
          "hint": "Suggest fresh possibilities"
        },
        {
          "id": "assertive",
          "label": "Assertive",
          "hint": "Speak up clearly for my needs"
        },
        {
          "id": "balanced",
          "label": "Balanced"
        }
      ]
    },
    {
      "id": "A05",
      "key": "communication_style",
      "section": "identity",
      "prompt": "How would you like your agent to talk with you?",
      "input": "groups",
      "groups": [
        {
          "id": "detail",
          "label": "Detail",
          "options": [
            {
              "id": "essentials",
              "label": "Just the essentials"
            },
            {
              "id": "short",
              "label": "A short explanation"
            },
            {
              "id": "walkthrough",
              "label": "Walk me through the options"
            }
          ]
        },
        {
          "id": "tone",
          "label": "Tone",
          "options": [
            {
              "id": "straightforward",
              "label": "Straightforward"
            },
            {
              "id": "warm",
              "label": "Warm and friendly"
            },
            {
              "id": "playful",
              "label": "Playful"
            }
          ]
        }
      ]
    },
    {
      "id": "A06",
      "key": "home_base",
      "section": "identity",
      "prompt": "Where do you usually start your trips or outings?",
      "input": "home_base",
      "helper": "A city is enough. Never your exact address."
    },
    {
      "id": "E01",
      "key": "destination_settings",
      "section": "experiences",
      "prompt": "What kinds of places draw you in?",
      "input": "multi",
      "max": 4,
      "options": [
        {
          "id": "big_cities",
          "label": "Big cities"
        },
        {
          "id": "small_towns",
          "label": "Small towns"
        },
        {
          "id": "coast",
          "label": "Coast and islands"
        },
        {
          "id": "mountains",
          "label": "Mountains"
        },
        {
          "id": "countryside",
          "label": "Countryside"
        },
        {
          "id": "forests",
          "label": "Forests and parks"
        },
        {
          "id": "lakes",
          "label": "Lakes and rivers"
        },
        {
          "id": "desert",
          "label": "Desert landscapes"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "E02",
      "key": "weather_preference",
      "section": "experiences",
      "prompt": "What weather do you usually enjoy traveling in?",
      "input": "multi",
      "text_field": {
        "id": "avoid",
        "label": "Any weather you tend to avoid?"
      },
      "options": [
        {
          "id": "warm",
          "label": "Warm or tropical"
        },
        {
          "id": "mild",
          "label": "Mild or temperate"
        },
        {
          "id": "cool",
          "label": "Cool"
        },
        {
          "id": "snowy",
          "label": "Snowy or wintry"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        }
      ]
    },
    {
      "id": "E03",
      "key": "novelty_preference",
      "section": "experiences",
      "prompt": "How do you feel about unfamiliar places and experiences?",
      "input": "single",
      "options": [
        {
          "id": "familiar",
          "label": "Prefer familiar and predictable"
        },
        {
          "id": "mostly_familiar",
          "label": "Mostly familiar with a little discovery"
        },
        {
          "id": "mix",
          "label": "A mix of familiar and new"
        },
        {
          "id": "seek_new",
          "label": "Seek out new experiences"
        },
        {
          "id": "depends",
          "label": "It depends on the activity"
        }
      ]
    },
    {
      "id": "E04",
      "key": "activity_intensity",
      "section": "experiences",
      "prompt": "What level of physical activity do you usually enjoy?",
      "input": "single",
      "helper": "This is about enjoyment. Accessibility needs have their own place.",
      "options": [
        {
          "id": "low",
          "label": "Mostly seated or low exertion"
        },
        {
          "id": "gentle",
          "label": "Gentle outings with breaks"
        },
        {
          "id": "moderate",
          "label": "Moderate activity"
        },
        {
          "id": "strenuous",
          "label": "Strenuous activities"
        },
        {
          "id": "depends",
          "label": "It depends"
        }
      ]
    },
    {
      "id": "E05",
      "key": "daily_rhythm",
      "section": "experiences",
      "prompt": "When do you usually like to be active on a trip?",
      "input": "groups",
      "groups": [
        {
          "id": "start",
          "label": "Start",
          "options": [
            {
              "id": "early",
              "label": "Early mornings"
            },
            {
              "id": "mid",
              "label": "Mid-morning"
            },
            {
              "id": "late",
              "label": "Late mornings"
            },
            {
              "id": "flexible",
              "label": "Flexible"
            }
          ]
        },
        {
          "id": "evenings",
          "label": "Evenings",
          "options": [
            {
              "id": "early",
              "label": "Wind down early"
            },
            {
              "id": "some_late",
              "label": "Some late nights"
            },
            {
              "id": "late",
              "label": "Usually stay out late"
            },
            {
              "id": "flexible",
              "label": "Flexible"
            }
          ]
        }
      ]
    },
    {
      "id": "E06",
      "key": "crowd_preference",
      "section": "experiences",
      "prompt": "What atmosphere do you usually enjoy?",
      "input": "single",
      "options": [
        {
          "id": "quiet",
          "label": "Quiet and uncrowded"
        },
        {
          "id": "some_buzz",
          "label": "Some buzz without big crowds"
        },
        {
          "id": "lively",
          "label": "Busy and lively"
        },
        {
          "id": "mix",
          "label": "A mix"
        },
        {
          "id": "no_preference",
          "label": "No strong preference"
        }
      ]
    },
    {
      "id": "E07",
      "key": "food_experiences",
      "section": "experiences",
      "prompt": "What makes eating out enjoyable for you?",
      "input": "multi",
      "max": 4,
      "helper": "Dietary needs always take precedence.",
      "options": [
        {
          "id": "local",
          "label": "Local specialties"
        },
        {
          "id": "street_food",
          "label": "Street food and markets"
        },
        {
          "id": "casual",
          "label": "Casual and familiar"
        },
        {
          "id": "fine_dining",
          "label": "Fine dining"
        },
        {
          "id": "unfamiliar",
          "label": "Trying unfamiliar dishes"
        },
        {
          "id": "cooking",
          "label": "Cooking or grocery shopping"
        },
        {
          "id": "cafes",
          "label": "Cafés and bakeries"
        },
        {
          "id": "easy",
          "label": "Easy options near the plan"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "E08",
      "key": "nightlife_preferences",
      "section": "experiences",
      "prompt": "Which evening activities appeal to you?",
      "input": "multi",
      "helper": "These choices don't imply drinking.",
      "options": [
        {
          "id": "quiet_dinners",
          "label": "Quiet dinners"
        },
        {
          "id": "shows",
          "label": "Shows and performances"
        },
        {
          "id": "live_music",
          "label": "Live music"
        },
        {
          "id": "bars",
          "label": "Bars or pubs"
        },
        {
          "id": "clubs",
          "label": "Clubs and dancing"
        },
        {
          "id": "alcohol_free",
          "label": "Alcohol-free social spaces"
        },
        {
          "id": "relaxing",
          "label": "Relaxing at the accommodation"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "E09",
      "key": "travel_patterns",
      "section": "experiences",
      "prompt": "What kinds of trips or outings do you usually enjoy?",
      "input": "multi",
      "options": [
        {
          "id": "day_outings",
          "label": "Local day outings"
        },
        {
          "id": "weekends",
          "label": "Weekend getaways"
        },
        {
          "id": "vacations",
          "label": "Longer vacations"
        },
        {
          "id": "road_trips",
          "label": "Road trips"
        },
        {
          "id": "city_breaks",
          "label": "City breaks"
        },
        {
          "id": "adventure",
          "label": "Nature or adventure trips"
        },
        {
          "id": "resorts",
          "label": "Resorts or retreats"
        },
        {
          "id": "visiting",
          "label": "Visiting friends or family"
        },
        {
          "id": "figuring_out",
          "label": "Still figuring it out",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "T01",
      "key": "transport_preferences",
      "section": "travel",
      "prompt": "How do you usually like to get around?",
      "input": "multi",
      "avoid_list": {
        "label": "Which would you rather avoid?"
      },
      "options": [
        {
          "id": "walking",
          "label": "Walking or rolling"
        },
        {
          "id": "transit",
          "label": "Public transit"
        },
        {
          "id": "trains",
          "label": "Trains"
        },
        {
          "id": "cycling",
          "label": "Cycling"
        },
        {
          "id": "rental_car",
          "label": "Rental car"
        },
        {
          "id": "rideshare",
          "label": "Rideshare or taxis"
        },
        {
          "id": "transfers",
          "label": "Organized transfers"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "T02",
      "key": "driving_preference",
      "section": "travel",
      "prompt": "How do you feel about driving during a trip?",
      "input": "single",
      "helper": "This isn't a promise to drive on any particular trip.",
      "options": [
        {
          "id": "happy",
          "label": "Happy to drive"
        },
        {
          "id": "if_needed",
          "label": "Willing if needed"
        },
        {
          "id": "passenger",
          "label": "Prefer to be a passenger"
        },
        {
          "id": "not_an_option",
          "label": "Driving is not an option for me"
        },
        {
          "id": "depends",
          "label": "It depends"
        }
      ]
    },
    {
      "id": "T03",
      "key": "flight_tradeoffs",
      "section": "travel",
      "prompt": "What do you usually prioritize when choosing flights?",
      "input": "multi",
      "max": 3,
      "text_field": {
        "id": "details",
        "label": "Airports or other details"
      },
      "options": [
        {
          "id": "lowest_cost",
          "label": "Lowest total cost"
        },
        {
          "id": "nonstop",
          "label": "Nonstop routes"
        },
        {
          "id": "convenient_times",
          "label": "Convenient departure times"
        },
        {
          "id": "shorter",
          "label": "Shorter total journey"
        },
        {
          "id": "preferred_airport",
          "label": "A preferred airport"
        },
        {
          "id": "comfort",
          "label": "Extra legroom or comfort"
        },
        {
          "id": "flexible",
          "label": "Flexible or refundable fares"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "dont_fly",
          "label": "I don't fly",
          "exclusive": true
        }
      ]
    },
    {
      "id": "T04",
      "key": "baggage_habit",
      "section": "travel",
      "prompt": "What do you usually bring when you fly?",
      "input": "single",
      "text_field": {
        "id": "details",
        "label": "Specialty baggage details"
      },
      "helper": "Your agent still checks this for each trip.",
      "options": [
        {
          "id": "personal_item",
          "label": "Personal item only"
        },
        {
          "id": "carry_on",
          "label": "Carry-on and personal item"
        },
        {
          "id": "checked",
          "label": "Usually a checked bag"
        },
        {
          "id": "specialty",
          "label": "Specialty or additional baggage"
        },
        {
          "id": "depends",
          "label": "It depends"
        },
        {
          "id": "dont_fly",
          "label": "I don't fly"
        }
      ]
    },
    {
      "id": "T05",
      "key": "accommodation_types",
      "section": "travel",
      "prompt": "Where do you usually like to stay?",
      "input": "multi",
      "options": [
        {
          "id": "hotels",
          "label": "Hotels"
        },
        {
          "id": "rentals",
          "label": "Apartments or vacation rentals"
        },
        {
          "id": "resorts",
          "label": "Resorts"
        },
        {
          "id": "hostels",
          "label": "Hostels"
        },
        {
          "id": "guesthouses",
          "label": "Guesthouses or bed-and-breakfasts"
        },
        {
          "id": "cabins",
          "label": "Cabins"
        },
        {
          "id": "camping",
          "label": "Camping or glamping"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "T06",
      "key": "sleeping_privacy",
      "section": "travel",
      "prompt": "What sleeping arrangement usually works for you?",
      "input": "single",
      "strength": true,
      "helper": "Tell us whether it's a must or a preference. We never assume who shares with whom.",
      "options": [
        {
          "id": "own_room",
          "label": "I need my own room",
          "needs_strength": true
        },
        {
          "id": "own_bed",
          "label": "Shared room is fine with my own bed",
          "needs_strength": true
        },
        {
          "id": "bed_sharing_ok",
          "label": "Sharing a bed can be okay with someone I choose"
        },
        {
          "id": "depends",
          "label": "It depends on the group"
        },
        {
          "id": "no_preference",
          "label": "No preference yet"
        }
      ]
    },
    {
      "id": "T07",
      "key": "accommodation_priorities",
      "section": "travel",
      "prompt": "What matters most about where you stay?",
      "input": "multi",
      "max": 4,
      "options": [
        {
          "id": "price",
          "label": "Low total price"
        },
        {
          "id": "central",
          "label": "Central location"
        },
        {
          "id": "quiet",
          "label": "Quiet sleep"
        },
        {
          "id": "bed",
          "label": "Comfortable bed"
        },
        {
          "id": "private_bathroom",
          "label": "Private bathroom"
        },
        {
          "id": "kitchen",
          "label": "Kitchen"
        },
        {
          "id": "climate",
          "label": "Air conditioning or heating"
        },
        {
          "id": "clean",
          "label": "Cleanliness"
        },
        {
          "id": "pool",
          "label": "Pool or spa"
        },
        {
          "id": "transit",
          "label": "Easy access to transit"
        },
        {
          "id": "cancellation",
          "label": "Flexible cancellation"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "T08",
      "key": "location_tradeoff",
      "section": "travel",
      "prompt": "Where would you usually rather stay?",
      "input": "single",
      "options": [
        {
          "id": "close",
          "label": "Close to activities even if it costs more"
        },
        {
          "id": "farther_cheaper",
          "label": "Farther out if it saves money"
        },
        {
          "id": "quieter",
          "label": "Somewhere quieter even with more travel"
        },
        {
          "id": "balance",
          "label": "Balance location, cost, and comfort"
        },
        {
          "id": "depends",
          "label": "It depends"
        }
      ]
    },
    {
      "id": "N01",
      "key": "food_allergies",
      "section": "needs",
      "prompt": "Are there foods or ingredients that must be avoided?",
      "input": "allergies",
      "private": true,
      "helper": "Treated as a requirement. No venue is assumed safe until checked.",
      "checks": [
        {
          "id": "avoid_ingredient",
          "label": "Ingredient avoidance"
        },
        {
          "id": "cross_contact",
          "label": "Cross-contact precautions"
        },
        {
          "id": "contact_venue",
          "label": "Contact the venue to confirm"
        },
        {
          "id": "ask_me",
          "label": "Ask me before choosing"
        }
      ]
    },
    {
      "id": "N02",
      "key": "dietary_requirements",
      "section": "needs",
      "prompt": "What eating preferences or requirements should we plan around?",
      "input": "needs",
      "private": true,
      "helper": "Allergies have their own question.",
      "options": [
        {
          "id": "vegetarian",
          "label": "Vegetarian"
        },
        {
          "id": "vegan",
          "label": "Vegan"
        },
        {
          "id": "pescatarian",
          "label": "Pescatarian"
        },
        {
          "id": "halal",
          "label": "Halal"
        },
        {
          "id": "kosher",
          "label": "Kosher"
        },
        {
          "id": "gluten_free",
          "label": "Gluten-free"
        },
        {
          "id": "dairy_free",
          "label": "Dairy-free"
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "N03",
      "key": "accessibility_needs",
      "section": "needs",
      "prompt": "What would make places, activities, and travel work for you?",
      "input": "needs",
      "private": true,
      "ask_privately": true,
      "options": [
        {
          "id": "step_free",
          "label": "Step-free routes"
        },
        {
          "id": "wheelchair_transport",
          "label": "Wheelchair-accessible transportation"
        },
        {
          "id": "accessible_room",
          "label": "Accessible bathroom or room"
        },
        {
          "id": "short_distances",
          "label": "Short walking or rolling distances"
        },
        {
          "id": "breaks",
          "label": "Seating and regular breaks"
        },
        {
          "id": "avoid_stairs",
          "label": "Avoid stairs or steep terrain"
        },
        {
          "id": "hearing",
          "label": "Hearing or captioning support"
        },
        {
          "id": "visual",
          "label": "Visual or navigation support"
        },
        {
          "id": "service_animal",
          "label": "Service animal accommodation"
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "N04",
      "key": "sensory_rest_needs",
      "section": "needs",
      "prompt": "Are there sensory or rest needs we should account for?",
      "input": "needs",
      "private": true,
      "options": [
        {
          "id": "quiet",
          "label": "Quiet environments"
        },
        {
          "id": "no_flashing",
          "label": "Avoid flashing lights"
        },
        {
          "id": "no_crowds",
          "label": "Avoid crowded spaces"
        },
        {
          "id": "downtime",
          "label": "Regular downtime"
        },
        {
          "id": "meal_breaks",
          "label": "Predictable meal breaks"
        },
        {
          "id": "no_scents",
          "label": "Avoid strong scents"
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "N05",
      "key": "other_boundaries",
      "section": "needs",
      "prompt": "Is there anything else your agent should always respect?",
      "input": "boundaries",
      "private": true,
      "helper": "For example: smoke-free accommodation, prayer time, avoiding animal attractions, no water activities, or a need to return early. You don't need to explain why."
    },
    {
      "id": "G01",
      "key": "convenience_spending",
      "section": "negotiation",
      "prompt": "How open are you to paying more to save time or effort?",
      "input": "single",
      "helper": "Always within the budget you confirm for that plan.",
      "options": [
        {
          "id": "cheaper",
          "label": "Usually prefer the cheaper option"
        },
        {
          "id": "clear_benefit",
          "label": "Sometimes, for a clear benefit"
        },
        {
          "id": "often",
          "label": "Often, if it makes the experience easier"
        },
        {
          "id": "ask",
          "label": "Ask me each time"
        }
      ]
    },
    {
      "id": "G02",
      "key": "personal_group_tradeoff",
      "section": "negotiation",
      "prompt": "If your preferred option differs from the group's, what should your agent usually suggest?",
      "input": "single",
      "helper": "No answer here lets your agent add costs or change a requirement.",
      "options": [
        {
          "id": "advocate",
          "label": "Advocate for my preference first"
        },
        {
          "id": "middle",
          "label": "Look for a middle ground"
        },
        {
          "id": "lean_group",
          "label": "Lean toward the group if my requirements are met"
        },
        {
          "id": "separate",
          "label": "Offer a separate option for me when practical"
        },
        {
          "id": "ask",
          "label": "Ask me"
        }
      ]
    },
    {
      "id": "G03",
      "key": "comfortable_compromises",
      "section": "negotiation",
      "prompt": "Which kinds of compromises are you usually open to considering?",
      "input": "multi",
      "helper": "Open to a proposal, not agreeing in advance.",
      "options": [
        {
          "id": "activity",
          "label": "Different activity"
        },
        {
          "id": "restaurant",
          "label": "Different restaurant or cuisine"
        },
        {
          "id": "accommodation",
          "label": "Different accommodation type"
        },
        {
          "id": "location",
          "label": "Less central location"
        },
        {
          "id": "timing",
          "label": "Earlier or later start"
        },
        {
          "id": "travel_time",
          "label": "More travel time"
        },
        {
          "id": "split_up",
          "label": "Splitting up for part of the day"
        },
        {
          "id": "pay_more",
          "label": "Paying more within my confirmed budget"
        },
        {
          "id": "ask_every",
          "label": "Ask me about every compromise",
          "exclusive": true
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "G04",
      "key": "extra_consultation_triggers",
      "section": "negotiation",
      "prompt": "Besides changes to firm requirements, what should your agent always check with you about?",
      "input": "multi",
      "helper": "These add check-ins; they never remove a required approval.",
      "options": [
        {
          "id": "over_target",
          "label": "Any increase over my preferred spending target"
        },
        {
          "id": "sleeping",
          "label": "Changes to sleeping arrangements"
        },
        {
          "id": "early_late",
          "label": "Early departures or late arrivals"
        },
        {
          "id": "demanding",
          "label": "Physically demanding activities"
        },
        {
          "id": "splitting",
          "label": "Splitting from the group"
        },
        {
          "id": "transport",
          "label": "Changes to transport"
        },
        {
          "id": "any_preference",
          "label": "Any compromise on a stated preference"
        },
        {
          "id": "other",
          "label": "Other",
          "other_text": true
        }
      ]
    },
    {
      "id": "G05",
      "key": "cost_sharing_preference",
      "section": "negotiation",
      "prompt": "How do you usually prefer shared costs to be handled?",
      "input": "single",
      "helper": "A preference, never permission to charge anyone.",
      "options": [
        {
          "id": "pay_what_you_use",
          "label": "Everyone pays for what they use"
        },
        {
          "id": "split_equally",
          "label": "Split shared costs equally when agreed"
        },
        {
          "id": "mix",
          "label": "A mix depending on the expense"
        },
        {
          "id": "discuss",
          "label": "Discuss it for each plan"
        }
      ]
    },
    {
      "id": "G06",
      "key": "group_size_preference",
      "section": "negotiation",
      "prompt": "What size group do you usually enjoy?",
      "input": "multi",
      "options": [
        {
          "id": "one",
          "label": "One other person"
        },
        {
          "id": "small",
          "label": "Small group of 3–5 people total"
        },
        {
          "id": "medium",
          "label": "Medium group of 6–10"
        },
        {
          "id": "large",
          "label": "Larger group of 11+"
        },
        {
          "id": "no_preference",
          "label": "No strong preference",
          "exclusive": true
        }
      ]
    },
    {
      "id": "F01",
      "key": "great_experience_example",
      "section": "details",
      "prompt": "Think of a trip or outing you loved. What made it work for you?",
      "input": "long_text",
      "max_length": 1000,
      "helper": "A quiet morning, an amazing meal, time with friends, a packed itinerary — whatever mattered to you."
    },
    {
      "id": "F02",
      "key": "avoid_experience_example",
      "section": "details",
      "prompt": "What tends to make a trip or group outing less enjoyable for you?",
      "input": "long_text",
      "max_length": 1000,
      "helper": "For example, rushed mornings, surprise costs, too much transit, or no time alone."
    },
    {
      "id": "F03",
      "key": "anything_else",
      "section": "details",
      "prompt": "What else would you like your agent to understand about you?",
      "input": "long_text",
      "max_length": 1000
    }
  ]
} as const;
