// Generated from contracts/ by scripts/sync-catalogs.py. Do not edit.

export const RULE_ATTRIBUTES = {
  "version": 1,
  "about": "What candidates of each category carry, named exactly as their facts (Candidate fields). Rules may only refer to these. Types: money (integer cents), int, number, bool, time (local HH:MM), epoch (seconds), date (local YYYY-MM-DD), text. shared_by_setting: for a shared pick, people without their own need for this attribute share it, that many to one (a room setting), so the pick must hold their explicit needs plus their share, rounded up. trip_bound: the attribute must sit inside the trip's dates (room settings start_date/end_date) for every pick: by_end means on or before the end date (e.g. arriving), from_start on or after the start date (e.g. leaving to go home). search_date: a person's own firm rule on this attribute moves their search: outbound searches no earlier than their earliest allowed date, return no later than their latest.",
  "categories": {
    "FLIGHT": {
      "total_cents": {
        "type": "money",
        "label": "fare"
      },
      "arrival_local": {
        "type": "time",
        "label": "landing time"
      },
      "arrival_epoch": {
        "type": "epoch",
        "label": "landing"
      },
      "departure_epoch": {
        "type": "epoch",
        "label": "departure"
      },
      "stops": {
        "type": "int",
        "label": "connections"
      },
      "baggage_included": {
        "type": "bool",
        "label": "checked bag included"
      },
      "checked_bags_included": {
        "type": "int",
        "label": "checked bags included"
      },
      "departure_date": {
        "type": "date",
        "label": "departure date",
        "search_date": "outbound"
      },
      "arrival_date": {
        "type": "date",
        "label": "arrival date",
        "trip_bound": "by_end"
      },
      "duration_minutes": {
        "type": "int",
        "label": "travel time in minutes"
      },
      "return_departure_epoch": {
        "type": "epoch",
        "label": "return departure"
      },
      "return_arrival_epoch": {
        "type": "epoch",
        "label": "return landing"
      },
      "return_departure_date": {
        "type": "date",
        "label": "return date",
        "trip_bound": "from_start",
        "search_date": "return"
      },
      "return_arrival_date": {
        "type": "date",
        "label": "date back home",
        "search_date": "return"
      },
      "return_stops": {
        "type": "int",
        "label": "return connections"
      },
      "return_duration_minutes": {
        "type": "int",
        "label": "return travel time in minutes"
      }
    },
    "STAY": {
      "total_cents": {
        "type": "money",
        "label": "total price"
      },
      "capacity": {
        "type": "int",
        "label": "guests"
      },
      "bedrooms": {
        "type": "int",
        "label": "bedrooms",
        "shared_by_setting": "people_per_bedroom"
      },
      "rating": {
        "type": "number",
        "label": "rating",
        "range": [
          0,
          5
        ]
      },
      "free_cancellation": {
        "type": "bool",
        "label": "free cancellation"
      }
    },
    "TRANSFER": {
      "total_cents": {
        "type": "money",
        "label": "price"
      },
      "capacity": {
        "type": "int",
        "label": "seats"
      }
    },
    "ACTIVITY": {
      "total_cents": {
        "type": "money",
        "label": "price per person"
      },
      "rating": {
        "type": "number",
        "label": "rating",
        "range": [
          0,
          5
        ]
      }
    },
    "DINNER": {
      "total_cents": {
        "type": "money",
        "label": "price per person"
      },
      "rating": {
        "type": "number",
        "label": "rating",
        "range": [
          0,
          5
        ]
      }
    }
  },
  "aggregates": {
    "my_cost": {
      "type": "money",
      "label": "your share of the whole trip",
      "about": "Everything this person pays across the decisions: their own picks plus their share of split ones."
    }
  }
} as const;

export const LEGACY_KEYS = {
  "version": 1,
  "about": "How the named limits stored before rules existed become rules, needs and facts. Data, read by the module and the worker alike; the only key-specific knowledge left, kept for old rooms and the form flow. A SOFT limit becomes a soft rule (a preference, not enforced) unless enforce_soft; a SOFT need makes nothing binding. A flexible budget is a preference with partial credit up to the top of its authorized range (also firm there) or, with no range, up to soft_tolerance times itself. A cleared value (set aside) makes nothing.",
  "keys": {
    "flight_budget_total": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "total_cents",
      "operator": "lte",
      "flexible_limit": "authorized_range.max",
      "soft_tolerance": 2
    },
    "trip_budget_total": {
      "as": "rule",
      "aggregate": "my_cost",
      "operator": "lte",
      "flexible_limit": "authorized_range.max",
      "soft_tolerance": 2
    },
    "baggage_required": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "baggage_included",
      "operator": "eq",
      "only_when": true,
      "value": true
    },
    "arrival_window": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "arrival_local",
      "operator": "between",
      "value_from": [
        "earliest",
        "latest"
      ],
      "defaults": [
        "00:00",
        "23:59"
      ]
    },
    "max_stops": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "stops",
      "operator": "lte"
    },
    "stay_min_rating": {
      "as": "rule",
      "category": "STAY",
      "attribute": "rating",
      "operator": "gte"
    },
    "own_bedroom": {
      "as": "need",
      "category": "STAY",
      "attribute": "bedrooms",
      "only_when": true,
      "needs": 1
    },
    "origin_airport": {
      "as": "search_fact",
      "fact": "origin"
    },
    "no_budget_limit": {
      "as": "coverage",
      "topic": "money"
    },
    "dates_ok": {
      "as": "coverage",
      "topic": "dates"
    },
    "trip_note": {
      "as": "note"
    }
  },
  "kept_prefix": "kept_"
} as const;

export const DECISIONS = {
  "version": 1,
  "about": "How each kind of choice is made, as data. chooses: one_per_person (each traveller picks their own) or one_for_group. pays: each (your own pick), split (shared equally) or per_person (a per-head price). A room plans the categories its settings list; group rules limit the group's combined choice, with values from room settings. When the organizer gives stages (an order), each stage is decided on its own: scope is the kind of proposal it publishes and milestone the progress stage it moves. repeats_setting: a stage decided several times (one decision each), as many as that room setting says, or one per full day of the trip.",
  "categories": {
    "FLIGHT": {
      "chooses": "one_per_person",
      "pays": "each",
      "scope": "FLIGHT_BUNDLE",
      "milestone": "TRAVEL"
    },
    "STAY": {
      "chooses": "one_for_group",
      "pays": "split",
      "fits_everyone": "capacity",
      "scope": "SINGLE_SHARED_BOOKING",
      "milestone": "STAY_EXPERIENCES"
    },
    "TRANSFER": {
      "chooses": "one_for_group",
      "pays": "split",
      "fits_everyone": "capacity",
      "scope": "SINGLE_SHARED_BOOKING",
      "milestone": "TRAVEL"
    },
    "ACTIVITY": {
      "chooses": "one_for_group",
      "pays": "per_person",
      "scope": "ACTIVITY_DECISION",
      "milestone": "STAY_EXPERIENCES",
      "repeats_setting": "activities"
    },
    "DINNER": {
      "chooses": "one_for_group",
      "pays": "per_person",
      "scope": "ACTIVITY_DECISION",
      "milestone": "STAY_EXPERIENCES"
    }
  },
  "group_rules": [
    {
      "id": "arrive_together",
      "spread": "arrival_epoch",
      "across": "FLIGHT",
      "operator": "lte",
      "value_setting": "arrival_overlap_seconds",
      "default": 7200
    }
  ],
  "fairness": {
    "burden": "extra_over_cheapest",
    "full_at": 2.0,
    "about": "Each person's compromise in a plan is how much more they pay than the cheapest plan available to them, as a share of that cheapest cost: 0 when they pay their cheapest, 1 at full_at times it or more. The optimizer weighs the worst (with each person's history), the total, then cost."
  }
} as const;
