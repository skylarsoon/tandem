// Generated from contracts/ by scripts/sync-catalogs.py. Do not edit.

export const RULE_ATTRIBUTES = {
  "version": 1,
  "about": "What candidates of each category carry, named exactly as their facts (Candidate fields). Rules may only refer to these. Types: money (integer cents), int, number, bool, time (local HH:MM), epoch (seconds), text. shared_by_setting: for a shared pick, people without their own need for this attribute share it, that many to one (a room setting), so the pick must hold their explicit needs plus their share, rounded up.",
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
  "about": "How the named limits stored before rules existed become rules, needs and facts. Data, read by the module and the worker alike; the only key-specific knowledge left, kept for old rooms and the form flow. A SOFT limit becomes a soft rule (a preference, not enforced) unless enforce_soft; a flexible budget is firm at the top of its authorized range; a cleared value (set aside) makes nothing.",
  "keys": {
    "flight_budget_total": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "total_cents",
      "operator": "lte",
      "flexible_limit": "authorized_range.max"
    },
    "trip_budget_total": {
      "as": "rule",
      "aggregate": "my_cost",
      "operator": "lte",
      "flexible_limit": "authorized_range.max"
    },
    "baggage_required": {
      "as": "rule",
      "category": "FLIGHT",
      "attribute": "baggage_included",
      "operator": "eq",
      "only_when": true,
      "value": true,
      "enforce_soft": true
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
